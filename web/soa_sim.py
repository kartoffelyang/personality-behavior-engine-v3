"""HTTP control surface for the local simulator, never an external SOA proxy."""
import json
from pathlib import Path
import re
import threading
from flask import Blueprint, jsonify, render_template, request
from engine.soa_simulator import new_session, execute, snapshot, SimError, prepare_public

bp=Blueprint('soa_sim',__name__)
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output'/'soa_sim'
LOCK=threading.Lock()


def valid_id(value):return isinstance(value,str) and bool(re.fullmatch(r'[a-f0-9]{32}',value))


def read_session(sid):
    if not valid_id(sid):raise SimError('NOT_FOUND','模拟会话不存在')
    try:return json.loads((OUT/f'{sid}.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):raise SimError('NOT_FOUND','模拟会话不存在')


def save(s):
    OUT.mkdir(parents=True,exist_ok=True)
    p=OUT/f'{s["id"]}.json';temp=p.with_suffix('.tmp')
    temp.write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(p)


@bp.get('/soa-sim')
def page():return render_template('soa_sim.html')


@bp.post('/api/soa-sim/sessions')
def create():
    data=request.get_json(silent=True) or {}
    if not isinstance(data,dict) or not valid_id(data.get('job_id')) or data.get('persona') not in {'ESTJ','INFP'}:
        return jsonify(error='请选择一个已生成的计划和人格。'),400
    try:
        record=json.loads((ROOT/'output'/'fast'/f'{data["job_id"]}.json').read_text(encoding='utf-8'))
        variant=next(v for v in record['result']['variants'] if v['persona']==data['persona'])
        actions=variant['actions']
    except (OSError,ValueError,KeyError,StopIteration):return jsonify(error='未找到可用的生成计划。'),404
    with LOCK:
        s=new_session(data['job_id'],data['persona'],actions);s['input']=record.get('input','');save(s)
    return jsonify(snapshot(s)),201


@bp.get('/api/soa-sim/sessions/<sid>')
def get(sid):
    try:
        with LOCK:s=read_session(sid)
        return jsonify(snapshot(s))
    except SimError as e:return jsonify(error=e.message,code=e.code),404


@bp.post('/api/soa-sim/sessions/<sid>/step')
def step(sid):
    data=request.get_json(silent=True) or {}
    if not isinstance(data,dict):return jsonify(error='请求格式错误'),400
    try:
        with LOCK:
            s=read_session(sid)
            updated=execute(s,revision=data.get('revision'),params=data.get('params'),confirmed=data.get('confirmed',False),fault=data.get('fault'),manual_content=data.get('manual_content'))
            save(updated)
        return jsonify(snapshot(updated))
    except SimError as e:
        return jsonify(error=e.message,code=e.code),409 if e.code=='STALE_REVISION' else 404 if e.code=='NOT_FOUND' else 400


@bp.post('/api/soa-sim/sessions/<sid>/prepare')
def prepare(sid):
    data=request.get_json(silent=True) or {}
    if not isinstance(data,dict):return jsonify(error='请求格式错误'),400
    try:
        with LOCK:
            s=read_session(sid)
            if type(data.get('revision')) is not int or data['revision']!=s['revision']:
                raise SimError('STALE_REVISION','会话已更新，请刷新')
            updated=prepare_public(s);save(updated)
        return jsonify(snapshot(updated))
    except SimError as e:
        return jsonify(error=e.message,code=e.code),409 if e.code=='STALE_REVISION' else 400
