"""Summarize million-budget paired comparisons across complete registration runs."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_dynamic_study import svg_only
from web.dynamic_presentation import CONTROL_LABELS
from web.presentation import STYLE, table

ARM_NAMES = ('fixed_length8', 'fixed_complexity', 'static_selected',
             'fixed_schedule')
BUDGET = 1_000_000


def _point(evaluation):
    point = next((p for p in evaluation['minauto'] if p['budget'] == BUDGET), None)
    if point is None or not point['complete'] or not point['all_models_completed']:
        raise ValueError('百万预算攻击评估未完整完成')
    models = {row['run']['model']: row['run'] for row in evaluation['models']}
    if set(models) != {'frequency', 'dictionary-rules', 'character-ngram'}:
        raise ValueError('百万预算攻击模型集合不一致')
    if (models['character-ngram']['charged_count'] < BUDGET or
            models['character-ngram']['stop_reason'] != 'reached_budget'):
        raise ValueError('字符模型未完成百万次有效猜测')
    if any(row['stop_reason'] not in ('reached_budget', 'exhausted')
           for row in models.values()):
        raise ValueError('攻击模型异常停止')
    return point


def summarize(paths):
    rows = []
    reference_config = source_hash = None
    for seed, path in sorted(paths.items()):
        result = json.loads(path.read_text(encoding='utf-8'))
        if result['dataset']['seed'] != seed:
            raise ValueError(f'报告种子不匹配：{seed}')
        if result['dataset']['registration_occurrences'] != 100_000:
            raise ValueError(f'不是十万注册用户：{seed}')
        config = dict(result['config'])
        config.pop('seed')
        if reference_config is None:
            reference_config = config
            source_hash = result['dataset']['source_sha256']
        elif config != reference_config or result['dataset']['source_sha256'] != source_hash:
            raise ValueError(f'配置或语料不一致：{seed}')
        dynamic = _point(result['attacks']['A1'])
        attempted = result['dataset']['registration_occurrences']
        row = {'seed': seed, 'dynamic_A1_cracked': dynamic['rate'],
               'dynamic_modified_rate': sum(
                   r['response']['modified_users'] for r in result['cohorts']) / attempted,
               'dynamic_collision_per_million_pairs':
                   1_000_000 * result['final_distribution']['collision_probability'],
               'controls': {}, 'report': str(path)}
        for name in ARM_NAMES:
            control = result['controls'][name]
            risk = _point(result['attacks']['controls'][name])
            paired = next((p for p in result['attacks']
                           ['paired_dynamic_minus_control'][name]
                           if p['budget'] == BUDGET), None)
            if paired is None or paired['status'] != 'complete' or paired['paired_users'] < 90_000:
                raise ValueError(f'种子 {seed} 的 {name} 配对比较未完成')
            row['controls'][name] = {
                'A1_cracked': risk['rate'],
                'modified_rate': control['modified_users'] / attempted,
                'collision_per_million_pairs':
                    1_000_000 * control['final']['collision_probability'],
                'paired_users': paired['paired_users'],
                'dynamic_minus_control_percentage_points':
                    100 * paired['difference'],
                'conditional_ci95_percentage_points':
                    [100 * paired['ci95_lower'], 100 * paired['ci95_upper']],
            }
        rows.append(row)
    return {'schema_version': 'zipfguard-dynamic-controls-cross-seed-v1',
            'source_sha256': source_hash, 'budget_per_model': BUDGET,
            'comparison_scope': 'seed-level paired differences; conditional per-seed intervals are not pooled',
            'runs': rows}


def export(summary, destination):
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'cross_seed_controls.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
        encoding='utf-8')
    rows = summary['runs']
    names = [('动态历史反馈', 'dynamic')] + [(CONTROL_LABELS[n], n)
                                               for n in ARM_NAMES]
    risk = []
    tradeoff = []
    for label, name in names:
        if name == 'dynamic':
            risk.append((label, [(r['seed'], r['dynamic_A1_cracked']) for r in rows]))
            tradeoff.append((label, [(r['dynamic_modified_rate'], r['dynamic_A1_cracked'])
                                      for r in rows]))
        else:
            risk.append((label, [(r['seed'], r['controls'][name]['A1_cracked'])
                                  for r in rows]))
            tradeoff.append((label, [(r['controls'][name]['modified_rate'],
                                      r['controls'][name]['A1_cracked'])
                                     for r in rows]))
    for filename, series, xlabel, ylabel in [
            ('cross_seed_control_risk.svg', risk, '随机种子', '百万预算 A1 命中率'),
            ('cross_seed_control_tradeoff.svg', tradeoff,
             '原始用户修改率', '百万预算 A1 命中率')]:
        (destination / filename).write_text(
            svg_only(series, xlabel=xlabel, ylabel=ylabel, scatter=True),
            encoding='utf-8')
    overview = []
    for name in ARM_NAMES:
        values = [r['controls'][name]['dynamic_minus_control_percentage_points']
                  for r in rows]
        overview.append([CONTROL_LABELS[name],
                         f'{statistics.mean(values):+.2f}',
                         f'{min(values):+.2f} 至 {max(values):+.2f}',
                         sum(v < 0 for v in values), len(values)])
    detail = []
    for r in rows:
        detail.append([r['seed'], f"{100*r['dynamic_modified_rate']:.2f}%",
                       f"{100*r['dynamic_A1_cracked']:.2f}%",
                       f"{r['dynamic_collision_per_million_pairs']:.2f}"] + [
                           f"{r['controls'][name]['dynamic_minus_control_percentage_points']:+.2f}"
                           for name in ARM_NAMES])
    body = '<main><section><h1>五种子固定策略对照</h1>'
    body += '<p>同一注册顺序内只比较双方都完成注册的用户。正值表示动态策略的自适应攻击命中率更高；每个模型的预算均为 1,000,000 次有效猜测。</p>'
    body += table(['固定对照', '动态减固定：平均百分点', '五种子范围',
                   '动态命中率更低的种子数', '种子数'], overview)
    body += '</section><section><h2>自适应攻击风险</h2><img src="cross_seed_control_risk.svg" alt="种子与攻击风险" style="width:100%;max-width:900px"></section>'
    body += '<section><h2>修改成本与攻击风险</h2><img src="cross_seed_control_tradeoff.svg" alt="成本与攻击风险" style="width:100%;max-width:900px"></section>'
    body += '<section><h2>逐种子配对差</h2>'
    body += table(['种子', '动态修改率', '动态 A1 命中率', '动态碰撞数/百万对'] +
                  [CONTROL_LABELS[name] for name in ARM_NAMES], detail)
    body += '<p>表内末四列为动态减固定的同用户命中率差，单位为百分点。各方案的分布与修改率使用各自完成用户，不能直接当作同一用户配对估计；完整的每种子配对人数和条件区间保存在 JSON。</p>'
    body += '<p>五个种子是模拟注册顺序与显式模拟修改行为；这些范围不代表真实用户行为的不确定性。每种子的条件 95% 区间没有合并为跨种子区间。</p></section></main>'
    (destination / 'cross_seed_controls_report.html').write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>ZipfGuard · 五种子固定策略对照</title><style>' + STYLE +
        '</style><body>' + body + '</body></html>', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description='汇总五种子固定策略百万预算配对对照')
    parser.add_argument('--input-dir', type=Path, required=True)
    parser.add_argument('--seed42-report', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    paths = {seed: args.input_dir / f'seed_{seed}' / 'report.json'
             for seed in (11, 23, 67, 101)}
    paths[42] = args.seed42_report
    export(summarize(paths), args.output_dir)
    print(args.output_dir / 'cross_seed_controls_report.html')


if __name__ == '__main__':
    main()
