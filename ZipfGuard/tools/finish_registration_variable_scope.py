"""Finish report export after the single authorized experiment completes."""
from pathlib import Path
import json, subprocess, sys, time, shutil, hashlib
root = Path(sys.argv[1]).resolve()
workspace = Path(sys.argv[2]).resolve()
assert root.name == 'registration_variable_scope_20261012'
assert root.is_relative_to(Path('C:/Users/jlx51/.codex/visualizations/2026/10/11/01a12a85-b74d-7740-89ee-d9101382a8d9').resolve())
report = workspace/'ZipfGuard/tools/report_registration_variable_scope.py'
status_path = root/'report_status.json'
def write_status(value):
    status_path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
write_status({'stage':'waiting_for_complete_experiment','no_training_in_report':True})
for _ in range(2160):
    try:
        status=json.loads((root/'status.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):
        time.sleep(10);continue
    if status['stage']=='failed':
        write_status({'stage':'experiment_failed','details':status.get('failures',[])})
        raise SystemExit(1)
    if status['stage']=='complete':
        break
    time.sleep(10)
else:
    write_status({'stage':'waiting_timeout'});raise SystemExit(1)
write_status({'stage':'auditing_and_exporting'})
with (root/'report.stdout.log').open('w',encoding='utf-8') as out, (root/'report.stderr.log').open('w',encoding='utf-8') as err:
    completed=subprocess.run([sys.executable,str(report),str(root)],cwd=workspace,stdout=out,stderr=err)
if completed.returncode:
    write_status({'stage':'audit_or_report_failed','returncode':completed.returncode});raise SystemExit(completed.returncode)
public = workspace/'ZipfGuard/reports/registration_variable_scope_20261012'
public.mkdir(exist_ok=True)
shutil.copytree(root/'deliverables',public/'deliverables',dirs_exist_ok=True)
for name in ['manifest.json','status.json','audit.json','revision_protocol.json']:
    shutil.copy2(root/name,public/name)
for p in (root/'deliverables').iterdir():
    if p.is_file():
        assert hashlib.sha256(p.read_bytes()).digest()==hashlib.sha256((public/'deliverables'/p.name).read_bytes()).digest()
write_status({'stage':'complete','public_deliverables':str(public/'deliverables'),'audit':'passed','all_files_sha256_verified':True})
print('Complete audited result exported to',public/'deliverables',flush=True)
