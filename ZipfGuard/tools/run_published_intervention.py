"""Durable full-study runner; publish only after protocol checks succeed."""
import argparse
import gzip
import hashlib
import json
import math
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.intervention_config import load_intervention_config
from experiments.intervention_pipeline import run_intervention_pipeline
from core.ideal_distribution import METRIC
from core.intervention_acceptance import AREA_ONLY_POLICY, INDIVIDUAL_POLICY


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def audit_report(report):
    cfg = report['config']
    n = cfg['data']['users']
    comparison = report['google_round_zipf']
    area_only = cfg['controller']['execution_policy'] == AREA_ONLY_POLICY
    individual = cfg['controller']['execution_policy'] == INDIVIDUAL_POLICY
    assert comparison['common_google_start_verified']
    start = report['arms']['google_hold']['final']['account_state_sha256']
    round_cap = math.floor(n * cfg['controller']['round_fraction'] + 1e-9)
    total_cap = math.floor(n * cfg['controller']['total_fraction'] + 1e-9)
    if individual:
        dynamic_schedule = [r['action']['selected'] for r in report['arms']['google_dynamic']['rounds']]
        assert comparison['comparison_budget']['planned_round_notification_schedule'] == dynamic_schedule
        for key in ('google_random', 'google_frozen'):
            control_schedule = [r['action']['selected'] for r in report['arms'][key]['rounds']]
            matched = comparison['comparison_budget']['matched_notification_counts'][key]
            assert matched == (control_schedule == dynamic_schedule)
    for key, arm in report['arms'].items():
        assert arm['final']['ledger']['accounts'] == n
        assert arm['trajectory'][0]['account_state_sha256'] == start
        assert len(arm['rounds']) <= comparison['requested_rounds']
        ledger = arm['final']['ledger']
        assert ledger['adaptive_affected'] <= total_cap
        assert ledger['adaptive_notification_events'] == ledger['adaptive_affected']
        for previous, current, row in zip(arm['trajectory'], arm['trajectory'][1:], arm['rounds']):
            assert current['ledger']['adaptive_notification_events'] - previous['ledger']['adaptive_notification_events'] <= round_cap
            guard = row['aggregate_attack_guard']
            if guard['accepted']:
                if individual:
                    assert guard['individual_passed'] == guard['individual_total'] == row['changed']
                    assert guard['threshold_per_model'] == cfg['attack_models']['threshold']
                else:
                    assert guard['before_area'] - guard['after_area'] > 1e-12
                if not area_only and not individual:
                    assert guard['proposed_uncovered_rate_change'] <= 1e-12
            else:
                assert row['changed'] == 0
                assert abs(guard['before_area'] - guard['after_area']) <= 1e-12
            if area_only or individual:
                assert guard['accepted']
                assert row['changed'] == row['action']['selected']
                assert row['nonresponse'] == row['failed_to_comply'] == 0
        assert arm['attacks']['A1'] is not None
    return {'passed': True, 'accounts': n, 'common_start': True,
            'round_budget': round_cap, 'total_budget': total_cap,
            'checks': ['population', 'common eight-character start', 'notifications', 'round limit',
                       'individual PCFG + OMEN threshold' if individual else
                       'F area-only + full successful response' if area_only else 'atomic F guard', 'endpoint A1'],
            'notification_matching': comparison['comparison_budget'].get('matched_notification_counts')}


def publish(report):
    run_id = report['metadata']['run_id']
    source = ROOT/'reports'/'intervention'/run_id
    destination = ROOT/'published'/'intervention'/run_id
    destination.mkdir(parents=True, exist_ok=True)
    content = (source/'report.json').read_bytes()
    compressed = gzip.compress(content, mtime=0)
    (destination/'report.json.gz').write_bytes(compressed)
    (destination/'report.json.gz.sha256').write_text(hashlib.sha256(compressed).hexdigest()+'  report.json.gz\n', encoding='utf-8')
    for path in [source/'report.html', source/'validation.json', *source.glob('*.svg')]:
        (destination/path.name).write_bytes(path.read_bytes())
    save_json(ROOT/'published'/'intervention'/'latest.json', {
        'run_id': run_id, 'users': report['config']['data']['users'], 'google_start': True,
        'dynamic_rounds': len(report['arms']['google_dynamic']['rounds']),
        'candidate_pool': report['config']['controller']['candidate_pool'],
        'comparison_arms': 5, 'endpoint_arms': 6,
        'protocol': report['metadata']['protocol'],
        'analysis_metric': METRIC,
        'dynamic_execution_reused': False, 'site_controls': list(report.get('site_controls', {})),
        'created_at': report['metadata']['created_at'], 'validated': True})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preset', choices=['intervention_full', 'intervention_smoke'], default='intervention_full')
    parser.add_argument('--status-file', type=Path, default=ROOT/'reports'/'active_intervention.json')
    args = parser.parse_args()
    cfg = load_intervention_config(args.preset)
    job_id = time.strftime('%Y%m%dT%H%M%S') + '-' + str(os.getpid())
    directory = ROOT/'reports'/'intervention_jobs'/job_id
    state = {'job_id': job_id, 'pid': os.getpid(), 'status': 'running',
             'started_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
             'users': cfg['data']['users'], 'protocol': cfg['schema_version'],
             'run_id': None, 'message': '准备完整实验', 'checkpoint_directory': str(directory)}
    save_json(directory/'config.json', cfg)

    def update(message):
        state.update(message=message, updated_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
        save_json(args.status_file, state)
        save_json(directory/'status.json', state)
        print(state['updated_at'], message, flush=True)

    def checkpoint(method, payload):
        save_json(directory/(method+'.json'), payload)

    try:
        update('开始完整实验；每轮记录持久化保存')
        report = run_intervention_pipeline(cfg, progress=update, checkpoint=checkpoint)
        update('实验计算完成，核验预算、共同起点与 F 安全条件')
        validation = audit_report(report)
        save_json(ROOT/'reports'/'intervention'/report['metadata']['run_id']/'validation.json', validation)
        publish(report)
        state.update(status='complete', run_id=report['metadata']['run_id'])
        update('完整实验已完成并更新展示结果')
    except BaseException as exc:
        state['status'] = 'failed'
        update('实验未完成：'+str(exc))
        traceback.print_exc()
        raise


if __name__ == '__main__':
    main()
