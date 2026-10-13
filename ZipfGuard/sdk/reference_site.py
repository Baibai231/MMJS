"""A separate relying-party sample. It never renders a password input."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from services.common import bootstrap
import argparse,json,secrets,time
from fastapi import FastAPI,Request,HTTPException
from fastapi.responses import HTMLResponse
import uvicorn
from sdk.zipfguard import authenticate,migration_authorization
from services.common.store import Store
from services.common.security import fingerprint_public

def create_app(cfg):
    app=FastAPI(docs_url=None,redoc_url=None);store=Store(str(Path(cfg['runtime'])/'reference.sqlite'))
    @app.get('/health')
    def health():return {'ready':True,'service':'reference-site'}
    @app.get('/',response_class=HTMLResponse)
    def home():
        return (ROOT/'sdk/reference-site.html').read_text('utf-8')
    @app.get('/client.js')
    def client_js():
        from fastapi.responses import Response
        return Response((ROOT/'sdk/reference-client.js').read_text('utf-8'),media_type='text/javascript')
    @app.post('/challenge')
    def challenge():
        nonce=secrets.token_urlsafe(24);store.put('challenges',nonce,{'expires':time.time()+300})
        return {'site':cfg['site'],'nonce':nonce}
    @app.post('/migration-authorization')
    def migration(request:Request):
        session=store.get('sessions',request.cookies.get('site_session',''))
        if not session or session['expires']<time.time():raise HTTPException(401,'先通过本站既有授权登录')
        from services.common.security import load_private
        return {'account':session['account'],'ticket':migration_authorization(load_private(Path(cfg['runtime'])/'reference/migration.key'),cfg['site'],session['account']),
                'expires_in':300,'notice':'该证明不允许覆盖已经加入三方隐私服务的账户'}
    @app.post('/authenticate')
    async def auth(request:Request):
        body=await request.json()
        if set(body)!={'events','context','challenge'}:raise HTTPException(400,'未知字段')
        with store.transaction() as conn:
            challenge=store.get('challenges',body['challenge'],connection=conn)
            if not challenge or challenge['expires']<time.time() or body['context']['nonce']!=body['challenge']:raise HTTPException(403,'网站挑战无效或已使用')
            try:result=authenticate(body['events'],cfg['node_keys'],cfg['site'],body['challenge'],fingerprint_public(body['context']))
            except Exception:raise HTTPException(403,'认证证明无效')
            import httpx
            try:
                current=httpx.get(cfg['user_api']+'/account-version/'+result['account'],verify=cfg['ca'],timeout=5).json()
                if current['version']!=result['version'] or not current['enabled']:raise ValueError()
            except Exception:raise HTTPException(403,'账户凭据已变更或无法核对')
            store.remove('challenges',body['challenge'],conn)
            session=secrets.token_urlsafe(32);store.put('sessions',session,{**result,'expires':time.time()+900},conn)
        from fastapi.responses import JSONResponse
        response=JSONResponse({'account':result['account'],'status':'authenticated'})
        response.set_cookie('site_session',session,httponly=True,secure=True,samesite='strict',max_age=900)
        return response
    return app

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args();cfg=json.loads(Path(args.config).read_text('utf-8'))
    folder=Path(cfg['runtime'])/'user'
    uvicorn.run(create_app(cfg),host='127.0.0.1',port=8560,ssl_certfile=str(folder/'tls.crt'),ssl_keyfile=str(folder/'tls.key'),log_level='warning',access_log=False)
