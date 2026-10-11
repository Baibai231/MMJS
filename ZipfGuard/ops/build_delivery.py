"""Package separate frontends and secret-free full tool sources."""
from pathlib import Path
import zipfile,hashlib,json,datetime,re
ROOT=Path(__file__).resolve().parents[1]
def archive(path,files,base):
 with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as output:
  for file in sorted(files):
   if not file.is_file():raise ValueError('Archive input must be a file')
   output.write(file,str(file.relative_to(base)).replace('\\','/'))
 return {'path':str(path.relative_to(ROOT)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'files':len(files)}
def main():
 delivery=ROOT/'delivery';delivery.mkdir(exist_ok=True);results=[]
 for name in ['user-assistant','admin-console']:
  folder=ROOT/'apps'/name/'dist'
  if not (folder/'index.html').is_file():raise ValueError('Build both applications before packaging')
  results.append(archive(delivery/(name+'.zip'),[p for p in folder.rglob('*') if p.is_file()],folder))
 files=[]
 for name in ['services','privacy/service','adapters','contracts','ops','sdk','deploy','tests/tool_e2e']:
  files.extend(p for p in (ROOT/name).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
 for name in ['apps/user-assistant','apps/admin-console','apps/shared']:
  files.extend(p for p in (ROOT/name).rglob('*') if p.is_file() and 'dist' not in p.parts and 'node_modules' not in p.parts)
 files.extend(ROOT/name for name in ['privacy/transaction_contract.py','policy/privacy_joint_profile.py','policy/intervention_fragments.py','requirements-tool.txt','requirements-viewer.txt','apps/package.json','apps/package-lock.json','apps/tsconfig.json','启动完整工具.ps1','停止完整工具.ps1','打开可操作展示.cmd'])
 files.extend(p for p in (ROOT/'docs').glob('现实工具_*.md'))
 files.append(ROOT/'docs/现实可用工具_双端完整构建计划.md')
 files=list(set(files))
 assert not any('tool_runtime' in p.parts or p.suffix in {'.key','.sqlite'} for p in files)
 results.append(archive(delivery/'ZipfGuard-双端完整工具源码.zip',files,ROOT))
 evidence=ROOT/'reports/tool_acceptance'
 expected=['api-results.json','completion-results.json','rule-results.json','task-fault-results.json',
           'security-results.json','migration-results.json','joint-policy-results.json','session-results.json',
           'control-results.json','browser-result.json','profile-performance.json','summary.json',
           'admin-overview.png','admin-profile.png','admin-joint-policy.png',
           'user-assistant.png','user-account.png','user-stations.png']
 results.append(archive(delivery/'ZipfGuard-验收证据.zip',[evidence/name for name in expected],ROOT))
 for result in results:
  with zipfile.ZipFile(ROOT/result['path']) as bundle:
   for entry in bundle.infolist():
    parts=Path(entry.filename).parts
    if any(part in {'tool_runtime','tool_deps','node_modules','.venv'} for part in parts) or Path(entry.filename).suffix in {'.key','.sqlite','.pem','.pfx'}:
     raise ValueError('Runtime secret or dependency found in package')
    if re.search(br'-----BEGIN (?:RSA |EC |ENCRYPTED )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/=]{20,}',bundle.read(entry)):
     raise ValueError('Private key bytes found in package')
 (delivery/'manifest.json').write_text(json.dumps({'created':datetime.datetime.now(datetime.timezone.utc).isoformat(),'artifacts':results,'runtime_secrets_included':False},ensure_ascii=False,indent=2),encoding='utf-8')
 print('Packaged two independent applications, full sources and acceptance evidence; secret audit passed.')
if __name__=='__main__':main()
