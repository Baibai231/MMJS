"""Attach an equal-cost, paired F comparison to a completed 100k report."""
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from web.intervention_presentation import render_intervention_html

ARMS = ('google_random', 'google_frozen', 'google_dynamic')


def at_cost(arm, cost, budget):
    points = [(state['ledger']['adaptive_affected_rate'], next(
        row['rate'] for row in state['risk']['minauto'] if row['budget'] == budget))
        for state in arm['trajectory']]
    for (left_cost, left_rate), (right_cost, right_rate) in zip(points, points[1:]):
        if left_cost <= cost <= right_cost:
            if right_cost == left_cost:
                return right_rate
            fraction = (cost-left_cost)/(right_cost-left_cost)
            return left_rate + fraction*(right_rate-left_rate)
    if cost == points[0][0]:
        return points[0][1]
    raise ValueError('实验组未达到共同成本点')


def summarize(reports):
    if len(reports) < 2:
        raise ValueError('配对复验至少需要两个运行')
    main = reports[0]
    if any(r['config']['data']['users'] != 100000 or
           not r['google_round_zipf']['common_google_start_verified'] for r in reports):
        raise ValueError('所有运行必须是已核验共同 Google 起点的 10 万账户实验')
    base = dict(main['config'])
    base.pop('seed')
    if any(dict((k, v) for k, v in r['config'].items() if k != 'seed') != base
           or r['metadata']['source_hashes'] != main['metadata']['source_hashes']
           for r in reports[1:]):
        raise ValueError('除随机种子外，配置和代码摘要必须一致')
    seeds = [r['config']['seed'] for r in reports]
    if len(set(seeds)) != len(seeds):
        raise ValueError('配对复验种子重复')
    minimum = min(r['arms'][key]['final']['ledger']['adaptive_affected_rate']
                  for r in reports for key in ARMS)
    step = main['config']['controller']['round_fraction']
    cost = round(math.floor((minimum+1e-10)/step)*step, 10)
    if cost <= 0:
        raise ValueError('没有共同的正成本区间')
    budget = main['config']['risk_budget']
    runs = []
    for report in reports:
        values = {key: {'F': at_cost(report['arms'][key], cost, budget),
                        'terminal_cost': report['arms'][key]['final']['ledger']['adaptive_affected_rate']}
                  for key in ARMS}
        runs.append({'seed': report['config']['seed'], 'arms': values,
                     'run_id': report['metadata']['run_id']})
    mean = {key: {'F': sum(run['arms'][key]['F'] for run in runs)/len(runs)} for key in ARMS}
    mean['dynamic_minus_random'] = {'F': mean['google_dynamic']['F']-mean['google_random']['F']}
    mean['dynamic_minus_frozen'] = {'F': mean['google_dynamic']['F']-mean['google_frozen']['F']}
    return {'protocol': 'cost-matched-F-v1', 'comparison_cost': cost,
            'budget': budget, 'runs': runs, 'mean': mean,
            'interpolation': 'linear between adjacent observed cost points; no extrapolation'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('main_report', type=Path)
    parser.add_argument('replication_reports', nargs='+', type=Path)
    args = parser.parse_args()
    paths = [args.main_report, *args.replication_reports]
    reports = [json.loads(path.read_text(encoding='utf-8')) for path in paths]
    paired = summarize(reports)
    reports[0]['paired_replications'] = paired
    content = json.dumps(reports[0], ensure_ascii=False, indent=2, allow_nan=False)+'\n'
    args.main_report.write_text(content, encoding='utf-8')
    (args.main_report.parent/'report.json.sha256').write_text(
        hashlib.sha256(args.main_report.read_bytes()).hexdigest()+'  report.json\n', encoding='utf-8')
    (args.main_report.parent/'report.html').write_text(
        render_intervention_html(reports[0]), encoding='utf-8')
    print(json.dumps(paired, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
