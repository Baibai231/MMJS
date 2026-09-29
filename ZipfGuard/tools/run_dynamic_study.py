"""Run the cohort study and export aggregate figures plus private user outcomes."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.dynamic_config import load_dynamic_config, validate_dynamic_config
from experiments.dynamic_pipeline import run_dynamic_pipeline
from web.dynamic_presentation import CONTROL_LABELS, render_dynamic_html
from web.presentation import COLORS, escape, plot


def _standalone_svg(markup, names):
    match = re.search(r'<svg\b.*?</svg>', markup, re.S)
    if not match:
        raise RuntimeError('无法从结果生成 SVG 图')
    svg = match.group().replace('<svg ',
                                '<svg xmlns="http://www.w3.org/2000/svg" ', 1)
    svg = svg.replace('viewBox="0 0 890 385"', 'viewBox="0 0 890 470"', 1)
    legend = ['<g aria-label="图例" font-size="12" fill="#20304a">']
    for index, name in enumerate(names):
        x = 75 + 380 * (index % 2)
        y = 405 + 22 * (index // 2)
        legend.append(f'<circle cx="{x}" cy="{y-4}" r="5" fill="{COLORS[index % len(COLORS)]}"/>')
        legend.append(f'<text x="{x+13}" y="{y}">{escape(name)}</text>')
    legend.append('</g>')
    return svg.replace('</svg>', ''.join(legend) + '</svg>') + '\n'


def svg_only(series, *, xlabel, ylabel, log=False, scatter=False,
             x_ticks=None, x_format=None, y_format=None):
    markup = plot(series, xlabel=xlabel, ylabel=ylabel, log=log,
                  scatter=scatter, distinguish=True, x_ticks=x_ticks,
                  x_format=x_format, y_format=y_format)
    return _standalone_svg(markup, [name for name, _ in series])


def export_figures(result, directory):
    cohorts = result['cohorts']
    baseline = result['baseline_distribution']
    paired = result['baseline_completed_distribution']
    final = result['final_distribution']
    figures = {
        'final_rank_distribution.svg': svg_only(
            [('原始全体', baseline['rank_curve']),
             ('相同完成用户的原口令', paired['rank_curve']),
             ('动态', final['rank_curve'])],
            xlabel='口令热门程度排名（1 = 最热门）', ylabel='使用这一条口令的用户比例',
            log=True, x_format='count', y_format='percent'),
        'cumulative_collision.svg': svg_only(
            [('无策略', [(r['end_user'], 1_000_000 * r['baseline_cumulative']['collision_probability'])
                       for r in cohorts]),
             ('相同完成用户的原口令', [(r['end_user'], 1_000_000 * r['baseline_completed_cumulative']['collision_probability'])
                                        for r in cohorts]),
             ('动态', [(r['end_user'], 1_000_000 * r['cumulative']['collision_probability'])
                      for r in cohorts])],
            xlabel='已尝试注册人数', ylabel='每百万对用户同口令数（估计）'),
        'cohort_cost.svg': svg_only(
            [('修改率', [(r['end_user'], r['response']['modification_rate'])
                       for r in cohorts]),
             ('未完成率', [(r['end_user'], 1-r['response']['completion_rate'])
                         for r in cohorts])],
            xlabel='已尝试注册人数', ylabel='本批用户比例', x_format='count', y_format='percent'),
        'attack_budget.svg': svg_only(
            [('冻结 F', [(p['budget'], p['rate'])
                       for p in result['attacks']['F']['minauto']]),
             ('仅知策略 A0', [(p['budget'], p['rate'])
                            for p in result['attacks']['A0']['minauto']]),
             ('自适应 A1', [(p['budget'], p['rate'])
                           for p in result['attacks']['A1']['minauto']]),
             ('无策略 F', [(p['budget'], p['rate'])
                         for p in result['attacks']['baseline_F']['minauto']]),
             ('相同完成用户原口令 F', [(p['budget'], p['rate'])
                                   for p in result['attacks']['baseline_completed_F']['minauto']])],
            xlabel='每个模型的累计猜测次数', ylabel='累计猜中的用户比例', log=True,
            x_ticks=result['config']['budgets'], x_format='count', y_format='percent'),
    }
    for cohort in cohorts:
        if cohort['policy_changed']:
            figures[f"cohort_{cohort['cohort_id']:02d}_rule_step.svg"] = svg_only(
                [('新增前', cohort['step_before']['rank_curve']),
                 ('新增后', cohort['step_after']['rank_curve']),
                 ('无策略', cohort['baseline_cohort']['rank_curve'])],
                xlabel='本批口令热门程度排名（1 = 最热门）', ylabel='使用这一条口令的用户比例',
                log=True, x_format='count', y_format='percent')
    if result['attacks']['controls']:
        attempted = result['dataset']['registration_occurrences']
        modified = sum(row['response']['modified_users'] for row in cohorts)
        comparison = [('动态历史反馈', [(modified / attempted,
                                    result['attacks']['A1']['minauto'][-1]['rate'])])]
        for name, control in result['controls'].items():
            if name in result['attacks']['controls']:
                comparison.append((CONTROL_LABELS.get(name, name),
                                   [(control['modified_users'] / attempted,
                                     result['attacks']['controls'][name]['minauto'][-1]['rate'])]))
        markup = plot(comparison, xlabel='原始用户中的修改率',
                      ylabel='最高预算自适应 Min_auto 命中率',
                      scatter=True, distinguish=True, x_format='percent', y_format='percent')
        figures['risk_cost_scatter.svg'] = _standalone_svg(
            markup, [name for name, _ in comparison])
    for filename, markup in figures.items():
        (directory / filename).write_text(markup, encoding='utf-8')
    return sorted(figures)


def main():
    parser = argparse.ArgumentParser(description='ZipfGuard 分批注册动态策略实验')
    parser.add_argument('--preset', choices=['dynamic_smoke', 'dynamic_full'],
                        default='dynamic_smoke')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--corpus', type=Path)
    parser.add_argument('--seed', type=int)
    parser.add_argument('--seeds', help='逗号分隔的多个随机种子')
    parser.add_argument('--users', type=int)
    parser.add_argument('--development', type=int)
    parser.add_argument('--cohort-size', type=int)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    if args.seed is not None and args.seeds:
        parser.error('--seed 与 --seeds 只能选择一个')
    cfg = load_dynamic_config(args.preset, args.config)
    if args.corpus:
        cfg['data']['path'] = str(args.corpus.resolve())
    if args.seed is not None:
        cfg['seed'] = args.seed
    for name, value in [('users', args.users), ('development', args.development),
                        ('cohort_size', args.cohort_size)]:
        if value is not None:
            cfg['data'][name] = value
    seeds = [int(value.strip()) for value in args.seeds.split(',')] if args.seeds else [cfg['seed']]
    if not seeds or len(set(seeds)) != len(seeds):
        parser.error('种子不能为空或重复')
    summary = []
    for seed in seeds:
        run_cfg = validate_dynamic_config({**cfg, 'seed': seed})
        run_output = (args.output_dir / f'seed_{seed}' if args.output_dir
                      and len(seeds) > 1 else args.output_dir)
        print(f'开始随机种子 {seed}', flush=True)
        result = run_dynamic_pipeline(run_cfg, output_dir=run_output,
                                      progress=lambda message: print(message, flush=True))
        directory = Path(result['user_strength']['local_private_file']).parent
        (directory / 'report.html').write_text(render_dynamic_html(result), encoding='utf-8')
        figures = export_figures(result, directory)
        point = result['attacks']['A1']['minauto'][-1]
        summary.append({'seed': seed, 'report': str(directory / 'report.json'),
                        'html': str(directory / 'report.html'),
                        'private_user_outcomes': result['user_strength']['local_private_file'],
                        'figures': figures,
                        'baseline_collision': result['baseline_distribution']['collision_probability'],
                        'dynamic_collision': result['final_distribution']['collision_probability'],
                        'adaptive_cracked_at_k': point['rate'],
                        'adaptive_budget_complete': point['complete'],
                        'k_per_model': run_cfg['budgets'][-1]})
    if len(seeds) > 1:
        directory = args.output_dir or ROOT / 'reports' / 'dynamic'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'multi_seed_summary.json').write_text(json.dumps(
            {'schema_version': 'zipfguard-dynamic-multiseed-v1',
             'comparison_scope': 'seed-level descriptive; no pooled-user interval',
             'runs': summary}, ensure_ascii=False, indent=2,
            allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps(summary[0] if len(summary) == 1 else summary,
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
