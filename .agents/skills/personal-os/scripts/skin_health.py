#!/usr/bin/env python3
"""Skin Health adapter for Personal OS. Dot normalizes; records.py is the sole writer.

No network, notifications, diagnosis, prescribing or automatic phase transitions.
Read operations do not create caches. All examples belong in isolated test roots.
"""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo
import records

VERSION = '1.0.0'
PHASES = {'irritation_recovery', 'treatment_reintroduction', 'maintenance'}
TYPES = {'profile', 'routine', 'treatment', 'symptom', 'decision'}
EMERGENCY = {'breathing_difficulty', 'tongue_throat_swelling'}
URGENT = {'severe_swelling', 'blisters', 'oozing', 'severe_burning', 'persistent_reaction'}
ASSESS = {'increasing_deep_nodules', 'new_scarring', 'persistent_pain', 'treatment_ineffective'}
FLAGS = EMERGENCY | URGENT | ASSESS
STABILITY = ('washing_stinging_absent', 'moisturizer_burning_absent', 'significant_peeling_resolved')
INITIAL_STATUS = '按本人报送的资料记录暂定状态，等待最新症状确认'


def digest(value):
    return hashlib.sha256(records._json(value).encode()).hexdigest()


def day(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('Date must be a string or null')
    if len(value) == 10:
        return date.fromisoformat(value)
    if not records._aware(value):
        raise ValueError('Timestamp must have timezone; month is not a daily date')
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(ZoneInfo('Asia/Shanghai')).date()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_event(event):
    require(isinstance(event, dict) and event.get('type') in TYPES, 'Unknown skin event type')
    require(event.get('category') in {'user_reported', 'ai_proposed'}, 'Only user_reported or ai_proposed input; system_derived is output only')
    require('event_at' in event, 'event_at required; unknown is null')
    day(event['event_at'])
    data = event.get('data')
    require(isinstance(data, dict), 'data must be an object')
    # No image payload/path/URL ingestion contract. Photo notes are text observations only.
    def no_images(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                require(key not in {'image', 'images', 'image_url', 'photo', 'photos', 'photo_path', 'image_base64', 'attachment'}, 'Original photos are not accepted by this adapter')
                no_images(value)
        elif isinstance(obj, list):
            for value in obj: no_images(value)
    no_images(data)
    kind = event['type']
    if kind == 'profile':
        require(data.get('as_of') is not None, 'Profile needs dated historical scope')
        day(data['as_of'])
        require(data.get('phase') in PHASES and data.get('phase_status') == 'provisional', 'Initial phase must be provisional')
    if kind in {'routine', 'decision'}:
        require(isinstance(data.get('version'), str) and bool(data['version']), 'Routine/decision version required')
        require(data.get('adoption') in {'proposed', 'confirmed'}, 'Explicit adoption state required')
        require(data.get('adoption') != 'confirmed' or event['category'] == 'user_reported', 'AI cannot confirm a plan')
        require(data.get('phase') in PHASES, 'Valid phase required')
        require(isinstance(data.get('reason'), str) and bool(data['reason']), 'Decision reason required')
        require(isinstance(data.get('basis_refs'), list), 'Decision basis_refs required')
        require(data.get('clinical_confirmation') in {'confirmed', 'not_needed_reported', 'pending', 'unknown'}, 'Clinical confirmation must remain explicit')
        if data['adoption'] == 'confirmed' and data['phase'] != 'irritation_recovery':
            require(data['clinical_confirmation'] in {'confirmed', 'not_needed_reported'}, 'Treatment phase needs reported clinical review context')
        started = day(data.get('started_at'))
        if started and event['event_at']:
            require(started <= day(event['event_at']), 'Actual treatment start cannot be after the decision effective date')
        require(isinstance(data.get('products', []), list), 'products must be a list')
    if kind == 'treatment':
        require(isinstance(data.get('product'), str) and bool(data['product']), 'Reported product required')
        require(data.get('action') in {'used', 'paused', 'resumed', 'not_used', 'unknown'}, 'Explicit treatment action required')
        require(data.get('period') in {'morning', 'evening', 'other', 'unknown'}, 'Period required; unknown allowed')
        require(data.get('routine_version') is None or isinstance(data['routine_version'], str), 'routine_version must be known string or null')
        require(data.get('concentration') is None or isinstance(data['concentration'], str), 'Concentration must retain label text or null')
    if kind == 'symptom':
        for key in ('stinging', 'peeling'):
            require(key in data, key + ' required; unknown is null')
            require(data[key] is None or (type(data[key]) is int and 0 <= data[key] <= 3), 'Self rating must be explicit integer 0–3 or null')
        for key in STABILITY:
            require(data.get(key) is None or type(data[key]) is bool, 'Stability observations must be explicit boolean or null')
        require(isinstance(data.get('red_flags', []), list) and set(data.get('red_flags', [])) <= FLAGS, 'Unknown red flag; retain in other_abnormalities and review with dot')
        require(data.get('scope') in {'day', 'moment', 'unknown'}, 'Symptom scope required')
        require(data.get('new_lesions') is None or isinstance(data['new_lesions'], list), 'new_lesions must be list or null')
        for lesion in data.get('new_lesions') or []:
            require(isinstance(lesion, dict) and lesion.get('type') in {'closed_comedone', 'inflammatory_papule', 'pustule', 'deep_lump', 'unknown'}, 'Invalid lesion type')
            require(lesion.get('count') is None or (type(lesion['count']) is int and lesion['count'] >= 0), 'Lesion count must be explicit integer or null')


def read_events(root, inactive=False):
    snapshot = records._snapshot(records._root(root))
    errors = records._validate_snapshot(snapshot)
    require(not errors, '\n'.join(errors))
    result = []
    for path, (meta, body) in snapshot.items():
        if 'skin_event' in meta and (inactive or meta.get('status') == 'active'):
            result.append({'id': meta['id'], 'path': path, 'meta': meta, 'event': meta['skin_event'], 'raw_text': body})
    return sorted(result, key=lambda e: (e['meta']['recorded_at'], e['id']))


def _change(root, path, meta, body):
    return {'path': path, 'metadata': meta, 'body': body,
            'expected_sha256': records._hash((root/path).read_bytes()) if (root/path).exists() else None}


def _summary_changes(root, events):
    facts = [e for e in events if e['meta']['status'] == 'active' and e['event']['category'] == 'user_reported' and not e['event'].get('duplicate_of')]
    result = []
    for identifier, kinds, title in [('skin-profile', {'profile'}, 'Skin Profile：历史自述，非诊断'),
                                     ('skincare-routine', {'profile', 'routine', 'decision'}, 'Skincare Routine：确认方案与历史使用分开')]:
        selected = [e for e in facts if e['event']['type'] in kinds]
        if not selected: continue
        refs = [e['id'] for e in selected]
        meta = {'schema_version': 1, 'id': identifier, 'document_type': 'summary', 'kind': 'current', 'record_refs': refs}
        body = '# '+title+'\n\n来源日期与历史范围见以下证据。最新状态调用 skin_health.py state；本概况不是用药执行记录。\n'
        for e in selected:
            body += '\n来源 '+e['id']+'\n```json\n'+json.dumps(e['event']['data'], ensure_ascii=False, indent=2)+'\n```\n'
        result.append(_change(root, f'data/current/{identifier}.md', meta, body))
    history = [e for e in events if e['meta']['status']=='active' and not e['event'].get('duplicate_of')]
    decision_refs = [e['id'] for e in history]
    if decision_refs:
        meta = {'schema_version': 1, 'id': 'skin-health-history', 'document_type': 'case', 'kind': 'state_change', 'status': 'observing',
                'record_refs': decision_refs, 'user_hypothesis_refs': [], 'ai_hypothesis_refs': [], 'outcome_refs': []}
        body = '# Skin Health Decision History\n\n按来源事件追踪方案版本、当时理由、依据、实际使用和症状。建议不等于采纳，时间相关不等于因果。\n\n'
        body += '\n'.join('- '+e['id']+': '+e['event']['category']+' / '+e['event']['type']+'；'+str(e['event']['event_at'])+'；'+str(e['event']['data'].get('reason', '结果/背景见原始记录')) for e in history)
        result.append(_change(root, 'data/cases/skin-health-history.md', meta, body))
    return result


def ingest(root, envelope):
    root = records._root(root)
    source = deepcopy(envelope.get('source', {}))
    raw = envelope.get('raw_text')
    require(records._nonempty(source.get('ref')) and records._nonempty(raw), 'Actual source.ref and original raw_text required')
    require(source.get('message_at') is None or records._aware(source['message_at']), 'Unknown source time must be null')
    if source.get('message_date') is not None:
        require(isinstance(source['message_date'], str) and len(source['message_date'])==10, 'message_date must preserve date-only precision')
        day(source['message_date'])
    source.setdefault('message_at', None)
    source.setdefault('excerpt', raw[:240])
    event = deepcopy(envelope.get('event'))
    validate_event(event)
    identifier = 'skin-'+digest([source['ref'], envelope.get('part', 'main')])[:24]
    fingerprint = digest(envelope)
    journal = records._journal_path(root, identifier)
    if journal.exists():
        saved = json.loads(journal.read_text())
        original = next(c for c in saved['bundle']['changes'] if c['metadata']['id'] == identifier)
        require(original['metadata']['skin_event']['input_hash'] == fingerprint, 'Same source changed; use a new sourced correction')
        return records.apply_bundle(root, saved['bundle'], identifier)
    events = read_events(root, inactive=True)
    by_id = {e['id']: e for e in events}
    require(identifier not in by_id, 'Source already exists without journal; inspect before replay')
    event.update(schema_version=1, input_hash=fingerprint)
    related = list(event['data'].get('basis_refs', []))
    changes = []
    require(not (envelope.get('corrects') and envelope.get('duplicate_of')), 'Choose correction or explicit duplicate')
    for relationship in ('corrects', 'duplicate_of'):
        target_id = envelope.get(relationship)
        if not target_id: continue
        old = by_id.get(target_id)
        require(old is not None and old['meta']['status'] == 'active' and not old['event'].get('duplicate_of'), 'Target must be an active original skin event')
        require(old['event']['type'] == event['type'] and old['event']['category'] == event['category'], 'Relationship must preserve type and evidence category')
        if relationship == 'duplicate_of':
            require(old['event']['event_at'] == event['event_at'] and old['event']['data'] == event['data'], 'Duplicate must match confirmed original; supplements are separate reports')
        else:
            old['meta']['status'] = 'corrected'
            changes.append(_change(root, old['path'], old['meta'], old['raw_text']))
        event[relationship] = target_id
        related.append(target_id)
    if event['type'] == 'profile':
        require(envelope.get('corrects') or not any(e['meta']['status']=='active' and e['event']['type']=='profile' for e in events), 'Existing profile needs sourced correction, not reset')
    if event['type'] in {'routine', 'decision'} and event['data']['adoption'] == 'confirmed':
        require(not any(e['meta']['status']=='active' and e['event']['type'] in {'routine','decision'} and e['event']['data']['adoption']=='confirmed' and e['event']['data']['version']==event['data']['version'] for e in events), 'Use a new routine version for a new decision')
    occurred = event['event_at']
    month = str(day(source['message_at']) or day(occurred) or day(records._now()))[:7]
    is_ai = event['category'] == 'ai_proposed'
    meta = {'schema_version': 1, 'id': identifier, 'document_type': 'record',
            'kind': 'correction' if envelope.get('corrects') else ('intention' if is_ai else ('decision' if event['type'] in {'routine','decision'} else 'observation')),
            'author': 'ai' if is_ai else 'user', 'basis': 'ai_inference' if is_ai else 'user_statement',
            'domains': ['wellbeing_health'], 'status': 'active', 'source': source,
            'time': {'event': occurred, 'expressed': source['message_at'] or source.get('message_date'), 'target': None,
                     'precision': 'instant' if records._aware(occurred) else ('day' if occurred else 'unknown'),
                     'note': 'Profile为历史自述快照，不生成当天治疗或症状事件。' if event['type']=='profile' else None},
            'related': list(dict.fromkeys(related)), 'skin_event': event}
    path = f'data/records/{month}/{identifier}.md'
    changes.append(_change(root, path, meta, raw))
    events.append({'id': identifier, 'path': path, 'meta': meta, 'event': event, 'raw_text': raw})
    # Rebuild source-linked summaries in the SAME CAS bundle as corrections.
    changes += _summary_changes(root, events)
    return records.apply_bundle(root, {'changes': changes}, identifier)


def facts_at(root, at):
    end = day(at)
    return [e for e in read_events(root) if e['event']['category']=='user_reported' and not e['event'].get('duplicate_of')
            and (e['event']['event_at'] is None or day(e['event']['event_at']) <= end)]


def alerts(events):
    result = []
    for e in events:
        if e['event']['type'] != 'symptom': continue
        flags = set(e['event']['data'].get('red_flags', []))
        level = 'emergency' if flags & EMERGENCY else ('prompt_medical_review' if flags & URGENT else ('dermatology_assessment' if flags & ASSESS else None))
        if level:
            result.append({'level': level, 'flags': sorted(flags), 'source_id': e['id'], 'event_at': e['event']['event_at'], 'resolution': 'unknown; review the report context',
                           'action': '立即寻求急救' if level=='emergency' else '及时就医评估；不要等待评分或周复盘'})
    return result


def state(root, at=None):
    at = at or records._now()
    events = facts_at(root, at)
    profiles = [e for e in events if e['event']['type']=='profile']
    choices = [e for e in events if e['event']['type'] in {'routine','decision'} and e['event']['data']['adoption']=='confirmed']
    # Unknown effective date cannot silently supersede a dated current plan.
    dated = sorted([e for e in choices if e['event']['event_at']], key=lambda e:(day(e['event']['event_at']), e['meta']['recorded_at']))
    choice = dated[-1] if dated else None
    phase = choice['event']['data']['phase'] if choice else (profiles[-1]['event']['data']['phase'] if profiles else None)
    symptoms = [e for e in events if e['event']['type']=='symptom']
    by_day = {}
    for e in symptoms:
        if e['event']['event_at']:
            by_day.setdefault(day(e['event']['event_at']), []).append(e)
    stable = 0
    for offset in range(3):
        rows = by_day.get(day(at)-timedelta(days=offset), [])
        daily = [e for e in rows if e['event']['data']['scope']=='day']
        sufficient = any(all(e['event']['data'].get(k) is True for k in STABILITY) for e in daily)
        contradicts = any(e['event']['data'].get('red_flags') or any(e['event']['data'].get(k) is False for k in STABILITY)
                          or (e['event']['data'].get('stinging') or 0)>=2 or (e['event']['data'].get('peeling') or 0)>=2 for e in rows)
        if not sufficient or contradicts: break
        stable += 1
    recent = [e for e in symptoms if e['event']['event_at'] and 0 <= (day(at)-day(e['event']['event_at'])).days <= 2]
    irritation = any((e['event']['data'].get('stinging') or 0)>=2 or (e['event']['data'].get('peeling') or 0)>=2
                     or any(e['event']['data'].get(k) is False for k in STABILITY) for e in recent)
    adequate = any(all(e['event']['data'].get(k) is not None for k in ('stinging', 'peeling'))
                   or all(e['event']['data'].get(k) is not None for k in STABILITY) for e in recent)
    routine = choice['event']['data'] if choice else None
    latest_report = max((day(e['event']['event_at']) for e in symptoms if e['event']['event_at']), default=None)
    return {'category':'system_derived', 'module_version':VERSION, 'as_of':at, 'phase':phase,
            'phase_status':'confirmed' if choice else (('provisional_pending_confirmation' if latest_report else 'provisional_pending_latest_report') if profiles else 'unknown'),
            'status_text':INITIAL_STATUS if profiles and not choice and not latest_report else '以有来源的方案决定与实际报告为准；不是诊断',
            'latest_symptom_report_date':str(latest_report) if latest_report else None,
            'routine':routine, 'routine_source_id':choice['id'] if choice else None,
            'undated_decisions_need_review':[e['id'] for e in choices if not e['event']['event_at']],
            'consecutive_fully_reported_stable_days':stable,
            'reintroduction_discussion_candidate':stable>=2, 'automatic_transition':False,
            'irritation_review_needed':True if irritation else (False if adequate else None), 'red_flags':alerts(symptoms),
            'red_flag_scope':'已明确报告的信号；空列表不证明没有异常',
            'treatment_start':routine.get('started_at') if routine else None,
            'source_event_ids':[e['id'] for e in events],
            'information_checks':['结合最新报告核对当前症状','按原始产品/方案证据核对药物浓度与地区版本，不能推测']}


def _period(events, start, end):
    rows = [e for e in events if e['event']['event_at'] and start<=day(e['event']['event_at'])<=end]
    symptoms = [e for e in rows if e['event']['type']=='symptom']
    treatments = [e for e in rows if e['event']['type']=='treatment']
    daily = {key:{} for key in ('stinging','peeling')}
    counts = Counter()
    for e in symptoms:
        d = day(e['event']['event_at']); data=e['event']['data']
        for key in daily:
            if data[key] is not None: daily[key].setdefault(str(d), []).append(data[key])
        for lesion in data.get('new_lesions') or []:
            if lesion['count'] is not None: counts[lesion['type']] += lesion['count']
    scores = {}
    for key, days in daily.items():
        # Worst reported score per day avoids weighting days with many messages more heavily.
        values = [max(v) for v in days.values()]
        scores[key]={'known_days':len(values), 'daily_reported_max':{d:max(v) for d,v in days.items()},
                     'mean_daily_reported_max':sum(values)/len(values) if values else None}
    use = Counter(e['event']['data']['product'] for e in treatments if e['event']['data']['action']=='used')
    exposures = []
    for d in sorted({day(e['event']['event_at']) for e in symptoms}):
        symptom_refs = [e['id'] for e in symptoms if day(e['event']['event_at'])==d and ((e['event']['data']['stinging'] or 0)>0 or (e['event']['data']['peeling'] or 0)>0)]
        used = [e for e in treatments if day(e['event']['event_at'])==d and e['event']['data']['action']=='used']
        if symptom_refs and used:
            exposures.append({'date':str(d),'products':[e['event']['data']['product'] for e in used], 'source_ids':symptom_refs+[e['id'] for e in used], 'interpretation':'同日共现，不能判定先后或因果；SA洁面也计入暴露'})
    return {'source_ids':[e['id'] for e in rows], 'reported_days':len({day(e['event']['event_at']) for e in rows if e['event']['type'] in {'treatment','symptom'}}),
            'symptoms':scores, 'reported_uses':dict(use), 'reported_new_lesion_counts':dict(counts),
            'treatment_events':[{'id':e['id'], 'date':e['event']['event_at'], **e['event']['data']} for e in treatments],
            'possible_exposure_patterns':exposures}


def review(root, start=None, end=None, at=None):
    at = at or records._now(); last=day(end or at); first=day(start) if start else last-timedelta(days=6)
    require(first<=last and last<=day(at), 'Review range must be ordered and not future')
    events=facts_at(root, at); length=(last-first).days+1
    current=_period(events, first, last); previous=_period(events, first-timedelta(days=length), first-timedelta(days=1))
    trend={}
    for key in ('stinging','peeling'):
        a=current['symptoms'][key]; b=previous['symptoms'][key]
        # Operational coverage rule, not a clinical threshold. No inference on missing days.
        enough=length>=7 and a['known_days']>=5 and b['known_days']>=5
        delta=a['mean_daily_reported_max']-b['mean_daily_reported_max'] if enough else None
        trend[key]={'status':'reported_sample_comparison' if enough else 'insufficient_data', 'delta':delta}
    return {'category':'system_derived','module_version':VERSION,'start':str(first),'end':str(last),
            'coverage':{'reported_days':current['reported_days'],'calendar_days':length}, **current,
            'symptom_comparison':trend, 'acne_trend':'描述报告样本；未核实逐日完整计数，不据片段计算总体改善率',
            'unknown_date_source_ids':[e['id'] for e in events if e['event']['event_at'] is None and e['event']['type'] in {'treatment','symptom'}],
            'red_flags':alerts([e for e in events if e['id'] in current['source_ids']]),
            'next_week':'有明显刺激/红旗先复核；其余根据覆盖情况讨论维持或调整，不自动改变方案',
            'limitations':['报告使用N次不等于只使用N次；未报告不算漏用','未知评分不进入均值分母','日最大值及5/7覆盖为管理规则，非医学量表或疗效阈值','同日共现不证明药物致因','方案版本、开始时间、暂停与医生复核需共同解释6–8周/12周参考期']}


def daily(root, at=None):
    at=at or records._now(); events=facts_at(root, at)
    require(records._aware(at), 'Daily check requires an aware assessment timestamp')
    today=[e for e in events if e['event']['event_at'] and day(e['event']['event_at'])==day(at) and e['event']['type'] in {'treatment','symptom'}]
    # Existing followups own contact history; do not create another reminder ledger.
    import followups
    pending=followups.due(root, at=at)
    accepted=[]
    for path in (Path(root)/'operations/runs').glob('*.json'):
        run=json.loads(path.read_text())
        check=run.get('skin_health', {})
        if (check.get('local_date')==str(day(at)) and check.get('send_status')=='accepted'
                and check.get('delivery_ref') and records._aware(check.get('accepted_at'))
                and day(check['accepted_at'])==day(at)
                and datetime.fromisoformat(check['accepted_at'].replace('Z','+00:00'))<=datetime.fromisoformat(at.replace('Z','+00:00'))):
            accepted.append(check['delivery_ref'])
    awaiting=[]; paused=[]
    for path in (Path(root)/'data/followups').glob('*.md'):
        meta,_=records.read_document(path)
        pause_active = meta.get('status')=='paused' and (not meta.get('next_check_at') or
                       datetime.fromisoformat(meta['next_check_at'])>datetime.fromisoformat(at.replace('Z','+00:00')))
        if meta.get('case_id')=='skin-health-history' and (pause_active or
                (meta.get('status')=='closed' and any(h.get('action')=='reply' and h.get('answer')=='stop' for h in meta.get('history', [])))):
            paused.append(meta['id'])
        if (meta.get('case_id')=='skin-health-history' and meta.get('contact_state')=='awaiting_reply'
                and meta.get('status')=='active' and meta.get('next_check_at')
                and datetime.fromisoformat(meta['next_check_at'])>datetime.fromisoformat(at.replace('Z','+00:00'))):
            awaiting.append(meta['id'])
    ask=not bool(today or accepted or awaiting or paused)
    return {'category':'system_derived', 'state':state(root,at), 'reported_today':bool(today),
            'ask_routine_checkin':ask, 'checkin':'今天实际用了什么，是否有异常刺激？' if ask else None,
            'accepted_checkin_refs':accepted, 'awaiting_followup_ids':awaiting, 'paused_or_stopped_followup_ids':paused,
            'followups':pending, 'send':False,
            'instruction':'只在既有晚间窗口按reflection.md查近期聊天、已答和accepted回执后合并重要缺口；本结果不是发送授权或已发送回执。红旗优先即时回应。'}


def dispatch(root, request):
    intent=request.get('intent')
    if intent in {'record','correct'}:
        if request.get('envelope'): return ingest(root,request['envelope'])
        return {'status':'needs_normalization','saved':False,'raw_text':request.get('text'), 'instruction':'dot保留原文与真实来源，按skin-health.md逐项规范化；不猜分数/日期/已执行。'}
    if intent=='state': return state(root,request.get('at'))
    if intent=='review': return review(root,request.get('start'),request.get('end'),request.get('at'))
    if intent=='daily': return daily(root,request.get('at'))
    raise ValueError('Explicit skin intent required')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--root',required=True,type=Path)
    sub=parser.add_subparsers(dest='command',required=True)
    for cmd in ('ingest','dispatch','state','daily','review','validate'):
        p=sub.add_parser(cmd)
        if cmd in {'ingest','dispatch'}: p.add_argument('--input',type=Path,required=True)
        if cmd in {'state','daily','review'}: p.add_argument('--at')
        if cmd=='review': p.add_argument('--start'); p.add_argument('--end')
    args=parser.parse_args(argv)
    try:
        if args.command in {'ingest','dispatch'}: result=globals()[args.command](args.root,json.loads(args.input.read_text()))
        elif args.command=='review': result=review(args.root,args.start,args.end,args.at)
        elif args.command=='validate': result={'status':'valid','skin_events':len(read_events(args.root,True))}
        else: result=globals()[args.command](args.root,args.at)
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False));return 0
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps({'status':'error','error':str(exc)},ensure_ascii=False),file=sys.stderr);return 1

if __name__=='__main__': raise SystemExit(main())
