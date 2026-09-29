"""Stress-test registration order and modeled user responses on one sampled pool."""
from __future__ import annotations

import argparse
import copy
import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.corpus import counts_hash
from core.registration import load_registration
from experiments.dynamic_config import load_dynamic_config, validate_dynamic_config
from experiments.dynamic_pipeline import run_dynamic_pipeline
from tools.run_dynamic_study import export_figures
from web.dynamic_presentation import render_dynamic_html
from web.presentation import STYLE, table


def reorder(dataset, order, seed):
    """Keep the same sampled occurrences and development data in every scenario."""
    if order not in ('random', 'weak_first', 'weak_last'):
        raise ValueError('未知注册顺序情景')
    result = copy.deepcopy(dataset)
    words = [word for cohort in result['cohorts'] for word in cohort]
    if order != 'random':
        random.Random(seed ^ 0xD12).shuffle(words)
        frequency = result['development']['train']
        words.sort(key=lambda word: frequency.get(word, 0),
                   reverse=order == 'weak_first')
    size = result['metadata']['cohort_size']
    result['cohorts'] = [words[i:i + size] for i in range(0, len(words), size)]
    result['metadata']['registration_order_sha256'] = counts_hash(Counter(
        f'{i}:{word}' for i, word in enumerate(words)))
    result['metadata']['registration_order_scenario'] = order
    result['metadata']['registration_order_basis'] = (
        'frequency in disjoint development train; shuffled stable ties'
        if order != 'random' else 'seeded random order')
    return result


def scenarios(base):
    yield 'main_random', 'random', base
    yield 'weak_first', 'weak_first', base
    yield 'weak_last', 'weak_last', base
    repair = copy.deepcopy(base)
    repair['controller']['response_weights'] = [0.9, 0.05, 0.05]
    yield 'repair_heavy', 'random', repair
    reselect = copy.deepcopy(base)
    reselect['controller']['response_weights'] = [0.2, 0.1, 0.7]
    yield 'reselect_heavy', 'random', reselect
    abandon = copy.deepcopy(base)
    abandon['controller']['abandon_probability'] = 0.1
    abandon['controller']['min_completion'] = 0.85
    yield 'high_abandonment', 'random', abandon


def run(config, output, *, progress=None):
    cfg = validate_dynamic_config(config)
    data = cfg['data']
    source = Path(data['path'])
    if not source.is_absolute():
        source = ROOT / source
    sampled = load_registration(source, source_format=data['format'],
                                encoding=data['encoding'], users=data['users'],
                                development=data['development'], seed=cfg['seed'],
                                cohort_size=data['cohort_size'], progress=progress)
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, order, scenario_cfg in scenarios(cfg):
        scenario_cfg = validate_dynamic_config(scenario_cfg)
        if progress:
            progress(f'敏感性情景：{name}')
        dataset = reorder(sampled, order, cfg['seed'])
        result = run_dynamic_pipeline(scenario_cfg, dataset=dataset,
                                      output_dir=output / name, progress=progress)
        directory = output / name
        (directory / 'report.html').write_text(render_dynamic_html(result),
                                               encoding='utf-8')
        export_figures(result, directory)
        last = result['attacks']['A1']['minauto'][-1]
        rows.append({
            'scenario': name, 'order': order,
            'response_weights': scenario_cfg['controller']['response_weights'],
            'abandon_probability': scenario_cfg['controller']['abandon_probability'],
            'registration_order_sha256': dataset['metadata']['registration_order_sha256'],
            'development_hashes': dataset['metadata']['development_hashes'],
            'completed_users': result['final_distribution']['users'],
            'modified_users': sum(r['response']['modified_users']
                                  for r in result['cohorts']),
            'collision_per_million_pairs':
                1_000_000 * result['final_distribution']['collision_probability'],
            'baseline_paired_collision_per_million_pairs':
                1_000_000 * result['baseline_completed_distribution']['collision_probability'],
            'A1_cracked_at_max_budget': last['rate'],
            'A1_budget_complete': last['complete'],
            'updated_cohorts': [r['cohort_id'] for r in result['cohorts']
                                if r['policy_changed']],
            'report': str((directory / 'report.json').resolve()),
        })
    payload = {
        'schema_version': 'zipfguard-dynamic-sensitivity-v1',
        'source_sha256': sampled['metadata']['source_sha256'],
        'sample_seed': cfg['seed'],
        'registration_occurrences': data['users'],
        'development_occurrences': data['development'],
        'budget_per_model': cfg['budgets'][-1],
        'interpretation': 'same sampled occurrences; modeled order and behavior stress tests',
        'runs': rows,
    }
    (output / 'sensitivity_summary.json').write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
        encoding='utf-8')
    body = '<main><section><h1>注册顺序与用户响应敏感性</h1>'
    body += '<p>六个情景使用同一批抽取的注册出现记录及同一份开发数据。弱口令先到/后到按开发训练频次排序，作为模拟压力测试，不代表真实注册时间。响应参数也是模型假设。</p>'
    body += table(['情景', '完成用户', '修改率', '同口令数/百万对',
                   'A1 最高预算命中率', '新增规则批次'], [
        [r['scenario'], r['completed_users'],
         f"{100*r['modified_users']/data['users']:.2f}%",
         f"{r['collision_per_million_pairs']:.2f}",
         (f"{100*r['A1_cracked_at_max_budget']:.2f}%"
          if r['A1_cracked_at_max_budget'] is not None else '预算未完成'),
         ', '.join(map(str, r['updated_cohorts']))] for r in rows])
    body += '<p>小规模与较低预算的情景结果只检验方向和机制，不替代五种子十万人百万预算正式对照。逐情景报告与图表保存在同名子目录；逐用户明文仍只在本机私有结果文件中。</p></section></main>'
    (output / 'sensitivity_report.html').write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>ZipfGuard · 敏感性实验</title><style>' + STYLE +
        '</style><body>' + body + '</body></html>', encoding='utf-8')
    return payload


def main():
    parser = argparse.ArgumentParser(description='分批注册顺序和用户响应敏感性实验')
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    cfg = load_dynamic_config(path=args.config)
    result = run(cfg, args.output_dir,
                 progress=lambda message: print(message, flush=True))
    print(args.output_dir / 'sensitivity_report.html')
    print(len(result['runs']))


if __name__ == '__main__':
    main()
