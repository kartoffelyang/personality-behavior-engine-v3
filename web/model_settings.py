"""Loopback settings form. The user enters their own key; GET never exposes it."""
import secrets
from urllib.parse import urlsplit
from flask import Blueprint,jsonify,render_template,request,abort
from engine import local_cloud
bp=Blueprint('model_settings',__name__)
TOKEN=secrets.token_urlsafe(32)


@bp.before_request
def local_only():
    if request.remote_addr not in {'127.0.0.1','::1'}:abort(403)
    if request.method=='POST':
        origin=request.headers.get('Origin')
        if origin and origin.rstrip('/')!=request.host_url.rstrip('/'):abort(403)
        if not secrets.compare_digest(request.headers.get('X-Local-Settings-Token',''),TOKEN):abort(403)


@bp.after_request
def private_response(response):
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    return response


@bp.get('/settings/models')
def page():return render_template('model_settings.html',settings=local_cloud.public(),token=TOKEN)


@bp.get('/api/model-settings')
def get():return jsonify(local_cloud.public())


@bp.post('/api/model-settings')
def save():
    data=request.get_json(silent=True)
    if not isinstance(data,dict):return jsonify(error='请求格式错误'),400
    try:
        public=local_cloud.save(data.get('base_url'),data.get('model'),data.get('api_key'),data.get('observation_timeout_sec'),data.get('wording_timeout_sec'))
        return jsonify(**public,message='已保存，对下一次模型请求生效。' if public['key_configured'] else '地址与模型已保存，请输入API Key后再生成。')
    except ValueError as exc:return jsonify(error=str(exc)),400


@bp.post('/api/model-settings/test')
def test():
    if not local_cloud.load().get('api_key'):return jsonify(error='请先输入API Key并保存。'),400
    from engine.llm_router import get_llm
    from engine.kev_final_reply import cloud_failure_notice
    try:
        client=get_llm('openai');client.timeout=12;client.total_timeout=12;client.idle_timeout=10;client.stream=True
        reply=client.chat([{'role':'user','content':'仅回复OK。'}],max_tokens=40)
        if not isinstance(reply,str) or not reply.strip():raise ValueError('empty_response')
        return jsonify(ok=True,message='连接成功，已验证当前地址、Key与模型能够返回文本。',model=client.model)
    except Exception as exc:
        status=getattr(exc,'status_code',None)
        message=cloud_failure_notice(exc,'连接测试') or ('连接失败：HTTP '+str(status)+'，请检查模型名称与账户权限。' if status else '连接测试未完成，请检查地址、Key和模型名称。')
        return jsonify(ok=False,error=message),502
