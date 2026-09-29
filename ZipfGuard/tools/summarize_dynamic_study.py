"""Audit completed cohort runs and export a cross-seed, aggregate-only report."""
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
from web.presentation import STYLE, escape, table


def _pct(value):
    return f'{100 * value:.2f}%'


def _range(values, formatter):
    return f'{formatter(statistics.mean(values))}（范围 {formatter(min(values))}–{formatter(max(values))}）'


def _point(evaluation, budget):
    match = next((row for row in evaluation['minauto'] if row['budget'] == budget), None)
    if match is None or not match['complete'] or match['rate'] is None:
        raise ValueError(f'预算 {budget:,} 的攻击评价未完成')
    return match


def summarize(source, seeds, budget):
    rows = []
    source_hash = None
    for seed in seeds:
        path = source / f'seed_{seed}' / 'report.json'
        result = json.loads(path.read_text(encoding='utf-8'))
        data = result['dataset']
        if data['seed'] != seed or data['registration_occurrences'] != 100_000:
            raise ValueError(f'种子 {seed} 的数据身份或注册人数不匹配')
        if source_hash is None:
            source_hash = data['source_sha256']
        elif source_hash != data['source_sha256']:
            raise ValueError('不同种子使用了不同源语料')
        if len(result['cohorts']) != 10 or any(
                item['end_user'] - item['start_user'] + 1 != 10_000
                for item in result['cohorts']):
            raise ValueError(f'种子 {seed} 的批次人数不完整')
        if result['final_distribution']['users'] != sum(
                item['response']['completed_users'] for item in result['cohorts']):
            raise ValueError(f'种子 {seed} 的完成用户数不守恒')
        attacks = result['attacks']
        a1 = _point(attacks['A1'], budget)
        baseline = _point(attacks['baseline_completed_F'], budget)
        if not a1['all_models_completed'] or not baseline['all_models_completed']:
            raise ValueError(f'种子 {seed} 有攻击模型未完整评价')
        expected = {'frequency', 'dictionary-rules', 'character-ngram'}
        for arm_name in ('F', 'A1'):
            models = {item['run']['model']: item['run']
                      for item in attacks[arm_name]['models']}
            if set(models) != expected:
                raise ValueError(f'种子 {seed} 的 {arm_name} 主攻击模型不一致')
            if models['character-ngram']['charged_count'] < budget:
                raise ValueError(f'种子 {seed} 的 {arm_name} 字符模型未跑满 {budget:,} 次')
            if any(models[name]['stop_reason'] not in ('exhausted', 'reached_budget')
                   for name in expected):
                raise ValueError(f'种子 {seed} 的 {arm_name} 攻击器异常停止')
        completed = result['final_distribution']['users']
        paired = result['baseline_completed_distribution']
        if paired['users'] != completed:
            raise ValueError(f'种子 {seed} 的配对分布人数不一致')
        rows.append({
            'seed': seed,
            'completed_users': completed,
            'completion_rate': completed / 100_000,
            'modified_users': sum(item['response']['modified_users']
                                  for item in result['cohorts']),
            'modified_rate': sum(item['response']['modified_users']
                                 for item in result['cohorts']) / 100_000,
            'first_cohort_modified_rate': result['cohorts'][0]['response']['modification_rate'],
            'last_cohort_modified_rate': result['cohorts'][-1]['response']['modification_rate'],
            'last_minus_first_modified_percentage_points': 100 * (
                result['cohorts'][-1]['response']['modification_rate'] -
                result['cohorts'][0]['response']['modification_rate']),
            'minimum_cohort_completion_rate': min(
                item['response']['completion_rate'] for item in result['cohorts']),
            'last_cohort_modified_edit_distance_p90':
                result['cohorts'][-1]['response']['p90_edit_distance_modified'],
            'user_strength_status_counts': result['user_strength']['status_counts'],
            'exact_strength_percent_share':
                result['user_strength']['status_counts'].get('exact', 0) / 100_000,
            'baseline_paired_collision_per_million_pairs':
                paired['collision_probability'] * 1_000_000,
            'dynamic_collision_per_million_pairs':
                result['final_distribution']['collision_probability'] * 1_000_000,
            'baseline_paired_F_cracked': baseline['rate'],
            'dynamic_A1_cracked': a1['rate'],
            'cracked_difference_percentage_points':
                100 * (a1['rate'] - baseline['rate']),
            'baseline_G10': attacks['guess_counts']['baseline_completed_F']
                ['quantiles']['0.1']['guess_count'],
            'dynamic_G10': attacks['guess_counts']['dynamic_A1']
                ['quantiles']['0.1']['guess_count'],
            'updated_cohorts': [item['cohort_id'] for item in result['cohorts']
                                if item['policy_changed']],
            'ngram_charged_count': models['character-ngram']['charged_count'],
            'report': str(path),
        })
    return {
        'schema_version': 'zipfguard-dynamic-cross-seed-v1',
        'source_sha256': source_hash,
        'budget_per_model': budget,
        'interpretation': '五条独立模拟注册顺序的描述性结果；不合并用户构造置信区间',
        'runs': rows,
    }


