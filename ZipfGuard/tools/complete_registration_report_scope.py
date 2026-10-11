"""Resume only the 35 report trajectories with unchanged frozen policy/data."""
from pathlib import Path
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.registration_risk_cap import run_extended_job
from experiments.registration_joint_pipeline import write_json


def main():
    root = Path(sys.argv[1]).resolve()
    import msvcrt
    lock = (root/'runner.lock').open('a+b')
    lock.seek(0)
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    manifest = json.loads((root/'manifest.json').read_text('utf-8'))
    scope = json.loads((root/'report_scope.json').read_text('utf-8'))
    cfg = {**manifest['config'], 'arms': scope['arms']}
    for e, arms in cfg['arms'].items():
        assert set(arms).issubset(manifest['config']['arms'][e])
    status = {'stage': 'test', 'completed': [], 'queued': [], 'scope': '35 report trajectories; frozen policy/data unchanged'}
    tasks = []
    for e, arms in cfg['arms'].items():
        for seed in cfg['seeds']:
            directory = root/'runs/test'/e/str(seed)
            if all((directory/a/'result.json').exists() for a in arms):
                # A summary can be rebuilt from completed audited method files.
                summary = {'phase': 'test', 'experiment': e, 'seed': seed, 'arms': {
                    a: json.loads((directory/a/'result.json').read_text('utf-8')) for a in arms}}
                write_json(directory/'summary.json', summary)
                status['completed'].append(f'test/{e}/{seed}')
            else:
                tasks.append({'root': str(root), 'cfg': cfg, 'phase': 'test', 'experiment': e, 'seed': seed})
                status['queued'].append(f'test/{e}/{seed}')
    write_json(root/'status.json', status)
    with ProcessPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(run_extended_job, t): t for t in tasks}
        for future in as_completed(futures):
            task = futures[future]
            future.result()
            key = f"test/{task['experiment']}/{task['seed']}"
            status['completed'].append(key)
            status['queued'].remove(key)
            status['updated_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
            write_json(root/'status.json', status)
    import subprocess
    status['stage'] = 'reporting'
    write_json(root/'status.json', status)
    subprocess.run([sys.executable, str(ROOT/'tools/report_registration_comparison.py'), str(root)], check=True)
    status.update(stage='complete', completed_at=time.strftime('%Y-%m-%d %H:%M:%S'))
    write_json(root/'status.json', status)


if __name__ == '__main__':
    main()
