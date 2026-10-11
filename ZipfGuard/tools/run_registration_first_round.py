"""User-requested scope reduction: finish only the prespecified first seed.

Frozen implementations, cohorts, policy settings, and attack budgets are reused.
Other repetitions remain on disk but are excluded from the single-round report.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import run_registration_joint_graded_full as original
import run_registration_joint_graded_additions as additions


def run_task(task):
    if task['arm'] in task['cfg']['arms']:
        return original.run_one(task)
    return additions.run_one(Path(task['root']), task['cfg'], task['extension'], task['seed'], task['arm'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    locks = []
    for name in ('runner.lock', 'additions.lock'):
        handle = (root / name).open('a+b')
        handle.seek(0); handle.write(b'0'); handle.flush(); handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        locks.append(handle)
    parent = json.loads((root / 'manifest.json').read_text('utf-8'))
    extra = json.loads((root / 'additions_manifest.json').read_text('utf-8'))
    for manifest in (parent, extra):
        for name, expected in manifest['source_hashes'].items():
            if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
                raise RuntimeError('Frozen source changed: ' + name)
    cfg, extension = parent['config'], extra['settings']
    seed = cfg['seeds'][0]
    arms = cfg['arms'] + extension['literature_arms'] + extension['website_arms']
    scope = {'version': 'first-round-only-v1', 'retained_seed': seed,
             'excluded_seeds': cfg['seeds'][1:], 'arms': arms, 'users': cfg['users'],
             'batches': cfg['batches'], 'total_trajectories': len(arms),
             'reason': 'User explicitly requested one round only and stopping all later rounds',
             'selection': 'First originally prespecified seed, not selected by performance',
             'parent_manifest_sha256': hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest(),
             'additions_manifest_sha256': hashlib.sha256((root / 'additions_manifest.json').read_bytes()).hexdigest(),
             'source_hashes': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                               (Path(__file__), ROOT / 'tools/report_registration_first_round.py')}}
    scope_file = root / 'single_scope.json'
    if scope_file.exists() and json.loads(scope_file.read_text('utf-8')) != scope:
        raise RuntimeError('Single-round scope changed; review before resuming')
    original.joint.write_json(scope_file, scope)
    for name in ('status.json', 'additions_status.json'):
        data = json.loads((root / name).read_text('utf-8'))
        data.update(stage='superseded_by_single_round', superseded_at=original.stamp(),
                    current_status='single_status.json', excluded_seeds=scope['excluded_seeds'])
        original.joint.write_json(root / name, data)
    (root / 'single.pid').write_text(str(os.getpid()), encoding='utf-8')
    completed = [f'{seed}/{arm}' for arm in arms if (root / 'runs' / str(seed) / arm / 'result.json').exists()]
    tasks = [{'root': str(root), 'cfg': cfg, 'extension': extension, 'seed': seed, 'arm': arm}
             for arm in arms if f'{seed}/{arm}' not in completed]
    status = {'stage': 'running', 'started_at': original.stamp(), 'retained_seed': seed,
              'total_trajectories': len(arms), 'users_per_trajectory': cfg['users'], 'batches': cfg['batches'],
              'completed': completed, 'active': [f'{seed}/{task["arm"]}' for task in tasks], 'failures': []}

    def refresh():
        status['updated_at'] = original.stamp()
        status['batch_progress'] = {}
        for task in status['active']:
            p = root / 'runs' / task / 'joint/progress.json'
            if p.exists():
                progress = json.loads(p.read_text('utf-8'))
                status['batch_progress'][task] = {k: progress.get(k) for k in ('completed_batches', 'users')}
        original.joint.write_json(root / 'single_status.json', status)

    refresh()
    original.log(f'Single round only: {len(completed)}/{len(arms)} complete; resume {status["active"]}')
    with ProcessPoolExecutor(max_workers=2) as pool:
        pending = {pool.submit(run_task, task): task for task in tasks}
        while pending:
            done, _ = wait(pending, timeout=60, return_when=FIRST_COMPLETED)
            for future in done:
                task = pending.pop(future); key = f'{seed}/{task["arm"]}'
                try:
                    future.result(); status['completed'].append(key)
                except Exception as exc:
                    status['failures'].append({'task': key, 'error': str(exc), 'traceback': traceback.format_exc()})
                status['active'].remove(key)
            refresh()
            original.log(f'First round: {len(status["completed"])}/{len(arms)} complete; batches {status["batch_progress"]}')
    if status['failures']:
        status['stage'] = 'failed'; refresh()
        raise RuntimeError('Single-round task failed; checkpoints retained')
    status['stage'] = 'reporting'; refresh()
    try:
        subprocess.run([sys.executable, str(ROOT / 'tools/report_registration_first_round.py'), str(root)], check=True)
    except Exception as exc:
        status['stage'] = 'failed'; status['failures'].append({'task': 'report', 'error': str(exc)}); refresh()
        raise
    status['stage'] = 'complete'; status['completed_at'] = original.stamp(); refresh()
    original.log('First round completed; single-round tables and figures ready')


if __name__ == '__main__':
    main()