def export(summary, destination):
    destination.mkdir(parents=True, exist_ok=True)
    rows = summary['runs']
    (destination / 'cross_seed_summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
        encoding='utf-8')
    figures = {
        'cross_seed_collision.svg': svg_only([
            ('相同完成用户原口令', [(r['seed'], r['baseline_paired_collision_per_million_pairs']) for r in rows]),
            ('动态策略', [(r['seed'], r['dynamic_collision_per_million_pairs']) for r in rows])],
            xlabel='随机种子', ylabel='每百万对用户同口令数（估计）', scatter=True),
        'cross_seed_attack.svg': svg_only([
            ('原口令冻结 F', [(r['seed'], r['baseline_paired_F_cracked']) for r in rows]),
            ('动态口令自适应 A1', [(r['seed'], r['dynamic_A1_cracked']) for r in rows])],
            xlabel='随机种子', ylabel='每模型百万预算命中率', scatter=True),
        'cross_seed_cost.svg': svg_only([
            ('修改率', [(r['seed'], r['modified_rate']) for r in rows]),
            ('完成率', [(r['seed'], r['completion_rate']) for r in rows])],
            xlabel='随机种子', ylabel='占原始用户比例', scatter=True),
    }
    for name, markup in figures.items():
        (destination / name).write_text(markup, encoding='utf-8')
    stats = [
        ('完成注册率', 'completion_rate', _pct),
        ('修改率', 'modified_rate', _pct),
        ('最后一批减第一批修改率 / 百分点',
         'last_minus_first_modified_percentage_points', lambda n: f'{n:+.2f}'),
        ('各批完成率的最低值', 'minimum_cohort_completion_rate', _pct),
        ('逐用户提升百分比可精确计算比例', 'exact_strength_percent_share', _pct),
        ('原口令同口令碰撞数 / 百万对',
         'baseline_paired_collision_per_million_pairs', lambda n: f'{n:.2f}'),
        ('动态同口令碰撞数 / 百万对',
         'dynamic_collision_per_million_pairs', lambda n: f'{n:.2f}'),
        ('原口令冻结攻击命中率', 'baseline_paired_F_cracked', _pct),
        ('动态自适应攻击命中率', 'dynamic_A1_cracked', _pct),
        ('动态减原口令命中率差 / 百分点',
         'cracked_difference_percentage_points', lambda n: f'{n:+.2f}'),
    ]
    body = ['<main><section><h1>五种子动态口令策略实验</h1>',
            '<p>每条模拟注册顺序各有 100,000 名用户、10 批；每个攻击模型最高检查 1,000,000 条不同有效候选。</p>',
            '<p>本页只汇总已经完成的整条轨迹。平均值和范围描述这五个种子，不是跨人群置信区间。</p>',
            table(['指标', '五种子均值与范围'], [
                [name, _range([r[key] for r in rows], fmt)]
                for name, key, fmt in stats]), '</section>']
    for filename, title in [
            ('cross_seed_collision.svg', '分布集中度'),
            ('cross_seed_attack.svg', '攻击命中率'),
            ('cross_seed_cost.svg', '用户成本')]:
        body.append(f'<section><h2>{title}</h2><img src="{filename}" alt="{title}" style="width:100%;max-width:900px"></section>')
    body.extend(['<section><h2>逐种子审计表</h2>',
                 table(['种子', '完成用户', '修改率', '动态碰撞数/百万对',
                        '动态 A1 命中率', '原口令 F 命中率', '原口令 G10',
                        '动态 G10', '新增规则批次'], [
                     [r['seed'], r['completed_users'], _pct(r['modified_rate']),
                      f"{r['dynamic_collision_per_million_pairs']:.2f}",
                      _pct(r['dynamic_A1_cracked']),
                      _pct(r['baseline_paired_F_cracked']),
                      r['baseline_G10'], r['dynamic_G10'],
                      ', '.join(map(str, r['updated_cohorts']))]
                     for r in rows]),
                 '<p>频次和字典攻击器正常耗尽，字符 n-gram 每个种子完成百万有效猜测。A0 百万预算仍有截断；PCFG 不进入这项百万预算主比较。</p>',
                 '<p>动态与固定策略在单种子配对试验中的取舍仍需保留：动态分布更分散，但未胜过相近成本的开发集固定规则。本五种子运行没有重复固定策略的百万 A1 攻击，不能由此推出动态优于固定策略。</p>',
                 '<p>逐用户记录均有状态；只有修改前后猜测排名都实测时才能给出精确提升百分比。其他记录保留提升下界、退化上界、未命中未决、未修改或未完成注册等状态，不把百万预算之外的排名编造为精确值。</p>',
                 '<p>最后一批已修改用户的编辑距离 P90 在五种子中均为 21，提示一部分用户需要较大改动。注册顺序是模拟的，修改行为是模型，不代表真实用户研究。各种子的完整公开报告、图表及本机私有逐用户结果在各自目录。</p>',
                 '</section></main>'])
    html = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>ZipfGuard · 五种子实验</title><style>' + STYLE +
            '</style><body>' + ''.join(body) + '</body></html>')
    (destination / 'cross_seed_report.html').write_text(html, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description='汇总并审计分批注册多种子实验')
    parser.add_argument('--input-dir', type=Path, required=True)
    parser.add_argument('--seeds', default='11,23,42,67,101')
    parser.add_argument('--budget', type=int, default=1_000_000)
    args = parser.parse_args()
    seeds = [int(x.strip()) for x in args.seeds.split(',')]
    if not seeds or len(seeds) != len(set(seeds)):
        parser.error('种子不能为空或重复')
    result = summarize(args.input_dir, seeds, args.budget)
    export(result, args.input_dir)
    print(args.input_dir / 'cross_seed_report.html')


if __name__ == '__main__':
    main()
