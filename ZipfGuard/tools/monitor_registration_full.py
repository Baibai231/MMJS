"""Observe the frozen registration study without changing or restarting it."""
import argparse
import ctypes
import json
import msvcrt
import os
from pathlib import Path
import time


def read_json(path):
    for attempt in range(3):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            if attempt == 2:
                raise
            time.sleep(.2)


def alive(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False if ctypes.get_last_error() == 87 else None
    code = ctypes.c_ulong()
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
    kernel.CloseHandle(handle)
    return code.value == 259 if ok else None


def single_snapshot(root):
    status = read_json(root / 'single_status.json')
    scope = read_json(root / 'single_scope.json')
    pid = int((root / 'single.pid').read_text().strip())
    living = alive(pid)
    stage = status['stage']
    stdout, stderr = root / 'single.stdout.log', root / 'single.stderr.log'
    age = round(time.time() - stdout.stat().st_mtime)
    issues = []
    if status.get('failures') or stage == 'failed':
        issues.append('Single-round runner reports failure')
    if stage in ('running', 'reporting') and living is False:
        issues.append('Single-round process stopped before completion')
    if stage == 'running' and age > 900:
        issues.append('No stdout update for over 15 minutes; inspect current phase')
    if stderr.stat().st_size:
        issues.append('Single-round stderr is nonempty; inspect, not necessarily fatal')
    results = [root / 'runs' / str(scope['retained_seed']) / arm / 'result.json' for arm in scope['arms']]
    active = []
    for task in status.get('active', []):
        path = root / 'runs' / task / 'joint/progress.json'
        data = read_json(path) if path.exists() else {}
        active.append({'task': task, 'completed_batches': data.get('completed_batches'),
                       'users': data.get('users'), 'stage': data.get('stage', 'registration')})
    count = sum(path.exists() for path in results)
    return {'checked_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'completed': len(status['completed']), 'total': len(results), 'result_files': count,
            'retained_seed': scope['retained_seed'], 'excluded_seeds': scope['excluded_seeds'],
            'runners': {'single': {'stage': stage, 'pid': pid, 'alive': living,
                                  'log_age_seconds': age, 'failures': status.get('failures', []),
                                  'stderr_bytes': stderr.stat().st_size}},
            'active': active, 'issues': issues,
            'all_complete': stage == 'complete' and count == len(results)}


def snapshot(root):
    if (root / 'single_status.json').exists():
        return single_snapshot(root)
    now = time.time()
    runners = {}
    issues = []
    active = []
    for name, status_file in [('runner', 'status.json'), ('additions', 'additions_status.json')]:
        status = read_json(root / status_file)
        pid = int((root / (name + '.pid')).read_text().strip())
        living = alive(pid)
        stdout = root / (name + '.stdout.log')
        stderr = root / (name + '.stderr.log')
        age = round(now - stdout.stat().st_mtime)
        stage = status.get('stage')
        failures = status.get('failures', [])
        runners[name] = {'stage': stage, 'completed': len(status.get('completed', [])),
                         'pid': pid, 'alive': living, 'log_age_seconds': age,
                         'failures': failures, 'stderr_bytes': stderr.stat().st_size}
        if failures or stage in ('failed', 'parent_failed'):
            issues.append(name + ': runner reports failure')
        if stage == 'running' and living is False:
            issues.append(name + ': process stopped before completion')
        if stage == 'running' and age > 900:
            issues.append(name + ': no stdout update for over 15 minutes; inspect current phase')
        if stderr.stat().st_size:
            issues.append(name + ': stderr is nonempty; inspect, not necessarily fatal')
    results = sorted(root.glob('runs/*/*/result.json'))
    for path in sorted(root.glob('runs/*/*/joint/progress.json')):
        run = path.parent.parent
        if (run / 'result.json').exists():
            continue
        data = read_json(path)
        active.append({'task': '/'.join(run.relative_to(root / 'runs').parts),
                       'completed_batches': data.get('completed_batches'),
                       'users': data.get('users'), 'stage': data.get('stage', 'registration')})
    completed = sum(item['completed'] for item in runners.values())
    done = all(item['stage'] == 'complete' for item in runners.values())
    return {'checked_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'completed': completed,
            'total': 50, 'result_files': len(results), 'runners': runners,
            'active': active, 'issues': issues, 'all_complete': done and len(results) == 50}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    lock = (root / 'monitor.lock').open('a+b')
    lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    (root / 'monitor.pid').write_text(str(os.getpid()), encoding='utf-8')
    previous = None
    while True:
        try:
            data = snapshot(root)
        except Exception as exc:
            data = {'checked_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'issues': ['Monitor read error: ' + str(exc)], 'all_complete': False}
        temporary = root / 'monitor_status.tmp'
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(root / 'monitor_status.json')
        key = (data.get('completed'), data.get('result_files'),
               json.dumps(data.get('active')), tuple(data['issues']), data['all_complete'])
        if key != previous:
            with (root / 'monitor_events.jsonl').open('a', encoding='utf-8') as output:
                output.write(json.dumps(data, ensure_ascii=False) + '\n')
            print(json.dumps(data, ensure_ascii=False), flush=True)
            previous = key
        if args.once or data['all_complete']:
            return
        time.sleep(60)


if __name__ == '__main__':
    main()
