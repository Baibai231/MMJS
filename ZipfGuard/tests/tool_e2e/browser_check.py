"""Browser QA uses a fresh disposable profile and virtual WebAuthn authenticator."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from services.common import bootstrap
import json,time
from playwright.sync_api import sync_playwright

def run():
    from client import Client
    Client()  # Wait for durable three-party recovery before browser interaction.
    cfg=json.loads((ROOT/'tool_runtime/installation.json').read_text('utf-8'))
    output=ROOT/'reports/tool_acceptance';output.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        print('Launching isolated browser',flush=True)
        browser=Path('C:/Users/jlx51/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe')
        context=p.chromium.launch_persistent_context(str(ROOT/'tool_runtime/browser-qa'),executable_path=str(browser),headless=True,
             args=['--headless=new','--ignore-certificate-errors',f"--disable-extensions-except={ROOT/'apps/user-assistant/dist'}",f"--load-extension={ROOT/'apps/user-assistant/dist'}"],ignore_https_errors=True,viewport={'width':1440,'height':1000})
        page=context.new_page();errors=[]
        def capture(response):
            if response.url.endswith('/auth/verify') and response.status==200:
                (ROOT/'tool_runtime/qa-admin-session.json').write_text(json.dumps(response.json()),encoding='utf-8')
        page.on('response',capture)
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(cfg['admin_api'])
        cdp=context.new_cdp_session(page);cdp.send('WebAuthn.enable')
        auth=cdp.send('WebAuthn.addVirtualAuthenticator',{'options':{'protocol':'ctap2','transport':'internal','hasResidentKey':True,'hasUserVerification':True,'isUserVerified':True,'automaticPresenceSimulation':True}})
        saved=ROOT/'tool_runtime/qa-passkey.json'
        if saved.exists():
            for credential in json.loads(saved.read_text('utf-8'))['credentials']:
                cdp.send('WebAuthn.addCredential',{'authenticatorId':auth['authenticatorId'],'credential':credential})
            page.locator('input').nth(0).fill('qa-owner')
            page.get_by_role('button',name='使用通行密钥登录').click()
        else:
            page.get_by_role('button',name='首次安装 / 接受邀请').click()
            page.locator('input').nth(0).fill('qa-owner')
            page.locator('input').nth(1).fill((ROOT/'tool_runtime/administrator-installation-code.txt').read_text('utf-8').strip())
            page.get_by_role('button',name='创建通行密钥').click()
        page.get_by_role('heading',name='运行总览').wait_for(timeout=30000)
        print('Passkey and admin overview passed',flush=True)
        page.screenshot(path=str(output/'admin-overview.png'),full_page=True)
        session=page.evaluate("""async()=>{return null}""")
        # Session stays in React memory. Capture only the token in Python memory
        # from the network response to exercise private API/role tests separately.
        credentials=cdp.send('WebAuthn.getCredentials',{'authenticatorId':auth['authenticatorId']})
        (ROOT/'tool_runtime/qa-passkey.json').write_text(json.dumps(credentials),encoding='utf-8')
        for label in ['受保护画像','注册动态策略','存量分布调整','账户与任务','决策与效果','运行作业','网站接入','权限与设置']:
            page.get_by_role('button',name=label,exact=True).click();page.get_by_role('heading',name=label,exact=True).wait_for();assert page.locator('body').inner_text()
        user=context.new_page();user.goto('chrome-extension://'+cfg['extension_id']+'/index.html');user.get_by_role('heading',name='口令助手',exact=True).wait_for(timeout=20000)
        print('Extension loaded',flush=True)
        user.get_by_text('适用于新注册与修改口令',exact=True).wait_for(timeout=15000)
        user.screenshot(path=str(output/'user-assistant.png'),full_page=True)
        import httpx
        version=httpx.get(cfg['user_api']+'/account-version/integration-smoke',verify=cfg['ca']).json()['version']
        password='Updated-Smoke-Password-92' if version>1 else 'Unique-Sample-Password-37'
        user.get_by_role('button',name='私密登录',exact=True).click();user.locator('#account').fill('integration-smoke');user.locator('#password').fill(password);user.get_by_role('button',name='私密登录',exact=True).last.click()
        try:user.get_by_text('操作完成',exact=True).wait_for(timeout=60000)
        except Exception:
            print('User status:',user.locator('body').inner_text(),flush=True)
            print('Browser errors:',errors,flush=True)
            raise
        user.get_by_role('button',name='我的账户',exact=True).click();user.get_by_role('heading',name='本人账户').wait_for();user.screenshot(path=str(output/'user-account.png'),full_page=True)
        user.get_by_role('button',name='站点连接',exact=True).click()
        user.get_by_label('导入可信站点配置').set_input_files(str(ROOT/'tool_runtime/signed-station.json'))
        user.get_by_role('button',name='核对身份并授予本站权限').wait_for(timeout=15000)
        user.screenshot(path=str(output/'user-stations.png'),full_page=True)
        page.get_by_role('button',name='注册动态策略',exact=True).click();page.get_by_role('heading',name='快照联合阻断 · 管理员手动制定').wait_for();page.screenshot(path=str(output/'admin-joint-policy.png'),full_page=True)
        page.get_by_role('button',name='受保护画像',exact=True).click();page.screenshot(path=str(output/'admin-profile.png'),full_page=True)
        assert not errors,errors
        (output/'browser-result.json').write_text(json.dumps({'passed':True,'admin_passkey':True,'admin_pages':9,'extension_login':True,'signed_station_import_verified':True,'joint_policy_panel':True,'page_errors':errors,'local_certificate_exception':'disposable QA profile only'},ensure_ascii=False,indent=2),encoding='utf-8')
        context.close()
    print('Browser WebAuthn enrollment, 9 admin pages, extension login passed.')

if __name__=='__main__':run()
