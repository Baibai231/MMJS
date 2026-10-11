"""Resume-safe runner, calibration freeze, test gate, automatic report and audit."""
from pathlib import Path
import argparse
import hashlib
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.registration_comparison import prepare_partition, run_comparison_job, VERSION
from experiments.registration_joint_pipeline import write_json


def log(message):
    print(time.strftime('%Y-%m-%d %H:%M:%S')+' '+message, flush=True)


def freeze(root, cfg):
    for experiment in cfg['arms']:
        path = root/'frozen_parameters'/f'{experiment}.json'
        if path.exists():
            continue
        jobs = [json.loads((root/'runs/calibration'/experiment/str(seed)/'summary.json').read_text('utf-8'))
                for seed in cfg['seeds']]
        ranking = []
        for label in jobs[0]['arms']:
            rows = [job['arms'][label] for job in jobs]
            ranking.append({'label': label, 'arm': rows[0]['arm'], 'parameters': rows[0]['parameters'],
                'eligible_all_seeds': all(not r['eligibility_failures'] for r in rows),
                'mean_risk': sum(r['risk'][-1] for r in rows)/len(rows),
                'mean_extra': sum(r['cost']['mean_extra_submissions'] for r in rows)/len(rows),
                'failures': [r['eligibility_failures'] for r in rows]})
        output = {'frozen_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'test_data_used': False,
                  'selection_source': 'five new calibration cohorts, fixed train-only predictor', 'ranking': ranking}
        for arm in ['fixed', 'refresh', 'schechter']:
            candidates = [r for r in ranking if r['arm'] == arm and r['eligible_all_seeds']]
            if candidates:
                output[arm] = min(candidates, key=lambda r: (r['mean_risk'], r['mean_extra'], r['label']))['parameters']
            else:
                output[arm] = {'k': 0} if arm != 'schechter' else {'rate': max(cfg['popularity_rates'])}
                output[arm+'_selection_failure'] = 'no grid value eligible; explicitly reported, no claim of matched costs'
        write_json(path, output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Windows byte-range lock guards against concurrent resume invocations.
    import msvcrt
    lock = (root/'runner.lock').open('a+b')
    lock.seek(0)
    lock.write(b'0')
    lock.flush()
    lock.seek(0)
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    cfg = json.loads(Path(args.config).read_text('utf-8'))
    cfg['current_config'] = json.loads((ROOT/'configs/registration_joint_minimal.json').read_text('utf-8'))
    files = [ROOT/'policy/registration_comparators.py', ROOT/'experiments/registration_comparison.py',
             ROOT/'tools/run_registration_comparison.py', ROOT/'policy/registration_joint.py',
             ROOT/'experiments/registration_joint_pipeline.py', ROOT/'experiments/registration_joint_data.py',
             ROOT/'ai/pcfg_adapter.py', ROOT/'ai/dual_attack.py']
    identity = {'version': VERSION, 'config': cfg, 'source_hashes': {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    manifest = root/'manifest.json'
    if manifest.exists() and json.loads(manifest.read_text('utf-8')) != identity:
        raise RuntimeError('Implementation/configuration changed; do not resume the frozen study silently')
    write_json(manifest, identity)
    prepare_partition(ROOT.parent/cfg['source'], ROOT/'reports/registration_joint_minimal_20261011/private/partition.pkl.gz',
                      root, cfg, log)
    if args.prepare_only:
        return
    status = {'stage': 'calibration', 'completed': [], 'updated_at': time.strftime('%Y-%m-%d %H:%M:%S')}
    try:
        for phase in ['calibration', 'test']:
            if phase == 'test':
                freeze(root, cfg)
                write_json(root/'test_opening.json', {'opened_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'configuration_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
                    'frozen_parameter_hashes': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                              for p in (root/'frozen_parameters').glob('*.json')},
                    'scope': '100000 users x five disjoint paired seeds, two experiments'})
            tasks = [{'root': str(root), 'cfg': cfg, 'phase': phase, 'experiment': experiment, 'seed': seed}
                     for experiment in cfg['arms'] for seed in cfg['seeds']]
            todo = []
            for task in tasks:
                key = f"{phase}/{task['experiment']}/{task['seed']}"
                if (root/'runs'/key/'summary.json').exists():
                    status['completed'].append(key)
                else:
                    todo.append(task)
            status['stage'] = phase
            status['queued'] = [f"{phase}/{t['experiment']}/{t['seed']}" for t in todo]
            write_json(root/'status.json', status)
            with ProcessPoolExecutor(max_workers=args.workers) as executor:
                futures = {executor.submit(run_comparison_job, task): task for task in todo}
                for future in as_completed(futures):
                    task = futures[future]
                    future.result()
                    key = f"{phase}/{task['experiment']}/{task['seed']}"
                    status['completed'].append(key)
                    status['queued'].remove(key)
                    status['updated_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
                    write_json(root/'status.json', status)
                    log('Persisted '+key)
        status['stage'] = 'reporting'
        write_json(root/'status.json', status)
        import subprocess
        subprocess.run([sys.executable, str(ROOT/'tools/report_registration_comparison.py'), str(root)], check=True)
        status['stage'] = 'complete'
        status['completed_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
        write_json(root/'status.json', status)
    except Exception as exc:
        import traceback
        status.update(stage='failed', error=str(exc), traceback=traceback.format_exc())
        write_json(root/'status.json', status)
        raise


if __name__ == '__main__':
    main()
