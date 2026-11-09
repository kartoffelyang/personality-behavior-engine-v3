import json
import re
import threading
import uuid
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from flask import Blueprint, jsonify, render_template, request
from engine.persona_demo import cases, kev_cases, run_demo

bp=Blueprint('persona_demo',__name__)
OUT=Path(__file__).resolve().parents[1]/'output'/'persona_demo'
POOL=ThreadPoolExecutor(max_workers=2);LOCK=threading.Lock();JOBS={};SLOTS=threading.BoundedSemaphore(2)

def persist(jid,data):
    OUT.mkdir(parents=True,exist_ok=True);p=OUT/(jid+'.json');tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2));tmp.replace(p)

def worker(jid,text,world,model='original',case_id=None):
    started=time.monotonic()
    try:
        def progress(**fields):
            with LOCK:JOBS[jid].update(fields)
        if model=='kev':
            from engine.kev_demo import run_kev_demo
            result=run_kev_demo(text,world,jid,progress,case_id=case_id)
        else:
            result=run_demo(text,world,jid,progress)
        audits=result.pop('audits');persist(jid+'_audit',audits)
        with LOCK:JOBS[jid].update(status='completed',stage='completed',elapsed_seconds=round(time.monotonic()-started,2),result=result,variants=result['variants'])
    except Exception as e:
        # Do not expose provider response bodies or credentials.
        from engine.kev_demo import KevDemoError
        message=str(e) if isinstance(e,KevDemoError) else '本次生成未完成，请重试；运行记录保留。'
        with LOCK:JOBS[jid].update(status='failed',failed_stage=JOBS[jid].get('stage'),stage='failed',elapsed_seconds=round(time.monotonic()-started,2),error=message,error_type=type(e).__name__)
    finally:
        with LOCK:persist(jid,JOBS[jid])
        SLOTS.release()

@bp.get('/demo')
@bp.get('/demo/kev')
def page():
    model='kev' if request.path.endswith('/kev') else 'original'
    return render_template('persona_demo.html',cases=kev_cases() if model=='kev' else cases(),model=model)

@bp.post('/api/persona-demo')
def create():
    data=request.get_json(silent=True) or {}
    if not isinstance(data,dict):return jsonify(error='请求格式错误'),400
    model=data.get('model','original')
    if model not in ('original','kev'):return jsonify(error='不支持的模型入口'),400
    case=next((c for c in (kev_cases() if model=='kev' else cases()) if c['id']==data.get('case_id')),None)
    text=data.get('text',case['text'] if case else '')
    if not isinstance(text,str) or not 1<=len(text.strip())<=2000:return jsonify(error='请填写2000字以内的情境'),400
    if model=='kev' and case and text.strip()!=case['text']:case=None
    world=case['world'] if case else '当前车辆已安全驻车，用户独自乘坐；时间、地点、资源与所有动作均可在虚拟世界合理补全并标注演示假设，无真实外部操作。不推断真实个人历史。'
    if model=='kev' and case is None:
        from engine.kev_free import FREE_WORLD
        world=FREE_WORLD
    if not SLOTS.acquire(blocking=False):return jsonify(error='已有演示正在生成，请稍候'),429
    jid=uuid.uuid4().hex
    with LOCK:JOBS[jid]={'id':jid,'input':text.strip(),'world':world,'model':model,'case_id':case['id'] if case else None,'status':'running','stage':'queued','variants':{}}
    POOL.submit(worker,jid,text.strip(),world,model,case['id'] if case else None)
    return jsonify(id=jid),202

@bp.get('/api/persona-demo/<jid>')
def get(jid):
    if not re.fullmatch(r'[a-f0-9]{32}',jid):return jsonify(error='记录不存在'),404
    with LOCK:
        if jid in JOBS:return jsonify(JOBS[jid])
    try:return jsonify(json.loads((OUT/(jid+'.json')).read_text()))
    except (OSError,ValueError):return jsonify(error='记录不存在'),404

@bp.get('/api/persona-demo/<jid>/audit')
def audit(jid):
    if not re.fullmatch(r'[a-f0-9]{32}',jid):return jsonify(error='记录不存在'),404
    try:return jsonify(json.loads((OUT/(jid+'_audit.json')).read_text()))
    except (OSError,ValueError):return jsonify(error='记录尚未生成'),404

@bp.get('/api/persona-demo/examples')
def examples():
    if request.args.get('model')=='kev':
        from engine.kev_input import INPUT_VERSION
        try:return jsonify(json.loads((OUT/('kev_examples_'+INPUT_VERSION+'.json')).read_text()))
        except (OSError,ValueError):return jsonify({})
    try:return jsonify(json.loads((OUT/'examples.json').read_text()))
    except (OSError,ValueError):return jsonify({})

@bp.get('/api/persona-demo/<jid>/explanation')
def explanation(jid):
    if not re.fullmatch(r'[a-f0-9]{32}',jid):return jsonify(error='记录不存在'),404
    try:
        from engine.persona_demo import explain_result
        saved=json.loads((OUT/(jid+'_audit.json')).read_text())
        branches=saved.get('personas',saved)
        result={}
        for pid in ('ESTJ','INFP'):
            record=branches.get(pid)
            if record:
                result[pid]=record['explanation'] if record.get('model')=='kev-0.8b' else explain_result(record.get('original_full_chain',record))
        return jsonify(result)
    except (OSError,ValueError,TypeError,KeyError):return jsonify(error='本次配置记录尚未保存'),404
