"""Local launch, stop, health and independent-party backup commands."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from services.common import bootstrap
import argparse,json,os,signal,sqlite3,subprocess,time,shutil

def run(action,runtime):
    runtime=Path(runtime).resolve();cfg=json.loads((runtime/'installation.json').read_text('utf-8'))
    processes=runtime/'processes.json'
    if action=='start':
        if processes.exists():raise SystemExit('Process record exists; stop before restarting.')
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
            try:os.kill(process['pid'],signal.SIGTERM)
            except OSError:pass
        if processes.exists():processes.unlink()
        print('Stopped recorded services.')
    elif action=='health':
        import httpx
        results=[]
        with httpx.Client(verify=cfg['ca'],timeout=4) as client:
            for url in cfg['nodes']+[cfg['user_api'],cfg['admin_api'],cfg['reference_site']]:
                try:
                    r=client.get(url+'/health');r.raise_for_status();results.append({'url':url,**r.json()})
                except Exception as exc:results.append({'url':url,'ready':False,'error':type(exc).__name__})
        print(json.dumps(results,ensure_ascii=False,indent=2))
        if any(not r['ready'] for r in results):raise SystemExit(1)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['start','stop','health']);parser.add_argument('--runtime',default=str(ROOT/'tool_runtime'))
    args=parser.parse_args();run(args.action,args.runtime)
