#!/usr/bin/env python3
"""Personal OS training entrypoint. Dot interprets text; this deterministic adapter acts.

Run `training.py --help`. Facts always use records.py; read commands can run offline.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import re
import sys
import records
import training_store as store
import training_rules as rules


def state(root,at=None,persist=False):
    at=at or store.now()
    result=rules.rebuild(store.read_events(root),at)
    if persist:store.cache(root,'current-state.json',result)
    return result


def ingest(root,envelope):
    event=envelope.get('event',{});data=event.get('data',{})
    identifier='training-'+store.digest([envelope.get('source',{}).get('ref'),envelope.get('part','main')])[:24]
    journal=records._journal_path(records._root(root),identifier)
    recovering=journal.exists() and json.loads(journal.read_text()).get('state')=='prepared'
    if event.get('type')=='equipment' and not recovering:
        current=state(root)
        old=current['equipment_profiles'].get(data.get('role'))
        role=data.get('role'); base=rules.EXERCISES.get(role)
        if base is None:raise ValueError('Unknown exercise role')
        variant=data.get('variant')
        selection=rules.PLAN['initial_selection'].get(role,[base['variant']])
        changed=old and old['variant']!=variant
        if (changed or variant not in selection) and data.get('owner_confirmed') is not True:
            return {'status':'needs_confirmation','action':'major_exercise_replacement','saved':False}
        if role in rules.PLAN['initial_selection'] and not data.get('selection_reason'):
            raise ValueError('First selection needs its actual reason')
        if role in {'A6','C6'}:
            other=current['equipment_profiles'].get('C6' if role=='A6' else 'A6')
            if other and other['variant']!=variant and data.get('owner_confirmed') is not True:
                return {'status':'needs_confirmation','action':'lateral_raise_variant_conflict','saved':False}
    result=store.ingest(root,envelope)
    # A rebuild is also the readback of semantic state. No generated file is authoritative.
    snapshot=state(root,persist=True)
    result['state_status']=snapshot['training_status']
    return result


def prepare(root,template=None,at=None,context=None,persist=True):
    snapshot=state(root,at)
    p=rules.prescribe(snapshot,template,context)
    if persist:store.cache(root,p['id']+'.json',p)
    return p


def weekly(root,start=None,end=None,at=None):
    at=at or store.now(); end=end or str(store.day(at));start=start or str(store.day(end)-timedelta(days=6))
    return rules.review(state(root,at),start,end)


def dispatch(root,request):
    """Single bridge contract: intent + optional normalized original-source envelope.

    A raw utterance that cannot safely be normalized returns a draft and never fabricates sets.
    """
    intent=request.get('intent');text=request.get('text','').strip();at=request.get('at')
    if intent is None:
        if re.search(r'准备(?:练|训练)|今天练什么|下次.*(?:练|重量)',text):intent='prepare'
        elif '目前状态' in text:intent='state'
        elif '这周' in text or '周复盘' in text:intent='review'
        elif '为什么' in text or '术语' in text:intent='knowledge'
        elif any(x in text for x in ('练完','补一组','说错','睡得差','不舒服','吃了','很累')):intent='record'
        else:return {'status':'no_personal_fact','message':'普通讨论不自动创建训练事实。'}
    if intent=='state':return state(root,at)
    if intent=='prepare':
        match=re.search(r'练\s*([ABCS])',text,re.I)
        return prepare(root,request.get('template') or (match.group(1).upper() if match else None),at,request.get('context'))
    if intent in {'record','correct'}:
        if request.get('envelope'):return ingest(root,request['envelope'])
        return {'status':'needs_normalization','raw_text':text,'saved':False,
                'next_action':'dot按原话和当前会话关联生成source/raw_text/event；未知保留null。仅“练完”可记已结束、明细未知，不能补齐处方。'}
    if intent=='review':return weekly(root,request.get('start'),request.get('end'),at)
    if intent=='nutrition':return rules.nutrition(state(root,at),request.get('start'),request.get('end'))
    if intent=='knowledge':return {'path':str(Path(__file__).resolve().parents[1]/'references/training/knowledge.md'),'sources':str(rules.ASSETS/'sources.json')}
    if intent=='question_due':return {'due':rules.question_due(state(root,at),request['key'])}
    if intent=='change_request':return rules.authority(request['action'])
    if intent=='delete':return store.erase(root,request['event_id'],request['request_ref'])
    raise ValueError('Unknown training intent')


def render(p):
    if 'items' not in p:return json.dumps(p,ensure_ascii=False,indent=2)
    lines=[f"{p['template']}日建议 · {p['plan_version']} · 尚未执行"]
    lines.extend('安排提醒：'+note for note in p.get('scheduling_notes',[]))
    lines.append('热身：'+'；'.join(p['warmup']))
    for i in p['items']:
        s=i['scheme'];q=s['seconds'] or s['reps'];unit='秒' if s['seconds'] else '次'
        load=i['load'];weight=f"{load['value']} {load.get('unit') or '单位待核对'}（{load.get('basis') or '口径待核对'}）" if load else '重量待校准/核对'
        rir=s.get('rir');rest=i['rest_seconds']
        effort=f"保留约{rir[0]}–{rir[1]}次余力" if rir else i.get('effort','保持动作可控')
        quantity=f"{s['sets']}×{q[0]}–{q[1]}{unit}" if q else f"{s['sets']}组，次数未指定"
        rest_text=f"休息约{rest[0]}–{rest[1]}秒" if rest else '休息未指定'
        lines.append(f"{i['role']} {i['name']}：{quantity}"+(' /侧' if i['per_side'] else '')+f"；{weight}；{effort}；{rest_text}；{i['action']}。{i['reason']}")
    field=p.get('field') or {}
    if field.get('modules'):
        lines.append('场地模块：'+json.dumps(field,ensure_ascii=False))
    return '\n'.join(lines)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    sub=parser.add_subparsers(dest='command',required=True)
    for cmd in ('state','prepare','review','nutrition','dispatch','ingest','validate','delete'):
        p=sub.add_parser(cmd)
        if cmd in {'state','prepare','review','nutrition'}:p.add_argument('--at')
        if cmd=='prepare':
            p.add_argument('--template',choices=['A','B','C','S']);p.add_argument('--context',type=Path);p.add_argument('--text',action='store_true')
        if cmd in {'review','nutrition'}:p.add_argument('--start');p.add_argument('--end')
        if cmd in {'ingest','dispatch'}:p.add_argument('--input',type=Path,required=True)
        if cmd=='delete':p.add_argument('--event-id',required=True);p.add_argument('--request-ref',required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=='state':result=state(args.root,args.at,persist=True)
        elif args.command=='prepare':result=prepare(args.root,args.template,args.at,json.loads(args.context.read_text()) if args.context else None)
        elif args.command=='review':result=weekly(args.root,args.start,args.end,args.at)
        elif args.command=='nutrition':result=rules.nutrition(state(args.root,args.at),args.start,args.end)
        elif args.command=='ingest':result=ingest(args.root,json.loads(args.input.read_text()))
        elif args.command=='dispatch':result=dispatch(args.root,json.loads(args.input.read_text()))
        elif args.command=='delete':result=store.erase(args.root,args.event_id,args.request_ref)
        else:
            errors=records.validate_root(args.root)
            for wrapped in store.read_events(args.root):
                if wrapped['event']['type']!='tombstone':store.validate_event(wrapped['event'])
            if errors:raise ValueError('\n'.join(errors))
            result={'status':'valid','training_events':len(store.read_events(args.root))}
        print(render(result) if getattr(args,'text',False) else json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
        return 0
    except (ValueError,KeyError,TypeError,OSError) as exc:
        # Do not echo original private input, stack locals, or external telemetry.
        print(json.dumps({'status':'error','error':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 1

if __name__=='__main__':raise SystemExit(main())
