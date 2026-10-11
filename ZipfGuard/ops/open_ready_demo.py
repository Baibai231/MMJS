"""Open a ready-to-use LOCAL presentation, using real WebAuthn and extension UI.

The software authenticator is deliberately confined to the private local demo.
No server authentication bypass, shared UI session, or fake dashboard is added.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from services.common import bootstrap
import ctypes,datetime,hashlib,json,os,secrets,time
from ctypes import wintypes
from services.common.store import Store


def browser_path():
    base=Path(os.environ.get('LOCALAPPDATA',''))/'ms-playwright'
    candidates=sorted(base.glob('chromium-*/chrome-win64/chrome.exe'),reverse=True)
    candidates+=sorted(base.glob('chromium-*/chrome-linux/chrome'),reverse=True)
    return next((p for p in candidates if p.is_file()),None)


def main():
    runtime=ROOT/'tool_runtime';demo=runtime/'ready-demo';demo.mkdir(parents=True,exist_ok=True)
    status=demo/'status.json'
    mutex=None
    if os.name=='nt':
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateMutexW.argtypes=[wintypes.LPVOID,wintypes.BOOL,wintypes.LPCWSTR]
        kernel.CreateMutexW.restype=wintypes.HANDLE
        name='Local\\ZipfGuardPresentation-'+hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]
        mutex=kernel.CreateMutexW(None,False,name)
        if ctypes.get_last_error()==183:
            (demo/'reopen.request').write_text('open',encoding='utf-8')
            kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle(mutex)
            return
    def report(state,**details):
        status.write_text(json.dumps({'status':state,'pid':os.getpid(),
                         'updated':datetime.datetime.now(datetime.timezone.utc).isoformat(),**details},ensure_ascii=False,indent=2),encoding='utf-8')
    try:
        report('starting')
        cfg=json.loads((runtime/'installation.json').read_text('utf-8'))
        if cfg.get('local_demo') is not True or cfg['admin_api']!='https://localhost:8532':
            raise RuntimeError('Ready presentation is restricted to the provisioned local demo.')
        browser=browser_path()
        if not browser:raise RuntimeError('Install a Playwright Chromium browser before using the ready presentation.')
        import httpx
        from playwright.sync_api import sync_playwright
        from ops.tool import run
        if not (runtime/'processes.json').exists():run('start',runtime)
        with httpx.Client(verify=cfg['ca'],trust_env=False,timeout=4) as http:
            deadline=time.time()+50
            while time.time()<deadline:
                try:
                    if all(http.get(url+'/health').json().get('ready') for url in cfg['nodes']+[cfg['user_api'],cfg['admin_api'],cfg['reference_site']]):break
                except Exception:pass
                time.sleep(.5)
            else:raise RuntimeError('Local services are not ready. See the local runtime logs.')
        report('opening_browser')
        with sync_playwright() as p:
            context=p.chromium.launch_persistent_context(str(demo/'browser'),executable_path=str(browser),headless=False,
                args=['--ignore-certificate-errors','--no-first-run','--no-default-browser-check',
                      '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1',
                      f"--disable-extensions-except={ROOT/'apps/user-assistant/dist'}",f"--load-extension={ROOT/'apps/user-assistant/dist'}"],
                ignore_https_errors=True,no_viewport=True)
            context.set_default_timeout(15000)
            admin=context.new_page();admin.goto(cfg['admin_api'])
            cdp=context.new_cdp_session(admin);cdp.send('WebAuthn.enable')
            auth=cdp.send('WebAuthn.addVirtualAuthenticator',{'options':{'protocol':'ctap2','transport':'internal',
                'hasResidentKey':True,'hasUserVerification':True,'isUserVerified':True,'automaticPresenceSimulation':True}})['authenticatorId']
            credential_file=demo/'presentation-passkey.json'
            store=Store(json.loads((runtime/'admin/config.json').read_text('utf-8'))['store'])
            if credential_file.exists():
                saved=json.loads(credential_file.read_text('utf-8'));identity=saved['identity']
                for credential in saved['credentials']:cdp.send('WebAuthn.addCredential',{'authenticatorId':auth,'credential':credential})
                row=store.get('administrators',identity)
                if not row or not row['enabled']:raise RuntimeError('The local presentation administrator is disabled; use your own administrator login.')
                admin.locator('input').first.fill(identity)
                admin.get_by_role('button',name='使用通行密钥登录',exact=True).click()
            else:
                identity='local-presenter-'+secrets.token_hex(3)
                invitation=secrets.token_urlsafe(32)
                store.put('invitations',invitation,{'identifier':identity,'role':'owner','expires':time.time()+120})
                admin.get_by_role('button',name='首次安装 / 接受邀请',exact=True).click()
                admin.locator('input').nth(0).fill(identity);admin.locator('input').nth(1).fill(invitation)
                admin.get_by_role('button',name='创建通行密钥',exact=True).click()
            admin.get_by_role('heading',name='运行总览',exact=True).wait_for(timeout=30000)
            def save_credential():
                credentials=cdp.send('WebAuthn.getCredentials',{'authenticatorId':auth})['credentials']
                credential_file.write_text(json.dumps({'identity':identity,'credentials':credentials}),encoding='utf-8')
            save_credential()
            def persist_on_login(response):
                if response.url==cfg['admin_api']+'/auth/verify' and response.status==200:
                    save_credential()
            admin.on('response',persist_on_login)
            user=context.new_page();user.goto('chrome-extension://'+cfg['extension_id']+'/index.html')
            user.get_by_role('heading',name='口令助手',exact=True).wait_for(timeout=20000)
            user.get_by_text('新规则不会锁定原有口令登录',exact=False).wait_for()
            # This public synthetic fixture is entered only into the extension.
            # It is never passed to the administrator page or business service.
            user_logged_in=False
            with httpx.Client(verify=cfg['ca'],trust_env=False,timeout=4) as http:
                version=http.get(cfg['user_api']+'/account-version/integration-smoke').json()['version']
            if version:
                user.get_by_role('button',name='私密登录',exact=True).click()
                user.locator('#account').fill('integration-smoke')
                user.locator('#password').fill('Updated-Smoke-Password-92' if version>1 else 'Unique-Sample-Password-37')
                user.get_by_role('button',name='私密登录',exact=True).last.click()
                try:
                    user.get_by_text('操作完成',exact=True).wait_for(timeout=60000)
                    user_logged_in=True
                except Exception:pass
                user.get_by_role('button',name='口令助手',exact=True).click()
            reference=context.new_page();reference.goto(cfg['reference_site'])
            for page in context.pages:
                if page not in [admin,user,reference] and page.url=='about:blank':page.close()
            output=ROOT/'delivery';output.mkdir(exist_ok=True)
            admin.screenshot(path=str(output/'预览_管理员.png'),full_page=True)
            user.screenshot(path=str(output/'预览_用户助手.png'),full_page=True)
            admin.bring_to_front()
            report('ready',admin_logged_in=True,user_logged_in=user_logged_in,admin_url=cfg['admin_api'],
                   user_url='chrome-extension://'+cfg['extension_id']+'/index.html',reference_url=cfg['reference_site'],
                   authenticator='local software demonstration credential; real WebAuthn verification',
                   interface_sessions_shared=False)
            print('Local administrator and extension are open and ready.',flush=True)
            reopen=demo/'reopen.request'
            while context.pages:
                try:
                    admin.wait_for_timeout(700) if not admin.is_closed() else context.pages[0].wait_for_timeout(700)
                    if reopen.exists():
                        reopen.unlink()
                        if not admin.is_closed():admin.bring_to_front()
                    # A refresh or expiry returns to the normal login form.
                    # Re-authentication uses the same private local authenticator.
                    if not admin.is_closed() and admin.get_by_role('heading',name='登录管理台',exact=True).count():
                        admin.locator('input').first.fill(identity)
                        admin.get_by_role('button',name='使用通行密钥登录',exact=True).click()
                        admin.get_by_role('heading',name='运行总览',exact=True).wait_for(timeout=30000)
                        save_credential()
                except Exception:
                    if not context.pages:break
                    time.sleep(.5)
            report('closed')
    except Exception as exc:
        report('error',error=type(exc).__name__,message=str(exc)[:240])
        raise
    finally:
        if mutex:
            kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle(mutex)


if __name__=='__main__':main()
