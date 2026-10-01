"""Evaluate the exhaustively selected path on a fresh 100k-user seed."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.dynamic_config import load_dynamic_config
from experiments.dynamic_pipeline import run_dynamic_pipeline
from tools.run_dynamic_study import export_figures
from web.dynamic_presentation import render_dynamic_html

SEARCH = ROOT / 'reports' / 'dynamic' / 'top15_exhaustive_a1'
OUTPUT = ROOT / 'reports' / 'dynamic' / 'top15_exact_selected_seed43'


def main():
    status_path = SEARCH / 'status.json'
    while True:
        try:
            status = json.loads(status_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            status = None
        if status and status.get('status') == 'complete':
            break
        if status and status.get('remaining_groups', 1) == 0:
            raise RuntimeError('A1 path search has inconsistent completion status')
        time.sleep(30)
    if (status['evaluated_groups'] != status['exact_outcome_groups'] or
            status['covered_rule_paths'] != status['feasible_distinct_rule_paths']):
        raise RuntimeError('A1 search does not cover all feasible rule paths')
    cfg = load_dynamic_config(path=ROOT / 'configs' / 'dynamic_full_top15_all10.json')
    cfg['seed'] = 43
    cfg['controller']['sequence'] = status['best_evaluated']['path']
    cfg['controller']['include_recommendations'] = False
    print('Selected path: ' + ' -> '.join(cfg['controller']['sequence']), flush=True)
    report_path = OUTPUT / 'report.json'
    if report_path.exists():
        result = json.loads(report_path.read_text(encoding='utf-8'))
        actual = [row['policy']['name'] for row in result['cohorts']]
        if result['config']['seed'] != 43 or actual != cfg['controller']['sequence']:
            raise RuntimeError('Existing output is for another selected path')
        print('Reusing matching seed-43 report', flush=True)
    else:
        result = run_dynamic_pipeline(cfg, output_dir=OUTPUT,
                                      progress=lambda message: print(message, flush=True))
    (OUTPUT / 'report.html').write_text(render_dynamic_html(result), encoding='utf-8')
    figures = export_figures(result, OUTPUT)
    point = next(row for row in result['attacks']['A1']['minauto']
                 if row['budget'] == 10**6)
    summary = {
        'status': 'complete',
        'selection_scope': ('All cost-feasible executable paths on seed-42 '
                            'development/validation sample; fixed 10k-sample MC A1'),
        'selected_path': cfg['controller']['sequence'],
        'search_fingerprint': status['fingerprint'],
        'search_groups': status['exact_outcome_groups'],
        'search_covered_paths': status['covered_rule_paths'],
        'evaluation_seed': 43,
        'evaluation_users': result['dataset']['registration_occurrences'],
        'evaluation_development_hashes': result['dataset']['development_hashes'],
        'evaluation_registration_order_sha256': result['dataset']['registration_order_sha256'],
        'evaluation_a1_at_1e6': point,
        'evaluation_collision': result['final_distribution']['collision_probability'],
        'evaluation_modification_rate': sum(row['response']['modified_users']
                                            for row in result['cohorts']) / cfg['data']['users'],
        'figure_files': figures,
        'limitation': ('Seed-43 sampling uses the same source corpus; cross-seed '
                       'occurrence disjointness is not guaranteed.'),
    }
    (SEARCH / 'selected_100k_evaluation.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': summary['status'], 'a1': point['rate'],
                      'path': summary['selected_path']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
