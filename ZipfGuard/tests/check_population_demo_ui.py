"""Verify the 1000-account UI using normal WebAuthn and extension login."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from services.common import bootstrap
from services.common.store import Store
from ops.open_ready_demo import browser_path
from playwright.sync_api import sync_playwright
import httpx,json,secrets,time
RUNTIME=ROOT/'tool_runtime';PRIVATE=RUNTIME/'population-1000-client';OUT=ROOT/'reports/tool_population_1000_20261012'
cfg=json.loads((RUNTIME/'installation.json').read_text('utf-8'))
fixture=json.loads((PRIVATE/'client-state.json').read_text('utf-8'))
store=Store(json.loads((RUNTIME/'admin/config.json').read_text('utf-8'))['store'])
state=store.get('site','state')
while state['simulation']['status']!='completed':
    time.sleep(3);state=store.get('site','state')
identity='population-1000-ui-auditor';credential_path=PRIVATE/'ui-auditor-passkey.json'
checks={};captured={};errors=[]
with sync_playwright() as p:
    context=p.chromium.launch_persistent_context(str(PRIVATE/'ui-check-browser'),executable_path=str(browser_path()),headless=True,
        args=['--headless=new','--ignore-certificate-errors',f"--disable-extensions-except={ROOT/'apps/user-assistant/dist'}",f"--load-extension={ROOT/'apps/user-assistant/dist'}"],
        ignore_https_errors=True,viewport={'width':1440,'height':1000})
    page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
    def capture(response):
        if response.url.endswith('/auth/verify') and response.status==200:captured.update(response.json())
        if response.url==cfg['admin_api']+'/dashboard' and response.status==200:captured['dashboard']=response.json()
    page.on('response',capture);page.goto(cfg['admin_api'])
    cdp=context.new_cdp_session(page);cdp.send('WebAuthn.enable')
    auth=cdp.send('WebAuthn.addVirtualAuthenticator',{'options':{'protocol':'ctap2','transport':'internal','hasResidentKey':True,'hasUserVerification':True,'isUserVerified':True,'automaticPresenceSimulation':True}})['authenticatorId']
    if credential_path.exists():
        for credential in json.loads(credential_path.read_text('utf-8'))['credentials']:cdp.send('WebAuthn.addCredential',{'authenticatorId':auth,'credential':credential})
        page.locator('input').first.fill(identity);page.get_by_role('button',name='使用通行密钥登录',exact=True).click()
    else:
        invitation=secrets.token_urlsafe(32)
        store.put('invitations',invitation,{'identifier':identity,'role':'auditor','expires':time.time()+120})
        page.get_by_role('button',name='首次安装 / 接受邀请',exact=True).click()
        page.locator('input').nth(0).fill(identity);page.locator('input').nth(1).fill(invitation)
        page.get_by_role('button',name='创建通行密钥',exact=True).click()
    page.get_by_role('heading',name='运行总览',exact=True).wait_for(timeout=30000)
    page.get_by_role('heading',name='已有账户改密进度',exact=True).wait_for()
    credential_path.write_text(json.dumps({'credentials':cdp.send('WebAuthn.getCredentials',{'authenticatorId':auth})['credentials']}),encoding='utf-8')
    page.screenshot(path=str(OUT/'admin-overview.png'),full_page=True)
    data=captured['dashboard'];raw=json.dumps(data,ensure_ascii=False)
    checks['dashboard_1000_accounts']=len(data['accounts'])==1000
    checks['simulation_100_80_20']=(data['simulation']['notified'],data['simulation']['completed'],data['simulation']['pending'])==(100,80,20)
    checks['administrator_no_password_strings']=not any(record['password'] in raw for record in fixture['accounts'].values())
    checks['administrator_no_shares']=all('shares' not in row and 'structure' not in row for row in data['accounts'].values())
    page.get_by_role('button',name='受保护画像',exact=True).click()
    page.get_by_role('heading',name='结构内匿名频次',exact=True).wait_for()
    page.screenshot(path=str(OUT/'admin-profile.png'),full_page=True)
    checks['protected_profile_visible']=True
    target=next(a for a,row in data['accounts'].items() if a in fixture['accounts'] and row.get('task') and row['task']['status']=='pending')
    page.get_by_role('button',name='账户与任务',exact=True).click()
    page.get_by_placeholder('按账户标识筛选').fill(target)
    page.get_by_role('cell',name='待处理',exact=True).wait_for()
    page.screenshot(path=str(OUT/'admin-account-pending.png'),full_page=True)
    user=context.new_page();user.on('pageerror',lambda e:errors.append(str(e)))
    user.goto('chrome-extension://'+cfg['extension_id']+'/index.html')
    user.get_by_role('heading',name='口令助手',exact=True).wait_for(timeout=20000)
    user.get_by_text('适用于新注册与修改口令',exact=True).wait_for()
    user.get_by_role('button',name='私密登录',exact=True).click()
    user.locator('#account').fill(target);user.locator('#password').fill(fixture['accounts'][target]['password'])
    user.get_by_role('button',name='私密登录',exact=True).last.click()
    user.get_by_text('操作完成',exact=True).wait_for(timeout=120000)
    user.get_by_role('button',name='我的任务',exact=True).click()
    user.get_by_text('待处理',exact=True).wait_for()
    user.get_by_text('长度至少 15 字符',exact=False).wait_for()
    user.screenshot(path=str(OUT/'user-pending-task.png'),full_page=True)
    checks['extension_pending_task_matches_admin']=True
    with httpx.Client(verify=cfg['ca'],trust_env=False,timeout=20) as http:
        headers={'Authorization':'Bearer '+captured['session']}
        body={'op':'assign_plan','params':{'quota':20,'total_cap':200,'fragments':[3]},'account':None,'policy_version':data['policy_version'],'account_version':0}
        checks['auditor_cannot_assign']=http.post(cfg['admin_api']+'/jobs',json=body,headers=headers).status_code==403
        checks['admin_session_rejected_by_user_api']=http.get(cfg['user_api']+'/me',headers=headers).status_code==401
    checks['no_page_errors']=not errors
    result={'passed':all(checks.values()),'checks':checks,'page_errors':errors,'synthetic_pending_account':target,'scope':'real browser UI, ordinary WebAuthn and real extension login'}
    (OUT/'ui-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    context.close()
    print(json.dumps(result,ensure_ascii=False),flush=True)
    assert result['passed']
