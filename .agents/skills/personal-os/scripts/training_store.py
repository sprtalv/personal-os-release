"""Training facts are Markdown records written through the existing records journal.

JSON in generated/training is disposable. No network or background tasks here.
Natural language interpretation remains with dot; this module validates its envelope.
"""
from __future__ import annotations
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
from zoneinfo import ZoneInfo
import records

TYPES = {'profile','session','equipment','context','measurement','food','habit','symptom','question','policy','tombstone'}
CATEGORIES = {'user_reported','confirmed_policy','system_observed','ai_proposed','ai_hypothesis'}
STATUSES = {'planned','in_progress','partial','completed','skipped','rescheduled','unknown'}
SET_TYPES = {'warmup','work','technique','test'}


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':'))


def digest(obj):
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def now():
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()


def day(value):
    if value is None:
        return None
    if len(value) == 10:
        return date.fromisoformat(value)
    return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(ZoneInfo('Asia/Shanghai')).date()


def temporal(value):
    return value is None or (isinstance(value,str) and records._temporal(value) and len(value) != 7)


def number(value, lower=0):
    return type(value) in (int,float) and value >= lower and value < float('inf')


def bar_total_kg(components):
    """Only compute when all original components are explicitly reported; preserve them."""
    if not all(number(components.get(k)) for k in ('bar_kg','plate_lb_per_side','sides')):
        raise ValueError('Bar/plate components incomplete; keep load null')
    if type(components['sides']) is not int or components['sides']<1:
        raise ValueError('Reported sides must be a positive integer')
    return float(Decimal(str(components['bar_kg']))+Decimal(str(components['plate_lb_per_side']))*components['sides']*Decimal('0.45359237'))


