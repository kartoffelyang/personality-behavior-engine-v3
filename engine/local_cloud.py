"""Local-only provider settings; secrets are never returned to the browser."""
import json,os,tempfile,threading
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit
PATH=Path(__file__).resolve().parents[1]/'.local-cloud.json'
LOCK=threading.RLock()
DEFAULT_BASE='https://www.fucheers.top/v1'
DEFAULT_MODEL='claude-opus-5-5'


def load():
    try:
        data=json.loads(PATH.read_text())
        return data if isinstance(data,dict) else {}
    except (OSError,ValueError):return {}


def normalize_base(value):
    if not isinstance(value,str):raise ValueError('请填写API Base URL')
    u=urlsplit(value.strip())
    if u.scheme not in {'http','https'} or not u.hostname or u.username or u.password or u.query or u.fragment:raise ValueError('请填写不含账号、参数的HTTP或HTTPS接口地址')
    path=u.path.rstrip('/')
    if path.endswith('/chat/completions'):path=path[:-len('/chat/completions')]
    if not path:path='/v1'
    return urlunsplit((u.scheme,u.netloc,path,'',''))


def save(base_url,model,api_key=None,observation_timeout_sec=None,wording_timeout_sec=None):
    base=normalize_base(base_url)
    if not isinstance(model,str) or not model.strip() or len(model)>160 or any(c in model for c in '\r\n'):raise ValueError('请填写合法的模型名称')
    if api_key is not None and (not isinstance(api_key,str) or any(c in api_key for c in '\r\n')):raise ValueError('API Key格式不正确')
    with LOCK:
        old=load()
        # Never send a key from the previous provider to a different host.
        key=api_key.strip() if api_key and api_key.strip() else old.get('api_key','') if old.get('base_url')==base else ''
        data={'base_url':base,'model':model.strip(),'api_key':key}
        for field,value in [('observation_timeout_sec',observation_timeout_sec),('wording_timeout_sec',wording_timeout_sec)]:
            if value is None and old.get('model')==model.strip():value=old.get(field)
            if value is not None:
                if type(value) is not int or not 10<=value<=180:raise ValueError('请求时限需为10至180秒的整数')
                data[field]=value
        PATH.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix='.local-cloud-',dir=PATH.parent)
        try:
            os.fchmod(fd,0o600)
            with os.fdopen(fd,'w') as f:json.dump(data,f,ensure_ascii=False,indent=2)
            os.replace(tmp,PATH)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
    return public()


def public():
    d=load()
    return {'base_url':d.get('base_url',DEFAULT_BASE),'model':d.get('model',DEFAULT_MODEL),'key_configured':bool(d.get('api_key')),'observation_timeout_sec':timeout_for('observation',d),'wording_timeout_sec':timeout_for('wording',d)}


def preparation_model(default):return load().get('model') or default


def timeout_for(stage,data=None):
    d=load() if data is None else data
    slow='opus' in str(d.get('model','')).lower()
    default=(90 if slow else 25) if stage=='observation' else (45 if slow else 15)
    value=d.get(stage+'_timeout_sec',default)
    return value if type(value) is int and 10<=value<=180 else default
