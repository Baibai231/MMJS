"""Local launch, stop, health and independent-party backup commands."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from services.common import bootstrap
import argparse,json,os,signal,sqlite3,subprocess,time,shutil

def recorded_process_is_running(process, runtime):
    """Check identity before trusting or terminating a saved PID."""
    pid = int(process['pid'])
    if pid <= 0:
        return False
    name = process['name']
    if name in {'party_0', 'party_1', 'party_2'}:
        script = ROOT / 'privacy/service/node.py'
        config = Path(runtime) / name / 'config.json'
    elif name in {'user', 'admin'}:
        script = ROOT / 'services/run.py'
        config = Path(runtime) / name / 'config.json'
    elif name == 'reference':
        script = ROOT / 'sdk/reference_site.py'
        config = Path(runtime) / 'installation.json'
    elif name == 'jobs':
        script = ROOT / 'services/jobs/runner.py'
        config = Path(runtime) / 'admin/config.json'
    else:
        raise RuntimeError('Unknown saved service name; refusing to use its PID.')
    if os.name == 'nt':
        query = (f"Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}' "
                 "| Select-Object -ExpandProperty CommandLine")
        result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', query],
                                capture_output=True, text=True, timeout=15,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError('Unable to verify saved process identity.')
        command = result.stdout
    else:
        result = subprocess.run(['ps', '-p', str(pid), '-o', 'args='],
                                capture_output=True, text=True, timeout=10)
        if result.returncode == 1:
            return False
        if result.returncode:
            raise RuntimeError('Unable to verify saved process identity.')
        command = result.stdout
    normalize = lambda value: str(value).replace(chr(92), '/').casefold()
    command = normalize(command)
    return normalize(script) in command and normalize(config) in command


def run(action,runtime):
    runtime=Path(runtime).resolve();cfg=json.loads((runtime/'installation.json').read_text('utf-8'))
    processes=runtime/'processes.json'
    if action=='start':
        if processes.exists():
            recorded = json.loads(processes.read_text('utf-8'))
            running = [p for p in recorded if recorded_process_is_running(p, runtime)]
            if running:
                if len(running) == len(recorded):
                    print('Recorded services are already running; checking readiness in the viewer.')
                    return
                raise RuntimeError('Some services exited. Stop the recorded services before restarting.')
            archive = runtime / f'processes.stale-{time.time_ns()}.json'
            processes.replace(archive)
            print('Removed inactive process record; starting local services.')
        children=[]
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        specs=[]
        for i in range(3):
            folder=runtime/f'party_{i}'
            specs.append((f'party_{i}',folder,[sys.executable,str(ROOT/'privacy/service/node.py'),'-M','3','-I',str(i),'-T','1','-B',str(cfg['mpc_base_port']),'-K','40','--ssl','--node-config',str(folder/'config.json')]))
        for kind in ['user','admin']:
            specs.append((kind,ROOT,[sys.executable,str(ROOT/'services/run.py'),'--kind',kind,'--config',str(runtime/kind/'config.json')]))
        specs.append(('reference',ROOT,[sys.executable,str(ROOT/'sdk/reference_site.py'),'--config',str(runtime/'installation.json')]))
        specs.append(('jobs',ROOT,[sys.executable,str(ROOT/'services/jobs/runner.py'),'--config',str(runtime/'admin/config.json')]))
        for name,cwd,command in specs:
            output=open(runtime/f'{name}.log','a',encoding='utf-8')
            child=subprocess.Popen(command,cwd=cwd,stdout=output,stderr=subprocess.STDOUT,creationflags=flags)
            output.close();children.append({'name':name,'pid':child.pid})
        processes.write_text(json.dumps(children,indent=2),encoding='utf-8')
        import httpx
        deadline=time.time()+45
        with httpx.Client(verify=cfg['ca'],timeout=2,trust_env=False) as client:
            while time.time()<deadline:
                try:
                    if all(client.get(url+'/health').json().get('ready') for url in cfg['nodes']+[cfg['user_api'],cfg['admin_api'],cfg['reference_site']]):
                        print('Started local services; all parties and applications are ready.');break
                except Exception:pass
                time.sleep(.5)
            else:print('Services started but recovery is still pending. Run health before using the applications.')
    elif action=='stop':
        for process in json.loads(processes.read_text('utf-8')) if processes.exists() else []:
            if recorded_process_is_running(process, runtime):
                try:os.kill(process['pid'],signal.SIGTERM)
                except OSError:pass
        if processes.exists():processes.unlink()
        print('Stopped recorded services.')
    elif action=='health':
        import httpx
        results=[]
        with httpx.Client(verify=cfg['ca'],timeout=4,trust_env=False) as client:
            for url in cfg['nodes']+[cfg['user_api'],cfg['admin_api'],cfg['reference_site']]:
                try:
                    r=client.get(url+'/health');r.raise_for_status();results.append({'url':url,**r.json()})
                except Exception as exc:results.append({'url':url,'ready':False,'error':type(exc).__name__})
        print(json.dumps(results,ensure_ascii=False,indent=2))
        if any(not r['ready'] for r in results):raise SystemExit(1)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['start','stop','health']);parser.add_argument('--runtime',default=str(ROOT/'tool_runtime'))
    args=parser.parse_args();run(args.action,args.runtime)