@contextmanager
def lock(root):
    import fcntl
    path = records._checked_path(records._root(root), 'operations/training.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def validate_event(event):
    if not isinstance(event,dict) or event.get('type') not in TYPES - {'tombstone'}:
        raise ValueError('Unknown training event type')
    if event.get('category') not in CATEGORIES:
        raise ValueError('category required: user_reported / confirmed_policy / ai_proposed / ai_hypothesis')
    if not temporal(event.get('event_at')):
        raise ValueError('event_at must preserve a date, aware timestamp or null')
    if 'event_at' not in event or not isinstance(event.get('data'),dict):
        raise ValueError('event_at and data required')
    data=event['data']; kind=event['type']
    if event['category']=='system_observed' and kind!='question':
        raise ValueError('System observed receipt is only valid for question history')
    if kind in {'session','measurement','food','context','symptom','equipment'} and event['category'] != 'user_reported':
        # Suggestions may be retained, but must never enter the actual evidence reducer.
        if event['category'] not in {'ai_proposed','ai_hypothesis'}:
            raise ValueError('Actual event must be user_reported')
    if kind=='session':
        if not isinstance(data.get('session_id'),str) or not data['session_id']:
            raise ValueError('session_id required; link supplements to the actual session')
        if data.get('status','unknown') not in STATUSES or data.get('template') not in {None,'A','B','C','S'}:
            raise ValueError('Invalid session status/template')
        for field in ('actual_training','recovery_ok','acute_adjustment','template_completion_confirmed'):
            if data.get(field) is not None and type(data[field]) is not bool:
                raise ValueError('Session evidence flags must be booleans or null')
        for item in data.get('items',[]):
            if not item.get('item_id') or not item.get('role') or item.get('kind','resistance') not in {'resistance','isometric','field'}:
                raise ValueError('item_id, role and valid kind required')
            if item.get('kind')=='field':
                for field in ('attempts','distance_m','runs'):
                    if item.get(field) is not None and not number(item[field]):
                        raise ValueError('Field quantities must be nonnegative reported values')
            scheme=item.get('scheme')
            if scheme is not None:
                if type(scheme.get('sets')) is not int or scheme['sets']<1:
                    raise ValueError('Scheme sets must be a positive integer')
                for key in ('reps','seconds','rir'):
                    v=scheme.get(key)
                    if v is not None and (not isinstance(v,list) or len(v)!=2 or not all(number(x) for x in v) or v[0]>v[1]):
                        raise ValueError('Scheme ranges must be ordered nonnegative pairs')
            ids=[]
            for group in item.get('sets',[]):
                if not group.get('set_id') or group.get('type') not in SET_TYPES:
                    raise ValueError('Each actual set needs stable set_id and type')
                ids.append(group['set_id'])
                for key in ('reps','seconds','rir','rpe'):
                    if group.get(key) is not None and not number(group[key]):
                        raise ValueError('Invalid nonnegative set quantity')
                if group.get('reps') is not None and type(group['reps']) is not int:
                    raise ValueError('Repetitions must be an integer')
                if group.get('reps_range') is not None:
                    bounds=group['reps_range']
                    if group.get('reps') is not None or not isinstance(bounds,list) or len(bounds)!=2 or not all(type(x) is int and x>=0 for x in bounds) or bounds[0]>bounds[1]:
                        raise ValueError('Approximate repetitions need ordered integer reps_range and reps=null')
                if item.get('kind')=='isometric' and group.get('reps') is not None:
                    raise ValueError('Isometric duration belongs in seconds, not reps')
                if group.get('rir') is not None and group.get('rir_scope','set') != 'set':
                    raise ValueError('A group RIR must have set scope')
                if group.get('rpe') is not None and group.get('rpe_scale')!='rir_based_set':
                    raise ValueError('Explicit group RPE scale required; session fatigue is separate')
                if group.get('rpe') is not None and not 1 <= group['rpe'] <= 10:
                    raise ValueError('Group RPE outside 1–10')
                load=group.get('load')
                if load is not None:
                    if not isinstance(load,dict) or not number(load.get('value')):
                        raise ValueError('load needs a nonnegative value; unit/basis may remain null')
                    if load.get('unit') not in {None,'kg','lb','display'}:
                        raise ValueError('Unsupported unit; machine scale is display, not kg')
                    if load.get('basis') not in {None,'bar_total','plates_only','per_dumbbell','pair_total','machine','assistance','added','bodyweight'}:
                        raise ValueError('Unknown load basis')
                    components=load.get('original_components')
                    if components is not None and all(k in components for k in ('bar_kg','plate_lb_per_side','sides')):
                        computed=bar_total_kg(components)
                        if load.get('unit')!='kg' or load.get('basis')!='bar_total' or abs(load['value']-computed)>1e-8:
                            raise ValueError('Derived total does not match the preserved original components')
                if group.get('side') not in {None,'left','right','bilateral'}:
                    raise ValueError('Invalid side')
            if len(ids)!=len(set(ids)):
                raise ValueError('Duplicate set_id within an item')
            for key in ('technique_ok','no_pain','no_grinding'):
                if item.get(key) is not None and type(item[key]) is not bool:
                    raise ValueError('Quality fields must be explicit boolean or null')
            if any(item.get(k) is not None for k in ('technique_ok','no_pain','no_grinding')) and not item.get('quality_source'):
                raise ValueError('Quality evidence needs its report source')
            if item.get('rir_lower_bound') is not None:
                if not number(item['rir_lower_bound']) or item.get('rir_scope')!='exercise_minimum':
                    raise ValueError('Overall RIR lower bound needs exercise_minimum scope')
        ids=[i['item_id'] for i in data.get('items',[])]
        if len(ids)!=len(set(ids)):
            raise ValueError('Duplicate item_id')
    if kind=='equipment':
        for field in ('role','variant','equipment_id'):
            if not data.get(field): raise ValueError('Equipment needs '+field)
        levels=data.get('available_loads')
        if levels is not None and (not isinstance(levels,list) or not all(number(x) for x in levels)):
            raise ValueError('available_loads must be actual nonnegative available levels')
    if kind=='measurement':
        if not data.get('metric') or not number(data.get('value')) or not data.get('unit'):
            raise ValueError('A measurement is a single actual value with unit; a reported range is profile context')
    if kind=='symptom' and data.get('domain') not in {None,'all','upper_push','upper_pull','lower_strength','lower_control','field','core'}:
        raise ValueError('Unknown symptom domain must remain null, not evade safety checks')
    if kind=='question':
        if data.get('action') not in {'asked','answered','no_result','declined','paused','stopped'} or not data.get('key'):
            raise ValueError('Question needs key and action')
        if data['action']=='asked':
            accepted=(data.get('send_status')=='accepted' and records._aware(data.get('accepted_at')))
            delivered=records._aware(data.get('delivered_at'))
            if not data.get('delivery_ref') or not (accepted or delivered):
                raise ValueError('Asked requires a real accepted send or delivery receipt and timestamp')
            if accepted and event['category']!='system_observed':
                raise ValueError('Accepted send receipt must be system_observed')
        if data.get('observed_at') is not None and not records._aware(data['observed_at']):
            raise ValueError('Observation timestamp must be timezone-aware')
    if kind=='policy' and event['category']!='confirmed_policy':
        raise ValueError('Policy activation requires explicit confirmed_policy evidence')
    canonical(event)  # rejects NaN/Infinity anywhere


def read_events(root, include_inactive=False):
    root=records._root(root)
    with records._lock(root):
        records._require_recovered(root)
        docs=records._snapshot(root)
    result=[]
    for path,(meta,body) in docs.items():
        event=meta.get('training_event')
        if event is not None and (include_inactive or meta.get('status')=='active'):
            result.append(dict(event=deepcopy(event),id=meta['id'],path=path,source=meta.get('source'),raw=body,meta=meta))
    return sorted(result,key=lambda e:(e['event'].get('event_at') or '',e['meta']['recorded_at'],e['id']))


def _change(root, identifier, event, source, raw, related, status='active', existing=None):
    month=(source.get('message_at') or event.get('event_at') or now())[:7]
    path=existing['path'] if existing else f'data/records/{month}/{identifier}.md'
    kind='decision' if event['type'] in {'profile','policy','equipment'} else 'outcome'
    if event.get('corrects'): kind='correction'
    author='external' if event['category']=='system_observed' else ('ai' if event['category'] in {'ai_proposed','ai_hypothesis'} else 'user')
    if author=='ai': kind='observation'
    meta=dict(schema_version=1,id=identifier,document_type='record',kind=kind,author=author,
              basis='tool_observation' if author=='external' else ('ai_inference' if author=='ai' else 'user_statement'),domains=['wellbeing_health','goals_preferences'],
              status=status,source=source,time={'event':event.get('event_at'),'expressed':source.get('message_at'),
              'target':None,'precision':'unknown' if event.get('event_at') is None else ('day' if len(event['event_at'])==10 else 'instant'),
              'note':'训练事件与报告时间分开；未提供字段保持未知。'},related=related,training_event=event)
    return dict(path=path,metadata=meta,body=raw,expected_sha256=records._hash((root/path).read_bytes()) if existing else None)


def ingest(root, envelope):
    """Dot supplies original text, source and checked normalization. Idempotent by message+part.

    Same session/item/set IDs merge supplements; conflicting values require explicit correction.
    A correction replaces one event, preserving its source and actual historical session identity.
    """
    root=records._root(root)
    source=deepcopy(envelope.get('source',{})); raw=envelope.get('raw_text')
    if not source.get('ref') or not isinstance(raw,str) or not raw.strip():
        raise ValueError('Actual source.ref and raw_text are required')
    if source.get('message_at') is not None and not records._aware(source['message_at']):
        raise ValueError('Unknown message time is null; known time must include timezone')
    source.setdefault('message_at',None);source.setdefault('excerpt',raw[:240])
    event=deepcopy(envelope.get('event'));validate_event(event)
    identifier='training-'+digest([source['ref'],envelope.get('part','main')])[:24]
    event['input_hash']=digest(envelope)
    event['schema_version']=1
    with lock(root):
        journal=records._journal_path(root,identifier)
        if journal.exists():
            saved=json.loads(journal.read_text())
            if saved.get('state')=='prepared':
                bundle=saved['bundle']
                candidate=next((c for c in bundle['changes'] if c['metadata']['id']==identifier),None)
                if candidate is None or candidate['metadata']['training_event'].get('input_hash')!=event['input_hash']:
                    raise ValueError('Interrupted source has different input; recover original envelope first')
                receipt=records.apply_bundle(root,bundle,identifier)
                invalidate_cache(root,identifier)
                return {'status':receipt['status'],'id':identifier,'operation_id':identifier,'receipt':receipt,'recovered':True}
        existing=read_events(root,True)
        found=next((e for e in existing if e['id']==identifier),None)
        if found:
            if found['event'].get('type')=='tombstone':
                return {'status':'deleted','id':identifier,'idempotent_replay':True}
            if found['event'].get('input_hash')!=event['input_hash']:
                raise ValueError('Same source changed; supply a new correction message')
            return {'status':'local_saved','id':identifier,'idempotent_replay':True,'operation_id':identifier}
        corrects=envelope.get('corrects'); changes=[]; related=[]
        if corrects:
            old=next((e for e in existing if e['id']==corrects and e['meta']['status']=='active'),None)
            if old is None or old['event']['type']=='tombstone':
                raise ValueError('Correction target must be an active event')
            if old['event']['type'] != event['type']:
                raise ValueError('Correction must keep event type')
            if event['type']=='session' and old['event']['data']['session_id'] != event['data']['session_id']:
                raise ValueError('Correction cannot invent another actual session')
            event['corrects']=corrects;related=[corrects]
            old_meta=deepcopy(old['meta']);old_meta['status']='corrected'
            changes.append(dict(path=old['path'],metadata=old_meta,body=old['raw'],expected_sha256=records._hash((root/old['path']).read_bytes())))
        if event['type']=='session':
            if not corrects:
                duplicate=next((e for e in existing if e['meta']['status']=='active' and e['event']['type']=='session'
                                and e['event'].get('event_at')==event.get('event_at')
                                and e['event']['data']==event['data'] and not e['event'].get('duplicate_of')),None)
                if duplicate:
                    event['duplicate_of']=duplicate['id'];related=[duplicate['id']]
            # Preview reduction before any write, rejecting silent overwrites in supplements.
            active=[e for e in existing if e['meta']['status']=='active' and e['id']!=corrects]
            merge_sessions(active+[{'id':identifier,'event':event,'source':source}])
        if event['type']=='profile' and any(e['event']['type']=='profile' and e['meta']['status']=='active' for e in existing) and not corrects:
            raise ValueError('Profile already exists; update only with a sourced correction/policy, never reimport over newer facts')
        changes.append(_change(root,identifier,event,source,raw,related))
        receipt=records.apply_bundle(root,{'changes':changes},identifier)
        invalidate_cache(root, identifier)
        return {'status':receipt['status'],'id':identifier,'operation_id':identifier,'receipt':receipt}


def merge_sessions(events):
    sessions={}
    for wrapped in events:
        e=wrapped['event']
        if e['type']!='session' or e['category']!='user_reported' or e.get('duplicate_of'):continue
        d=e['data'];sid=d['session_id']
        s=sessions.setdefault(sid,{'session_id':sid,'event_at':e.get('event_at'),'status':'unknown','template':None,'items':[], 'source_event_ids':[]})
        if s['event_at'] is not None and e.get('event_at') is not None and day(s['event_at'])!=day(e['event_at']):
            raise ValueError('Same session_id on different dates needs explicit correction')
        if s['template'] is not None and d.get('template') is not None and s['template']!=d['template']:
            raise ValueError('Session template conflict')
        if s['event_at'] is None:s['event_at']=e.get('event_at')
        for key,value in d.items():
            if key not in {'items','session_id'}:s[key]=deepcopy(value)
        s['source_event_ids'].append(wrapped['id'])
        byid={i['item_id']:i for i in s['items']}
        for item in d.get('items',[]):
            if item['item_id'] not in byid:
                if any(i['role']==item['role'] for i in s['items']):
                    raise ValueError('Same session role must share item_id')
                s['items'].append(deepcopy(item));byid[item['item_id']]=s['items'][-1];continue
            dest=byid[item['item_id']]
            for key,value in item.items():
                if key=='sets':continue
                if key in dest and dest[key] is not None and value is not None and dest[key]!=value:
                    raise ValueError('Conflicting item fields require correction, not supplement')
                if value is not None:dest[key]=deepcopy(value)
            groups={x['set_id']:x for x in dest.get('sets',[])}
            for group in item.get('sets',[]):
                if group['set_id'] in groups:
                    prior=groups[group['set_id']]
                    for key,value in group.items():
                        if key in prior and prior[key] is not None and value is not None and prior[key]!=value:
                            raise ValueError('Conflicting set values require correction')
                        if value is not None:prior[key]=deepcopy(value)
                else:dest.setdefault('sets',[]).append(deepcopy(group));groups[group['set_id']]=dest['sets'][-1]
    return sorted(sessions.values(),key=lambda s:(s['event_at'] or '',s['session_id']))


def invalidate_cache(root, cause):
    directory=records._checked_path(root,'generated/training')
    if not directory.exists():return
    for p in directory.glob('*.json'):
        records._checked_path(root,p.relative_to(root).as_posix())
        if p.name=='current-state.json':p.unlink();continue
        d=json.loads(p.read_text())
        if d.get('status')=='pending':
            d.update(status='invalidated',invalidated_by=cause)
            records._atomic_write(p,(canonical(d)+'\n').encode())


def cache(root, filename, value):
    path=records._checked_path(records._root(root),'generated/training/'+filename)
    with lock(root):
        records._atomic_write(path,(canonical(value)+'\n').encode())
        if json.loads(path.read_text())!=value:raise ValueError('Training cache readback failed')
    return value


def erase(root, identifier, request_ref):
    """Erase local fact/revision chain and copies; Git/remote/Windows history is explicitly pending.

    Requires a real deletion request. Never invoked by migration, ordinary correction or tests
    against production. Tombstone prevents a retried old message from restoring deleted content.
    """
    if not request_ref:raise ValueError('Explicit deletion request reference required')
    root=records._root(root)
    with lock(root):
        all_events=read_events(root,True);byid={e['id']:e for e in all_events}
        if identifier not in byid:raise ValueError('Unknown training event')
        closure={identifier}
        while True:
            extra={e['id'] for e in all_events if e['event'].get('corrects') in closure or e['event'].get('duplicate_of') in closure}
            extra|={byid[x]['event']['corrects'] for x in closure if byid[x]['event'].get('corrects')}
            extra|={byid[x]['event']['duplicate_of'] for x in closure if byid[x]['event'].get('duplicate_of')}
            if extra<=closure:break
            closure|=extra
        # No hidden deletion of unrelated semantic dependencies.
        targets={byid[x]['path'] for x in closure}; changes=[]
        for eid in sorted(closure):
            old=byid[eid]
            if old['event']['type']=='tombstone':continue
            source={'ref':None,'message_at':None,'excerpt':'已按本人删除请求清除该训练事件及本地派生副本。'}
            tomb={'type':'tombstone','category':'user_reported','event_at':None,'data':{},'schema_version':1}
            changes.append(_change(root,eid,tomb,source,'该事件已删除。来源不可用：按本人删除请求清除原始来源与原文。',[],existing=old))
        # Derived prose can contain copied facts: clear it, leaving its valid source IDs so
        # dot can regenerate from remaining evidence. Primary unrelated facts are not erased.
        review_required=[]
        with records._lock(root):
            docs=records._snapshot(root)
        for path,(meta,body) in docs.items():
            if path in targets:continue
            mentioned=any(eid in body or eid in canonical(meta) for eid in closure)
            if not mentioned:continue
            if meta['document_type'] in {'summary','report'}:
                safe={k:deepcopy(meta[k]) for k in ('schema_version','id','document_type','kind','record_refs')}
                safe['privacy_rebuild_required']=True
                changes.append(dict(path=path,metadata=safe,body='相关私人来源已按本人请求删除，本派生正文已清除；需从剩余有效来源重新生成。',expected_sha256=records._hash((root/path).read_bytes())))
                targets.add(path)
            else:review_required.append(path)
        op='training-delete-'+digest([identifier,request_ref])[:20]
        if changes: records.apply_bundle(root,{'changes':changes},op)
        cleaned=[]
        with records._lock(root):
            records._require_recovered(root)
            for journal in (root/'operations/changes').glob('*.json'):
                j=json.loads(journal.read_text())
                if any(w.get('path') in targets for w in j.get('writes',[])):
                    records._atomic_write(journal,(canonical({'schema_version':1,'state':'privacy_redacted','operation_id':journal.stem,'reason':'deleted training event; content replay disabled'})+'\n').encode())
                    cleaned.append(journal.relative_to(root).as_posix())
            for folder in ('generated/training','generated/index'):
                p=records._checked_path(root,folder)
                if p.exists():
                    # Check descendants before deleting a cache; never follow symlinks.
                    if any(x.is_symlink() for x in p.rglob('*')):raise ValueError('Symlink in cache')
                    shutil.rmtree(p);cleaned.append(folder)
        return {'status':'local_erased','event_ids':sorted(closure),'cleaned':cleaned,
                'other_primary_references_review_required':review_required,
                'remaining':['Git历史、远端副本与Windows可恢复备份须按单独隐私流程处理；本命令不重写历史或伪称已清除。'],
                'request_ref':request_ref}
