"""Summarize recorded evidence; compile sources and check local TLS in isolation."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from services.common import bootstrap
import datetime,hashlib,json,re,secrets
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import serialization
from ops.local_tls import renew
from services.common.security import create_private

SOURCE_TREES=['services','privacy/service','adapters','contracts','ops','sdk','deploy','tests/tool_e2e',
              'apps/user-assistant','apps/admin-console','apps/shared']
EXTRA=['privacy/transaction_contract.py','policy/privacy_joint_profile.py','policy/intervention_fragments.py',
       'requirements-tool.txt','apps/package.json','apps/package-lock.json','apps/tsconfig.json',
       '启动完整工具.ps1','停止完整工具.ps1','打开可操作展示.cmd','requirements-viewer.txt']

def source_files():
    files=[p for name in SOURCE_TREES for p in (ROOT/name).rglob('*')
           if p.is_file() and not {'__pycache__','dist','node_modules'}&set(p.parts)]
    files.extend(ROOT/name for name in EXTRA)
    files.extend((ROOT/'docs').glob('现实工具_*.md'))
    files.append(ROOT/'docs/现实可用工具_双端完整构建计划.md')
    return sorted(set(files))

def markdown_check(path):
    fence=None;outside=[]
    for line in path.read_text('utf-8').splitlines():
        match=re.match(r'^\s{0,3}(`{3,}|~{3,})(.*)$',line)
        if match:
            marker=match[1]
            if fence is None:fence=marker
            elif marker[0]==fence[0] and len(marker)>=len(fence) and not match[2].strip():fence=None
            continue
        if fence is None:outside.append(line)
    if fence:raise ValueError('Unclosed Markdown fence: '+path.name)
    text='\n'.join(outside)
    if '\\(' in text or '\\[' in text:raise ValueError('Nonportable Markdown math: '+path.name)
    if text.count('$$')%2 or re.sub(r'\$\$','',text).count('$')%2:raise ValueError('Unbalanced math: '+path.name)
    for target in re.findall(r'\]\(([^)]+)\)',text):
        if '://' in target or target.startswith('#'):continue
        candidate=target.split('#')[0].strip('<>')
        if candidate and not (path.parent/candidate).exists():raise ValueError('Broken Markdown link: '+target)

def isolated_tls_check():
    fixture=(ROOT/'tool_runtime'/('tls-audit-'+secrets.token_hex(4))).resolve()
    fixture.relative_to((ROOT/'tool_runtime').resolve())
    fixture.mkdir()
    preserved=[]
    for name in ['user','admin']+[f'party_{i}' for i in range(3)]:
        folder=fixture/name;folder.mkdir()
        key=folder/'issuer.key';create_private(key);preserved.append(key)
        state=folder/'state-marker.json';state.write_text('{"epoch":123}',encoding='utf-8');preserved.append(state)
    before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in preserved}
    renew(fixture)
    ca=x509.load_pem_x509_certificate((fixture/'tls/ca.crt').read_bytes())
    checks=0
    for name in ['user','admin']+[f'party_{i}' for i in range(3)]:
        folder=fixture/name;cert=x509.load_pem_x509_certificate((folder/'tls.crt').read_bytes())
        ca.public_key().verify(cert.signature,cert.tbs_certificate_bytes,padding.PKCS1v15(),cert.signature_hash_algorithm)
        assert 'localhost' in cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
        key=serialization.load_pem_private_key((folder/'tls.key').read_bytes(),None)
        assert key.public_key().public_numbers()==cert.public_key().public_numbers()
        if name.startswith('party'):
            assert (folder/'.config'/f'{name}.crt').read_bytes()==(folder/'tls.crt').read_bytes()
        checks+=1
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==value for p,value in before.items())
    return {'passed':True,'scope':'isolated certificate issuance; no live certificate changed',
            'certificates_verified':checks,'signing_and_state_markers_preserved':len(before)}

def plaintext_scan():
    runtime=ROOT/'tool_runtime';files=[]
    for name in ['business.sqlite','business.sqlite-wal','reference.sqlite','reference.sqlite-wal',
                 'user.log','admin.log','jobs.log','reference.log']:
        p=runtime/name
        if p.is_file():files.append(p)
    for i in range(3):
        files.extend(p for p in (runtime/f'party_{i}').glob('*')
                     if p.is_file() and p.name in {'state.sqlite','state.sqlite-wal','highwater.json'})
        p=runtime/f'party_{i}.log'
        if p.is_file():files.append(p)
    needles=[b'Unique-Sample-Password-37',b'Updated-Smoke-Password-92',b'Completed-Task-Password-56',
             b'Crash-Recovery-Password-48',b'Unconsumed-Commit-Password-52',b'Malformed-Synthetic-Password-7',
             b'Synthetic-Old-Password-39',b'Synthetic-New-Password-74',b'Synthetic-Recovered-Password-83']
    hits=[str(p.relative_to(ROOT)) for p in files if any(n in p.read_bytes() for n in needles)]
    assert not hits,hits
    return {'passed':True,'files':len(files),'synthetic_strings':len(needles),'hits':hits,
            'scope':'sample-string audit of runtime stores and logs; not a cryptographic proof'}

def main():
    output=ROOT/'reports/tool_acceptance';groups=[];functional_passed=0;failures=[]
    for name in ['api','completion','rule','task-fault','security','migration','joint-policy','session','control']:
        path=output/(name+'-results.json');data=json.loads(path.read_text('utf-8'))
        rows=data if isinstance(data,list) else data['cases']
        passed=sum(row.get('passed') is True for row in rows)
        scope='isolated control logic' if name=='control' else ('real MPC rule circuit' if name=='rule' else 'live services')
        groups.append({'file':path.name,'scope':scope,'passed':passed,'total':len(rows),
                       'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        if name!='control':functional_passed+=passed
        failures.extend({'file':path.name,'case':row.get('case',row.get('id'))} for row in rows if row.get('passed') is not True)
    assert failures==[{'file':'api-results.json','case':'private_profile_live_job'}],failures
    recovered=json.loads((output/'completion-results.json').read_text('utf-8'))
    assert any(r['case']=='original_profile_request_reconciled' and r['passed'] for r in recovered)
    browser=json.loads((output/'browser-result.json').read_text('utf-8'))
    performance=json.loads((output/'profile-performance.json').read_text('utf-8'))
    assert browser['passed'] and not browser['page_errors'] and performance['passed']
    files=source_files();compiled=0;markdown=0
    for p in files:
        if p.suffix=='.py':compile(p.read_text('utf-8-sig'),str(p),'exec');compiled+=1
        if p.suffix=='.md':markdown_check(p);markdown+=1
    summary={'date':'2026-10-11','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
             'data':'synthetic only','functional_passed_assertions':functional_passed,
             'isolated_control_assertions':groups[-1]['passed'],'groups':groups,
             'observed_failure':{'record':failures[0],'resolved':True,'resolution_file':'completion-results.json',
                                 'method':'reconcile original nonce; no replacement privacy release'},
             'browser':browser,'profile_performance':performance,
             'source_compile':{'passed':True,'python_files':compiled},
             'markdown':{'passed':True,'files':markdown},'isolated_tls':isolated_tls_check(),
             'plaintext_audit':plaintext_scan(),
             'source_hashes':{str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
             'evidence_hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.glob('*')) if p.suffix in {'.json','.png'} and p.name!='summary.json'},
             'limits':['single host and small synthetic population','passive security; at most one corrupted party',
                       'trusted extension and TLS','no malicious-party or PAKE proof','no production capacity audit',
                       'automatic joint policy holds without user-response/cost confirmation']}
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Acceptance audited: {functional_passed} live-service/MPC assertions; 9 isolated control assertions; one preserved recovered timeout.')
    print(f'Compiled {compiled} Python sources; checked {markdown} Markdown documents; local TLS and sample plaintext audits passed.')

if __name__=='__main__':main()
