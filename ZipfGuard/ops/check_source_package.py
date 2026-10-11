"""Offline source-package smoke check, isolated from the live installation."""
from pathlib import Path
import datetime,hashlib,json,os,secrets,subprocess,sys,zipfile
ROOT=Path(__file__).resolve().parents[1]

def main():
    archive=ROOT/'delivery/ZipfGuard-双端完整工具源码.zip'
    fixture=(ROOT/'tool_runtime'/('source-package-check-'+secrets.token_hex(4))).resolve()
    fixture.relative_to((ROOT/'tool_runtime').resolve());fixture.mkdir()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.namelist():(fixture/member).resolve().relative_to(fixture)
        bundle.extractall(fixture)
    env=os.environ.copy()
    # Reuse installed libraries only; imported application sources must come
    # from the extracted package, never the current workspace's Python modules.
    env['PYTHONPATH']=str(ROOT/'tool_deps')
    script="""
from pathlib import Path
from services.common.business import Business
from services.jobs.runner import Runner
from privacy.service.compute import Computer
from sdk.zipfguard import authenticate
from ops.local_tls import renew
import services.common.business, adapters.live_engines, policy.privacy_joint_profile
root=Path.cwd().resolve()
for module in [services.common.business,adapters.live_engines,policy.privacy_joint_profile]:
    Path(module.__file__).resolve().relative_to(root)
for p in root.rglob('*.py'):
    if '__pycache__' not in p.parts:compile(p.read_text('utf-8-sig'),str(p),'exec')
print('Extracted source imports and compilation passed.')
"""
    subprocess.run([sys.executable,'-c',script],cwd=fixture,env=env,check=True)
    for name in ['provision_tool.py','prepare_extension.py','provision_migration.py']:
        subprocess.run([sys.executable,str(fixture/'ops'/name)],cwd=fixture,env=env,check=True,stdout=subprocess.DEVNULL)
    cfg=json.loads((fixture/'tool_runtime/installation.json').read_text('utf-8'))
    assert cfg['runtime']==str(fixture/'tool_runtime')
    assert (fixture/'tool_runtime/administrator-installation-code.txt').is_file()
    assert (fixture/'tool_runtime/signed-station.json').is_file()
    original=json.loads((ROOT/'tool_runtime/installation.json').read_text('utf-8'))
    assert cfg['node_keys']!=original['node_keys'] and cfg['extension_id']!=original['extension_id']
    result={'passed':True,'checked_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'source_archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
            'scope':'offline imports, compile and fresh trust provisioning from extracted sources; installed dependencies reused',
            'fresh_signing_keys_and_extension_identity':True,'private_invitation_not_printed':True,
            'full_new_machine_install_or_cross_host_deployment_tested':False}
    target=ROOT/'delivery/source-package-check.json';target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    manifest=ROOT/'delivery/manifest.json';data=json.loads(manifest.read_text('utf-8'))
    data['source_package_check']={'path':str(target.relative_to(ROOT)),'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                                 'source_archive_sha256':result['source_archive_sha256'],'passed':True}
    manifest.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Fresh isolated source provisioning passed; new credentials generated only inside private test runtime.')

if __name__=='__main__':main()
