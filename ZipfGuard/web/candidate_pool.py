"""Read-only publication of the fully evaluated fifteen-policy pilot."""
from __future__ import annotations

import json
from pathlib import Path


DIRECTORY = (Path(__file__).resolve().parents[1] / 'reports' / 'dynamic'
             / 'fifteen_policy_exhaustive_seed42')
ASSETS = {
    'top10_report.html': 'text/html; charset=utf-8',
    'top10_distribution.svg': 'image/svg+xml; charset=utf-8',
    'top10_security.svg': 'image/svg+xml; charset=utf-8',
    'top10_cost.svg': 'image/svg+xml; charset=utf-8',
    'top10_summary.json': 'application/json; charset=utf-8',
}


def candidate_pool_snapshot(directory=None):
    directory = Path(directory) if directory is not None else DIRECTORY
    audit_file = directory / 'registration_audit.json'
    if not audit_file.is_file():
        return {'status': 'unavailable', 'message': '尚无 15 条策略穷举结果。'}
    audit = json.loads(audit_file.read_text(encoding='utf-8'))
    completed = set()
    seen = set()
    for path in [directory / 'attacked_paths.jsonl', *sorted(directory.glob('attacked_paths_shard_*.jsonl'))]:
        if not path.is_file():
            continue
        text = path.read_text(encoding='utf-8')
        # A writer may currently be appending its next record.
        lines = text.splitlines() if text.endswith('\n') else text.splitlines()[:-1]
        for line in lines:
            row = json.loads(line)
            key = tuple(row['path'])
            if key in seen:
                return {'status': 'invalid', 'message': '攻击检查点含有重复方案，排名暂不发布。'}
            seen.add(key)
            point = next((p for p in row['A1_minauto'] if p['budget'] == 1_000_000), None)
            if (point and point['complete'] and point['rate'] is not None
                    and all(m['stop_reason'] in ('reached_budget', 'exhausted') for m in row['models'])):
                completed.add(key)
    expected = audit['feasible_complete_paths']
    result = {
        'status': 'evaluating', 'evaluated_paths': len(completed),
        'expected_paths': expected, 'structural_paths': audit['structural_paths'],
        'cost_excluded_paths': audit['cost_excluded_complete_paths'],
        'message': (f'已穷举 {audit["structural_paths"]:,} 条合法路径，'
                    f'{audit["cost_excluded_complete_paths"]:,} 条因共同成本上限排除；'
                    f'百万预算攻击已保存 {len(completed):,}/{expected:,} 条完整结果。'
                    '三组 Top10 将在全部评价并核验后展示。'),
    }
    summary_file = directory / 'top10_summary.json'
    fragment = directory / 'top10_fragment.html'
    if len(completed) == expected and summary_file.is_file() and fragment.is_file():
        summary = json.loads(summary_file.read_text(encoding='utf-8'))
        if (summary['audit'] == audit and summary['attack_incomplete_paths'] == 0
                and all((directory / name).is_file() for name in ASSETS)):
            result.update(status='complete', message='全部穷举评价已完成。',
                          html=fragment.read_text(encoding='utf-8').replace(
                              'href="top10_', 'href="/dynamic/candidate-pool/top10_'),
                          report_url='/dynamic/candidate-pool/')
    return result


def candidate_pool_asset(name, directory=None):
    directory = Path(directory) if directory is not None else DIRECTORY
    if name not in ASSETS or not (directory / 'top10_summary.json').is_file():
        raise FileNotFoundError('完整候选池报告尚未发布')
    return (directory / name).read_bytes(), ASSETS[name]
