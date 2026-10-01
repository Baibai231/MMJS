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
from web.final_distribution import final_rank_series
from web.registration_distribution import batch_distributions, rank_series, comparison_domains, modification_series, normalized_rank_series
from web.dynamic_evidence import collision_series, stage_attack_series
from web.dynamic_comparison import attack_series, attack_groups, displayed_controls
from web.policy_rankings import METRICS, ranking_rows, ranking_svg
from web.sequence_rankings import export_sequence_figures
from web.presentation import COLORS, escape, plot


def _standalone_svg(markup, names):
    match = re.search(r'<svg\b.*?</svg>', markup, re.S)
    if not match:
        raise RuntimeError('无法从结果生成 SVG 图')
    svg = match.group().replace('<svg ',
                                '<svg xmlns="http://www.w3.org/2000/svg" ', 1)
    svg = svg.replace('viewBox="0 0 890 385"', f'viewBox="0 0 1050 {max(470, 430 + 22 * ((len(names) + 1) // 2))}"', 1)
    legend = ['<g aria-label="图例" font-size="12" fill="#20304a">']
    samples = re.findall(r'<label class="series-key-\d+">.*?(<svg\b.*?</svg>)', markup, re.S)
    for index, name in enumerate(names):
        x = 75 + 480 * (index % 2)
        y = 405 + 22 * (index // 2)
        if index < len(samples):
            sample = re.sub(r' style="[^"]*"', '', samples[index])
            sample = sample.replace('<svg ', f'<svg x="{x}" y="{y-12}" width="44" height="16" ', 1)
            legend.append(sample)
        else:
            legend.append(f'<circle cx="{x+22}" cy="{y-4}" r="5" fill="{COLORS[index % len(COLORS)]}"/>')
        legend.append(f'<text x="{x+52}" y="{y}">{escape(name)}</text>')
    legend.append('</g>')
    return svg.replace('</svg>', ''.join(legend) + '</svg>') + '\n'


def svg_only(series, *, xlabel, ylabel, log=False, scatter=False,
             x_ticks=None, x_format=None, y_format=None, log_y=False, markers=True,
             x_domain=None, y_domain=None):
    markup = plot(series, xlabel=xlabel, ylabel=ylabel, log=log,
                  scatter=scatter, distinguish=True, x_ticks=x_ticks,
                  x_format=x_format, y_format=y_format, log_y=log_y, markers=markers,
                  x_domain=x_domain, y_domain=y_domain)
    return _standalone_svg(markup, [name for name, _ in series])


def export_figures(result, directory):
    cohorts = result['cohorts']
    baseline = result['baseline_distribution']
    paired = result['baseline_completed_distribution']
    final = result['final_distribution']
    final_series, full_final_curve = final_rank_series(result)
    figures = {
        'final_rank_distribution.svg': svg_only(
            final_series, xlabel='口令排名 r' + ('' if full_final_curve else '（仅已保存头部）'),
            ylabel='使用该口令的人数 f(r)', log=True, log_y=True, markers=False,
            x_format='count', y_format='count'),
        'cohort_cost.svg': svg_only(
            modification_series(result),
            xlabel='累计注册人数', ylabel='本批用户修改比例', x_format='count', y_format='percent'),
        'attack_budget.svg': svg_only(
            attack_series(result),
            xlabel='每个模型的累计猜测次数', ylabel='累计猜中的用户比例', log=True,
            x_format='power10', y_format='percent', y_domain=(0, 1)),
    }
    if 'monte_carlo' in result['config']:
        ranking_candidates = ranking_rows(result)
        selected = result['cohorts'][0]['policy']['name']
        for metric, title, subtitle, unit in METRICS:
            figures[f'top10_{metric}.svg'] = ranking_svg(
                ranking_candidates, metric, title, subtitle, unit, selected) + '\n'
        for level in ('F', 'A0', 'A1'):
            level_series = [(name, [(p['budget'], p['rate']) for p in evaluations[level]['minauto']])
                            for name, evaluations in attack_groups(result) if level in evaluations]
            ceiling = max((rate for _, points in level_series for _, rate in points
                           if rate is not None), default=0)
            figures[f'attack_{level}.svg'] = svg_only(
                level_series, xlabel='累计攻击次数（估计猜测预算）',
                ylabel='累计猜出的口令比例（全部用户）', log=True,
                x_format='power10', y_format='percent',
                y_domain=(0, min(1, max(.01, ceiling * 1.12))))
    x_domain, y_domain = comparison_domains(result, CONTROL_LABELS)
    collisions = collision_series(result, CONTROL_LABELS)
    figures['cumulative_collision.svg'] = svg_only(
        collisions, xlabel='累计注册人数 N', ylabel='两名用户使用同一口令的概率',
        x_format='count', y_format='probability', x_ticks=[row['end_user'] for row in cohorts],
        log_y=all(y is not None and y > 0 for _, points in collisions for _, y in points))
    if 'registration_stages_F' in result['attacks']:
        for budget in result['config']['budgets']:
            figures[f'registration_attack_F_{budget}.svg'] = svg_only(
                stage_attack_series(result, CONTROL_LABELS, budget),
                xlabel='累计注册人数 N', ylabel='累计猜中的用户比例',
                x_format='count', y_format='percent', y_domain=(0, 1),
                x_ticks=[row['end_user'] for row in cohorts])
    for index, cohort in enumerate(cohorts):
        series, complete = rank_series(batch_distributions(result, index, CONTROL_LABELS))
        figures[f"registration_distribution_{cohort['cohort_id']:02d}.svg"] = svg_only(
            series, xlabel='口令排名 r' + ('' if complete else '（仅已保存头部）'),
            ylabel='使用该口令的人数 f(r)', log=True, log_y=True, markers=False,
            x_format='count', y_format='count', x_domain=x_domain, y_domain=y_domain)
        normalized, _ = normalized_rank_series(batch_distributions(result, index, CONTROL_LABELS), cohort['end_user'])
        figures[f"registration_distribution_normalized_{cohort['cohort_id']:02d}.svg"] = svg_only(
            normalized, xlabel='相对排名 r/N', ylabel='使用该口令的用户占比 f(r)/N',
            log=True, log_y=True, markers=False, x_format='probability', y_format='probability',
            x_domain=(1 / result['dataset']['registration_occurrences'], 1),
            y_domain=(1 / result['dataset']['registration_occurrences'], 1))
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
        comparison = [('无策略', [(0, result['attacks']['baseline_F']['minauto'][-1]['rate'])]),
                      ('动态策略', [(modified / attempted, result['attacks']['A1']['minauto'][-1]['rate'])])]
        for name, control in displayed_controls(result).items():
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
    return sorted([*figures, *export_sequence_figures(result, directory)])


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
