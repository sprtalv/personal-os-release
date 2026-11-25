"""Pure, versioned prescription and review rules. All loads come from actual evidence.

No LLM calls, account writes, clocks hidden in decisions, or estimates of medical safety.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo
from training_store import canonical, digest, day, merge_sessions

ASSETS=Path(__file__).resolve().parents[1]/'assets/training'
PLAN=json.loads((ASSETS/'plan.json').read_text())
RULES=json.loads((ASSETS/'rules-v2.json').read_text())
EXERCISES={e['role']:e for t in list(PLAN['templates'].values())+list(PLAN.get('optional_templates',{}).values()) for e in t}
STOP_SIGNALS={'sharp_pain','swelling','instability','chest_pain','syncope'}
URGENT={'chest_pain','syncope'}


def at_datetime(at):
    d=datetime.fromisoformat(at.replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError('Evaluation time must include timezone')
    return d.astimezone(ZoneInfo('Asia/Shanghai'))


def actual_sessions(events):
    return merge_sessions(events)


def worksets(item):
    return [s for s in item.get('sets',[]) if s.get('type')=='work']


def load_value(item):
    loads=[s.get('load') for s in worksets(item)]
    if not loads or any(l is None for l in loads) or len({canonical(l) for l in loads})!=1:return None
    return deepcopy(loads[0])


def proposed_load(previous,value):
    # Plate composition/conversion of the previous actual load must not masquerade as
    # the new suggested load's composition.
    return {'value':value,'unit':previous['unit'],'basis':previous['basis'],
            'classification':'ai_proposed','previous_reported_load':deepcopy(previous)}


def rounds(item):
    groups=worksets(item)
    if any(s.get('side') in {'left','right'} for s in groups):
        left=sum(s.get('side')=='left' for s in groups);right=sum(s.get('side')=='right' for s in groups)
        return {'left':left,'right':right,'paired_rounds':min(left,right)}
    return {'bilateral':len(groups)}


def count_rounds(item):
    r=rounds(item)
    return r.get('paired_rounds',r.get('bilateral',0))


def comparison_key(item):
    scheme=item.get('scheme',{})
    load=load_value(item) or {}
    values=[item.get('role'),item.get('variant'),item.get('equipment_id'),load.get('unit'),load.get('basis'),
            item.get('order'),scheme.get('sets'),scheme.get('reps'),scheme.get('seconds'),scheme.get('rir')]
    if any(v is None for v in values[:7]):return None
    if scheme.get('reps') is None and scheme.get('seconds') is None:return None
    return digest(values)


def rir_evidence(item):
    groups=worksets(item)
    if item.get('rir_scope')=='exercise_minimum' and item.get('rir_lower_bound') is not None:
        return item['rir_lower_bound']
    values=[]
    for s in groups:
        if s.get('rir') is not None:values.append(s['rir'])
        elif s.get('rpe_scale')=='rir_based_set' and s.get('rpe') is not None and 7<=s['rpe']<=10:
            values.append(10-s['rpe'])
        else:return None
    return min(values) if values else None


def quality(item,scheme,upper_end=False):
    groups=worksets(item);minimum_rir=scheme.get('rir')
    if not groups or count_rounds(item)!=scheme['sets']:return False
    if EXERCISES.get(item['role'],{}).get('per_side') and (rounds(item).get('left')!=scheme['sets'] or rounds(item).get('right')!=scheme['sets']):return False
    if item.get('technique_ok') is not True or item.get('no_pain') is not True:return False
    if EXERCISES.get(item['role'],{}).get('domain')=='lower_strength' and item.get('no_grinding') is not True:return False
    quantity='seconds' if scheme.get('seconds') else 'reps'; bounds=scheme.get(quantity)
    if bounds is None:return False
    threshold=bounds[1] if upper_end else bounds[0]
    if any(s.get(quantity) is None or s[quantity]<threshold or s[quantity]>bounds[1] for s in groups):return False
    if minimum_rir is not None:
        rir=rir_evidence(item)
        if rir is None or rir<minimum_rir[0]:return False
    return True


def valid_sessions(sessions,template=None):
    return [s for s in sessions if (template is None or s.get('template')==template) and s.get('status')=='completed'
            and s.get('actual_training') is True and s.get('template_completion_confirmed') is True
            and s.get('template') in (PLAN['templates']|PLAN.get('optional_templates',{})) and s.get('plan_id')==PLAN['id']
            and str(s.get('plan_version','')).split('+')[0]==PLAN['version'] and s.get('items') and s.get('event_at')]


def observations(sessions,role):
    return [(s,i) for s in sessions if s.get('event_at') and s.get('status') in {'in_progress','partial','completed'}
            for i in s.get('items',[]) if i['role']==role and worksets(i)]


def phase_schemes(state,template,context=None):
    if template in PLAN.get('optional_templates',{}):
        # Optional days do not gain automatic volume or enter the rolling cycle.
        first=not valid_sessions(state['sessions'],template)
        return {e['role']:{'sets':e['sets'],'reps':deepcopy(e.get('initial_reps') if first else e['reps']),
                'seconds':deepcopy(e['seconds']),'rir':deepcopy(e['rir']),'added_sets':0,
                'phase':'first_optional_calibration' if first else 'optional'}
                for e in PLAN['optional_templates'][template]}
    context=context or {}; sessions=valid_sessions(state['sessions'],template)
    eligible=len(sessions)>=RULES['minimum_exposures_for_phase'] and all(s.get('recovery_ok') is True for s in sessions[-2:])
    result={}; added=0
    maximum=RULES['lower_sets_added_per_session'] if template in {'B','B1','B2'} else RULES['upper_sets_added_per_session']
    lower_block=context.get('new_field_category') or context.get('add_acceleration')
    for e in PLAN['templates'][template]:
        old=observations(state['sessions'],e['role'])
        # Phase volume is acknowledged through completed, recovered sessions, not planned sets.
        completed_ids={s['session_id'] for s in sessions}
        old=[(s,i) for s,i in old if s['session_id'] in completed_ids]
        current=e['initial_sets']
        if old:
            current=max(current,min(e['sets'],old[-1][1].get('scheme',{}).get('sets',current)))
        new=current
        if eligible and current<e['sets'] and added<maximum and not (template in {'B','B1','B2'} and lower_block):
            # Actual last exposure must complete its known prescription with quality.
            if old and quality(old[-1][1],old[-1][1].get('scheme',{})):
                new=current+1;added+=1
        initial=not eligible
        result[e['role']]={'sets':new,'reps':deepcopy(e.get('initial_reps',e['reps']) if initial else e['reps']),
                          'seconds':deepcopy(e.get('initial_seconds',e['seconds']) if initial else e['seconds']),
                          'rir':deepcopy(e['initial_rir'] if initial else e['rir']),
                          'added_sets':new-current,'phase':'calibration' if initial else 'building'}
    return result


def rebuild(events,at):
    dt=at_datetime(at)
    eligible=[e for e in events if e['event'].get('event_at') is None or day(e['event']['event_at'])<=dt.date()]
    # A known timestamp later today is not yet evidence at this evaluation time.
    eligible=[e for e in eligible if e['event'].get('event_at') is None or len(e['event']['event_at'])==10 or at_datetime(e['event']['event_at'])<=dt]
    facts=[e for e in eligible if e['event'].get('category') in {'user_reported','confirmed_policy','system_observed'} and e['event']['type']!='tombstone']
    profiles=[e for e in facts if e['event']['type']=='profile']
    profile=deepcopy(profiles[-1]['event']['data']) if profiles else {}
    sessions=actual_sessions(facts)
    equipment={};symptoms={};contexts=[];measurements=[];foods=[];habits=[];policies=[];questions=[]
    for e in facts:
        t=e['event']['type'];d=deepcopy(e['event']['data']);d.update(source_event_id=e['id'],event_at=e['event'].get('event_at'))
        if t=='equipment':equipment[d['role']]=d
        elif t=='symptom':symptoms[d.get('region','unspecified')]=d
        elif t=='context':contexts.append(d)
        elif t=='measurement':measurements.append(d)
        elif t=='food':foods.append(d)
        elif t=='habit':habits.append(d)
        elif t=='question':questions.append(d)
        elif t=='policy':policies.append(d)
    anchors={}
    for s in sessions:
        if s.get('status') not in {'in_progress','partial','completed'} or not s.get('event_at'):continue
        for item in s.get('items',[]):
            key=comparison_key(item);load=load_value(item)
            if key is None or load is None:continue
            scheme=item.get('scheme',{})
            if not quality(item,scheme) or s.get('acute_adjustment') is True:continue
            previous=anchors.get(key)
            anchors[key]={'role':item['role'],'comparison_key':key,'variant':item['variant'],
                'equipment_id':item['equipment_id'],'load':load,'sets':deepcopy(worksets(item)),
                'rir_lower_bound':item.get('rir_lower_bound'),'rir_scope':item.get('rir_scope'),
                'quality_source':item.get('quality_source'),'event_at':s['event_at'],
                'source_event_ids':s['source_event_ids'],'confirmed_exposures':(previous['confirmed_exposures']+1 if previous and previous['load']==load else 1),
                'status':'needs_revalidation' if (dt.date()-day(s['event_at'])).days>RULES['freshness_days'] else ('confirmed' if previous and previous['load']==load else 'provisional')}
    completed=valid_sessions(sessions)
    # An actual partial/completed workout advances the rolling day, even when
    # missing details keep it out of comparable exposure/progression counts.
    ended_actual=[x for x in sessions if x.get('actual_training') is True and x.get('status') in {'partial','completed'} and x.get('template') in PLAN['sequence'] and x.get('event_at')]
    recent_templates=[]
    for finished in reversed(ended_actual):
        if finished['template'] not in recent_templates: recent_templates.append(finished['template'])
        if len(recent_templates)==3: break
    if len(recent_templates)==2:
        next_template=next(t for t in PLAN['sequence'] if t not in recent_templates)
    elif ended_actual:
        next_template=PLAN['sequence'][(PLAN['sequence'].index(ended_actual[-1]['template'])+1)%len(PLAN['sequence'])]
    else:
        next_template=PLAN['sequence'][0]
    phases={t:('calibration' if len(valid_sessions(sessions,t))<2 or not all(s.get('recovery_ok') is True for s in valid_sessions(sessions,t)[-2:]) else 'building') for t in PLAN['sequence']}
    status=('actual_reports_pending_calibration' if sessions else 'awaiting_first_calibration') if not anchors else 'calibrating' if any(a['status']=='provisional' for a in anchors.values()) else 'active'
    jump=profile.get('primary_jump_goal',{'confirmed_value':None,'raw_transcript':None,'interpretation_candidate':None,'confirmed':False})
    for policy in policies:
        if policy.get('action')=='confirm_jump_goal':
            jump={'confirmed_value':policy.get('value'),'confirmed':True,'source_event_id':policy['source_event_id'],'protocol':policy.get('protocol')}
        elif policy.get('action')=='update_goals':
            profile['goals']=policy.get('value')
        elif policy.get('action')=='update_constraints':
            profile['constraints']=policy.get('value')
    questions.sort(key=lambda q:(q.get('accepted_at') or q.get('delivered_at') or q.get('observed_at') or q.get('event_at') or '',q['source_event_id']))
    questions_pending=[]
    if not jump.get('confirmed'):questions_pending.append({'key':'primary_jump_goal','text':'你说的是带助跑的弹跳吗？','when':'field_baseline'})
    selected_version=PLAN['version']+('+selection.'+digest(equipment)[:10] if equipment else '')
    state=dict(schema_version=1,as_of=at,timezone='Asia/Shanghai',active_plan_id=PLAN['id'],active_plan_version=selected_version,rule_version=RULES['version'],
        source_event_ids=[e['id'] for e in facts],training_status=status,profile=profile,phases_by_domain={'upper_push':phases['A'],'upper_pull':phases['A'],'lower_strength':phases.get('B1',phases.get('B')),'lower_power':phases.get('B2',phases.get('B'))},
        phases_by_template=phases,completed_exposures_by_template={t:len(valid_sessions(sessions,t)) for t in PLAN['sequence']},
        optional_completed_exposures_by_template={t:len(valid_sessions(sessions,t)) for t in PLAN.get('optional_templates',{})},
        performance_anchors_by_exercise_role=anchors,latest_actual_sessions=sessions[-3:],sessions=sessions,
        body_measurements=measurements,recent_context=contexts,symptom_reports=[d for d in symptoms.values() if d.get('resolved') is not True],
        primary_jump_goal=jump,equipment_profiles=equipment,current_constraints=profile.get('constraints',[]),pending_questions=questions_pending,
        data_quality_notes=['没有记录不等于没有训练；质量字段是报告，未代表现场姿势核验。'],next_prescriptions=[],
        foods=foods,habits=habits,policies=policies,question_history=questions,
        actual_cycle_start=completed[0]['event_at'] if completed else None,
        next_template=next_template)
    state['evidence_hash']=digest([{'id':e['id'],'event':e['event']} for e in facts])
    return state


def safety(state,domain,context):
    symptoms=state['symptom_reports']+context.get('symptoms',[])
    related_domains={domain}
    if domain in {'lower_strength','lower_control','field'}:related_domains|={'lower_strength','lower_control','field'}
    if domain in {'upper_push','upper_pull'}:related_domains|={'upper_push','upper_pull'}
    applicable=[s for s in symptoms if s.get('domain') in {None,'all'}|related_domains or s.get('signal') in URGENT]
    urgent=next((s for s in applicable if s.get('signal') in URGENT),None)
    if urgent:return 'stop_urgent','停止运动；运动中胸痛/晕厥等严重急性症状需及时急诊评估。'
    if any(s.get('signal') in STOP_SIGNALS for s in applicable):return 'stop','停止相关负荷，先做适当医疗评估。'
    if applicable:return 'hold_for_symptom_check','相关不适尚未澄清；先暂停该项目负荷建议，不能用旧的无伤病报告覆盖。'
    return None,None


def permissible_increment(load,equipment,exercise):
    if load is None or load.get('unit') is None or load.get('basis') is None:return None,'load_unit_or_basis_unknown'
    if load['basis']=='plates_only':return None,'bar_total_unknown'
    if load['basis']=='assistance':return None,'assistance_direction_needs_defined_rule'
    if load['basis']=='bar_total' and equipment.get('bar_weight_kg') is None:return None,'bar_weight_unknown'
    if equipment.get('unit')!=load['unit'] or equipment.get('basis')!=load['basis']:return None,'equipment_unit_or_basis_unknown'
    levels=equipment.get('available_loads')
    if not levels:return None,'available_loads_unknown'
    larger=sorted(x for x in set(levels) if x>load['value'])
    if not larger:return None,'step_limited'
    candidate=larger[0];delta=candidate-load['value']
    if load['value']<=0 or delta/load['value']>RULES['max_increment_fraction']+1e-9:return None,'step_limited'
    total_factor=2 if load['basis']=='per_dumbbell' else 1
    kg_factor=0.45359237 if load['unit']=='lb' else 1
    if exercise['main']:
        if load['unit']=='display':return None,'main_load_mass_unknown'
        cap=RULES['lower_main_max_kg'] if exercise['domain']=='lower_strength' else RULES['upper_main_max_kg']
        if delta*kg_factor*total_factor>cap+1e-9:return None,'step_limited'
    return proposed_load(load,candidate),'two_comparable_successes'


def acute_adjust(load,equipment,scheme):
    levels=equipment.get('available_loads') or []
    candidates=[x for x in levels if load and 0.90*load['value']<=x<=0.95*load['value']]
    if candidates:return proposed_load(load,max(candidates)),dict(scheme),'已知可用档位当次降低约5%–10%；历史能力锚点保留。'
    lower=[x for x in levels if load and x<load['value']]
    if lower:return proposed_load(load,max(lower)),dict(scheme),'退回最近已知较轻档位，重新核对余力；若仍不稳结束项目。'
    return None,{**scheme,'sets':max(1,scheme['sets']-1)},'具体较轻档位未知；先减少一组并用现场可控档位重新校准，不猜公斤数。'


def decide_exercise(state,e,scheme,context):
    role=e['role'];equipment=state['equipment_profiles'].get(role,{})
    variant=equipment.get('variant',e['variant'])
    result={'role':role,'name':e['name'],'variant':variant,'domain':e['domain'],'equipment_id':equipment.get('equipment_id'),
            'order':e.get('order'),'kind':e['kind'],'per_side':e['per_side'],'scheme':deepcopy(scheme),'rest_seconds':e['rest_seconds'],
            'load':None,'source_event_ids':[],'rule_version':RULES['version'],'requires_approval':False,'comparison_key':None,'pending_questions':[]}
    stop,reason=safety(state,e['domain'],context)
    if stop:result.update(action=stop,reason=reason);return result
    if context.get('explicit_limit'):
        result.update(action='stop',reason='本人明确限制优先。');return result
    if e.get('progression')=='owner_fixed_pullup':
        result.update(action='owner_fixed_pullup', effort=e['effort'], reason='按模板做引体，次数和负荷不自动递增。')
        return result
    if role in PLAN['initial_selection'] and not equipment:
        result.update(action='select_then_calibrate',reason='按已定义的首次选型分支现场确认器械、单位和动作，再校准；起始重量未知。',options=PLAN['initial_selection'][role]);return result
    history=observations(state['sessions'],role)
    relevant=[(s,i) for s,i in history if i.get('variant')==variant and (not equipment or i.get('equipment_id')==equipment.get('equipment_id'))]
    if not relevant:
        result.update(action='calibrate',reason='本角色/设备尚无真实可比工作重量；从明显可控的轻负荷开始。');return result
    s,last=relevant[-1];result['source_event_ids']=s['source_event_ids'];result['last_actual']=deepcopy(last)
    if last.get('no_pain') is False:
        result.update(action='stop',reason='最近该动作明确报告不适，先暂停相关负荷建议并核对，不把它当普通加重缺口。');return result
    if last.get('technique_ok') is False or (e['domain']=='lower_strength' and last.get('no_grinding') is False):
        result.update(action='revalidate',reason='最近动作失控或下肢明显磨蹭，先用可控轻负荷重新核对，不奖励硬完成。');return result
    load=load_value(last)
    result['load']=load
    if (day(state['as_of'])-day(s['event_at'])).days>RULES['freshness_days']:
        result.update(action='revalidate',load=None,reason='超过14天无可比记录：先区分未报告和实际停训，再热身重验；不推算力量下降。');return result
    if context.get('illness') or context.get('actual_long_break'):
        result.update(action='revalidate',load=None,reason='疾病或实际较长中断后重新校准，固定减重比例不能证明适宜。');return result
    if context.get('unstable_after_adjustment'):
        result.update(action='stop',load=None,reason='保守调整后仍不稳，结束该项目。');return result
    if context.get('performance_below_target') or context.get('rir_below_target'):
        new_load,new_scheme,why=acute_adjust(load,equipment,scheme)
        result.update(action='acute_reduce',load=new_load,scheme=new_scheme,reason=why);return result
    if context.get('fatigue') or context.get('poor_sleep'):
        result.update(action='check_warmup',reason='单次疲劳/睡差先看热身与实际表现，尚无依据自动永久降重。');return result
    if e['kind']=='technique':
        result.update(action='hold_technique',load=None,reason='高拉只按速度与接杠技术保持或暂停；没有现场技术确认不自动增加重量。');return result
    if e['kind']=='isometric':
        result.update(action='hold_duration',load=None,reason='在规定秒数区间和姿势质量内推进；达到上端后保持，未授权额外难度。');return result
    if last.get('scheme') is None or comparison_key(last) is None or load is None:
        result.update(action='hold_data_gap',reason='保留真实组数，负重口径/设备/训练角色等比较条件不完整。',pending_questions=['确认本次设备、含杆/每只口径及目标工作组范围。']);return result
    current_core={k:scheme.get(k) for k in ('sets','reps','seconds','rir')}
    last_core={k:last['scheme'].get(k) for k in current_core}
    if current_core!=last_core:
        result.update(action='hold_scheme_change',reason='组数、区间或余力方案已调整；保持负重，按新条件积累记录。');return result
    if last.get('order')!=e.get('order'):
        result.update(action='hold_comparability',reason='实际动作顺序变化，需要重新积累同顺序证据。');return result
    result['comparison_key']=comparison_key(last)
    if s.get('acute_adjustment'):
        anchor=[a for a in state['performance_anchors_by_exercise_role'].values() if a['comparison_key']==result['comparison_key']]
        if anchor:result['load']=anchor[-1]['load']
        result.update(action='recheck_baseline',reason='上次为当次保守调整；原能力锚点保留，本次热身确认后再采用。');return result
    # Intervening exposure at a different device/order breaks the consecutive evidence pair.
    last_two=history[-2:]
    comparable=len(last_two)==2 and all(comparison_key(i)==comparison_key(last) and load_value(i)==load and not ses.get('acute_adjustment') for ses,i in last_two)
    success=comparable and all(quality(i,current_core,upper_end=True) for _,i in last_two)
    if success:
        if not all(ses.get('plan_id')==PLAN['id'] and str(ses.get('plan_version','')).split('+')[0]==PLAN['version'] for ses,_ in last_two):
            result.update(action='hold_plan_provenance',reason='实际参照的方案版本未明确，先核对，不能把旧安排直接当新计划两次达标。');return result
        if e['domain']=='lower_strength' and not all(ses.get('recovery_ok') is True for ses,_ in last_two):
            result.update(action='hold_tolerance_gap',reason='下肢近期走路/楼梯与训练耐受尚未确认；先保持并补关键反馈。');return result
        increment,why=permissible_increment(load,equipment,e)
        result['source_event_ids']=list(dict.fromkeys(x for ses,_ in last_two for x in ses['source_event_ids']))
        if increment:
            result.update(action='increase',load=increment,reason='两次同角色、同负荷、同组次均达到上端且质量/余力合格；使用最小可用档。',rule_id=why,target_reps=current_core['reps'][0]);return result
        result.update(action='hold',reason=why,rule_id=why,pending_questions=['只补负重口径或最小可用档；档位太大时维持。']);return result
    if quality(last,current_core):
        result.update(action='hold_add_reps',reason='保持重量和组数，在原区间争取总计多一次合格重复；不要求硬完成。');return result
    result.update(action='hold_quality_gap',reason='加重所需的完整动作/余力/无不适证据不足，保留实际表现，不补造余力。',
                  pending_questions=['其他组也都标准完成，没有比最后一组更难或出现不适吗？'])
    return result


def optional_s_readiness(state,context):
    missing=[]
    for key,description in (
        ('shoulder_discomfort_resolved','肩部目前无相关不适'),
        ('pain_free_bar_position','持杠姿势无痛且保护架已到位'),
        ('walking_recovered','腿部走路、楼梯和热身动作已恢复'),
        ('sports_load_compatible','近期球类负荷与本次重腿训练可兼容')):
        if context.get(key) is not True:missing.append(description)
    if context.get('fatigue') is True or context.get('poor_sleep') is True:
        missing.append('当次疲劳或睡差需先恢复，不把S作为必须完成的训练')
    verified_hours=context.get('confirmed_spacing_hours')
    spacing_confirmed=isinstance(verified_hours,(int,float)) and verified_hours>=72
    if not spacing_confirmed:missing.append('核对距上次重腿至少约72小时；不从日期或未报告推定间隔')
    lower=[s for s in state['sessions'] if s.get('actual_training') is True and s.get('status') in {'in_progress','partial','completed'}
           and s.get('event_at') and any(EXERCISES.get(i.get('role'),{}).get('domain')=='lower_strength' for i in s.get('items',[]))]
    if lower:
        latest=max(lower,key=lambda s:s['event_at'])['event_at']
        if len(latest)==10:
            # Calendar dates three days apart do not prove 72 elapsed hours.
            if (day(state['as_of'])-day(latest)).days<3:
                missing.append('距上次重腿至少约72小时；只有日期时需明确核对实际间隔')
        elif (at_datetime(state['as_of'])-at_datetime(latest)).total_seconds()<72*3600:
            missing.append('距上次重腿尚不足约72小时')
    return missing


def prescribe(state,template=None,context=None):
    template=template or state['next_template'];context=deepcopy(context or {})
    templates=PLAN['templates']|PLAN.get('optional_templates',{})
    if template not in templates:raise ValueError('Template must be an active A/B/C or explicitly requested optional S')
    # Today-only contextual reports; habits never become today's sleep/pain facts.
    for c in state['recent_context']:
        if c.get('event_at') and day(c['event_at'])==day(state['as_of']):
            for k in ('fatigue','poor_sleep','illness','actual_long_break'):
                if c.get(k) is not None:context.setdefault(k,c[k])
    schemes=phase_schemes(state,template,context)
    items=[decide_exercise(state,e,schemes[e['role']],context.get('by_role',{}).get(e['role'],{})|{k:v for k,v in context.items() if k!='by_role'}) for e in templates[template]]
    scheduling=[]
    completed=[s for s in state['sessions'] if s.get('actual_training') is True and s.get('event_at') and s.get('status') in {'partial','completed','in_progress'}]
    if completed and (day(state['as_of'])-day(completed[-1]['event_at'])).days<=1:
        scheduling.append('优先非连续抗阻日；先核对实际恢复与时间，不把漏练压成连续三天。')
        if day(state['as_of'])==day(completed[-1]['event_at']):
            scheduling.append('今天已有实际训练报告，不默认追加一整场；以下为待恢复和日程核对后的建议。')
    if template in PLAN.get('optional_templates',{}):
        missing=optional_s_readiness(state,context)
        scheduling.append('S是本人明确选择时才使用的额外深蹲日；不进入A→B→C循环，不补漏练。B与S都实际完成时为7个直接深蹲处方组，恢复优先。')
        if missing:
            for item in items:item.update(action='hold_optional_readiness',load=None,reason='；'.join(missing))
        else:
            scheduling.append('首次S增加的是组数，保持/校准负重，不因上一次单场表现自动加重；第四组质量下降可减重或结束于三组。')
            if state['optional_completed_exposures_by_template'][template]==0:
                for item in items:
                    item.update(action='calibrate_optional_first',load=None,reason='首次S按4×5校准，不能把B深蹲负荷直接当S已确认工作重量。')
    result={'schema_version':1,'classification':'ai_proposed','status':'readiness_check_required' if template in PLAN.get('optional_templates',{}) and missing else 'pending','as_of':state['as_of'],'plan_id':PLAN['id'],'plan_version':state['active_plan_version'],'rule_version':RULES['version'],
        'template':template,'source_event_ids':state['source_event_ids'],'evidence_hash':state['evidence_hash'],'items':items,
        'target_template':deepcopy(templates[template]),'target_template_note':PLAN.get('adoption_note'),'field':None,'warmup':PLAN['warmup'],'scheduling_notes':scheduling,
        'end_conditions':['不适、动作失控或保守调整后仍不稳，结束相关项目。','有限模板即上限；热身和休息不压缩，无90分钟硬限制。'],
        'adoption_state':'unknown','execution_state':'not_reported'}
    if template in PLAN.get('optional_templates',{}):result['readiness_missing']=missing
    result['id']='training-prescription-'+digest([result,context])[:24]
    return result


def question_due(state,key,at=None,immediate=False):
    history=[x for x in state['question_history'] if x['key']==key]
    if not history:return True
    last=history[-1]
    if last['action'] in {'answered','declined','paused','stopped'}:return False
    stamp=(last.get('accepted_at') or last.get('delivered_at')) if last['action']=='asked' else (last.get('observed_at') or last.get('event_at'))
    if stamp is None:return False  # Unknown last contact time: check rather than guessing.
    return (day(at or state['as_of'])-day(stamp)).days>=RULES['question_cadence_days']


def authority(action):
    allowed={'within_plan_load','within_plan_reps','acute_reduce','stop_field','initial_equipment_selection','defined_phase_progression','record','correct'}
    needs=action not in allowed
    return {'action':action,'status':'needs_confirmation' if needs else 'allowed_within_plan','requires_approval':needs}


def nutrition(state,start=None,end=None):
    end=day(end or state['as_of']);start=day(start) if start else end-timedelta(days=6)
    measurements=[m for m in state['body_measurements'] if m.get('event_at') and start<=day(m['event_at'])<=end]
    weight=[m for m in measurements if m['metric']=='weight' and m['unit']=='kg' and m.get('conditions')=='morning_comparable']
    # Average one value per day; repeated same-day measurements are not extra independent days.
    dated={}
    for m in weight:dated.setdefault(str(day(m['event_at'])),[]).append(m['value'])
    daily=[mean(v) for v in dated.values()]
    all_weight=[m for m in state['body_measurements'] if m['metric']=='weight' and m['unit']=='kg' and m.get('conditions')=='morning_comparable' and m.get('event_at')]
    weeks={}
    for m in all_weight:
        date_=day(m['event_at'])
        if date_>end:continue
        monday=date_-timedelta(days=date_.weekday());weeks.setdefault(str(monday),{}).setdefault(str(date_),[]).append(m['value'])
    valid=[(w,mean(mean(v) for v in days.values()),len(days)) for w,days in sorted(weeks.items()) if len(days)>=RULES['weight_week_min_samples']]
    trend=None
    if len(valid)>=3 and (day(valid[-1][0])-day(valid[-3][0])).days>=14:
        first,last=valid[-3],valid[-1]
        trend={'weekly_fraction':(last[1]/first[1]-1)/((day(last[0])-day(first[0])).days/7),'weeks':valid[-3:],'basis':'comparable_reported_morning_weights'}
    foods=[f for f in state['foods'] if f.get('event_at') and start<=day(f['event_at'])<=end]
    profile=state['profile'];body_range=profile.get('reported_weight_range_kg')
    target=[round(body_range[0]*RULES['protein_g_kg'][0]),round(body_range[1]*RULES['protein_g_kg'][1])] if body_range else None
    notes=['先保持三餐，收集约两周可比基线；体重稳定但表现改善可保持。','具体套餐、份量、耐受未知时不算精确热量或推荐特定食物份量。','饮水依口渴、环境、出汗和时长，；补剂不自动新增。']
    proposal=None
    if trend and any(c.get('nutrition_adherence_checked') is True and c.get('performance_stalled') is True for c in state['recent_context'][-3:]) and trend['weekly_fraction']<0.001:
        proposal={'classification':'ai_proposed','extra_kcal_per_day':RULES['proposed_extra_kcal'],'reason':'约2–3周可比趋势且执行已核对；结合表现考虑增加摄入，不作为强制增重任务。','executed':False}
    return {'from':str(start),'to':str(end),'protein_target_g':target,
        'weight_mean_kg':mean(daily) if daily else None,'weight_measurement_days':len(daily),'weight_trend':trend,
        'waist_measurements':[m for m in measurements if m['metric']=='waist'],'food_records':foods,'coverage':'reported_portion_only',
        'daily_total_kcal':None,'energy_surplus':None,'habits':state['habits'],'suggestion':proposal,'notes':notes}


def review(state,start,end):
    first,last=day(start),day(end)
    if first>last:raise ValueError('Review start after end')
    sessions=[s for s in state['sessions'] if s.get('event_at') and first<=day(s['event_at'])<=last]
    actual=[s for s in sessions if s.get('status') in {'completed','partial','in_progress'}]
    totals={};field={};comparisons={};plateaus=[]
    for s in actual:
        for i in s.get('items',[]):
            if i.get('kind')=='field':
                field.setdefault(i.get('variant','unknown'),[]).append({'event_at':s['event_at'],'reported':deepcopy(i),'source_event_ids':s['source_event_ids']});continue
            r=rounds(i)
            total=totals.setdefault(i['role'],{'bilateral':0,'left':0,'right':0,'paired_rounds':0})
            for k,v in r.items():total[k]+=v
    for role in EXERCISES:
        history=observations(state['sessions'],role)
        keys={comparison_key(i) for _,i in history if comparison_key(i)}
        comparisons[role]=[]
        for key in sorted(keys):
            comparable=[(s,i) for s,i in history if comparison_key(i)==key and day(s['event_at'])<=last]
            if not comparable:continue
            comparisons[role].append({'comparison_key':key,'actual':[{'event_at':s['event_at'],'load':load_value(i),'reps':[g.get('reps') for g in worksets(i)],'source_event_ids':s['source_event_ids']} for s,i in comparable[-3:]],'interpretation':'同角色、同设备及方案的实际记录；不同重量/余力需结合判断。'})
            three=comparable[-3:]
            if len(three)==3 and (day(three[-1][0]['event_at'])-day(three[0][0]['event_at'])).days>=RULES['plateau_days']:
                signatures=[(load_value(i),[g.get('reps') for g in worksets(i)]) for _,i in three]
                if all(x==signatures[0] for x in signatures):plateaus.append({'role':role,'action':'review_execution_recovery_nutrition_data','requires_approval_for_extra_volume':True})
    decline_roles=[]
    for role in ('A1','A3','C1','C3','B1'):
        h=observations(state['sessions'],role)[-3:]
        if len(h)==3 and len({comparison_key(i) for _,i in h})==1 and comparison_key(h[0][1]):
            same=all(load_value(i)==load_value(h[0][1]) for _,i in h)
            reps=[sum(g.get('reps') or 0 for g in worksets(i)) for _,i in h]
            if same and reps[0]>reps[1]>reps[2]:decline_roles.append(role)
    deload=None
    if len(decline_roles)>=2 and any(c.get('persistent_fatigue') is True or c.get('recovery_affected') is True for c in state['recent_context'][-3:]):
        deload={**authority('whole_week_deload'),'set_reduction':RULES['whole_week_deload_set_reduction'],'reason':'多个主项反复下降并有持续恢复问题；先本人确认，不诊断过度训练。'}
    cycle_start=state['actual_cycle_start'];stage_due=cycle_start is not None and (last-day(cycle_start)).days>=56
    return {'schema_version':1,'classification':'system_derived','from':str(first),'to':str(last),'timezone':'Asia/Shanghai',
        'sessions':[{'session_id':s['session_id'],'event_at':s['event_at'],'status':s['status'],'template':s.get('template'),'reason':s.get('partial_reason'),'source_event_ids':s['source_event_ids']} for s in sessions],
        'actual_completed':len(valid_sessions(sessions)),'actual_completed_scope':'confirmed_current_plan_templates',
        'actual_reports':len(actual),'reported_ended':sum(s.get('ended') is True or s.get('status')=='completed' for s in actual),
        'missing_information':'unknown','sets_by_role_and_side':totals,'field_by_category':field,
        'performance_by_role':comparisons,'nutrition':nutrition(state,str(first),str(last)),'plateau_reviews':plateaus,'deload_proposal':deload,
        'stage_review_due':stage_due,'stage_review_reason':'实际开始约8周，复核并可延续有效动作。' if stage_due else None,
        'source_event_ids':state['source_event_ids'],'next_action':'维持有效剂量；缺失记录不判失败，不补训练债务。'}
