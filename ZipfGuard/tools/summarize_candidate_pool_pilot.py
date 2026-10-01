"""Publish three audited Top-10 views only after exhaustive A1 evaluation."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.registration import load_registration
from core.uniformity import concentration
from experiments.dynamic_pipeline import _response
from policy.user_response import phrase_vocabulary, weighted_pool
from tools.run_candidate_pool_pilot import SEEDS, paths_and_suffixes, replay, settings
from tools.run_dynamic_study import svg_only
from web.presentation import STYLE, plot, table


def compact_path(names):
    sections = []
    for name in names:
        if sections and sections[-1][0] == name:
            sections[-1][1] += 1
        else:
            sections.append([name, 1])
    return ' → '.join(f'{name}×{count}' for name, count in sections)


def policy_label(seed):
    length, classes, blocklist, history, patterns = seed
    parts = [f'长度≥{length}']
    if classes:
        parts.append(f'至少 {classes} 类字符')
    if blocklist:
        parts.append(f'开发集热门前 {blocklist:,} 条黑名单')
    if history:
        parts.append('历史热门前 100 条黑名单（出现≥2次，逐批累积）')
    if patterns & 1:
        parts.append('禁止同一字符连续出现≥3次')
    if patterns & 2:
        parts.append('禁止4位连续升序或降序数字片段')
    return '；'.join(parts)


def load_complete(output):
    audit = json.loads((output / 'registration_audit.json').read_text(encoding='utf-8'))
    expected = paths_and_suffixes(10)[1](0, 9)
    if audit['structural_paths'] != expected or audit['feasible_complete_paths'] + audit['cost_excluded_complete_paths'] != expected:
        raise RuntimeError('Structural path audit is incomplete')
    registered = [json.loads(line) for line in (output / 'registered_paths.jsonl').read_text(encoding='utf-8').splitlines()]
    attack_files = [output / 'attacked_paths.jsonl', *sorted(output.glob('attacked_paths_shard_*.jsonl'))]
    attacked = [json.loads(line) for attack_file in attack_files if attack_file.exists()
                for line in attack_file.read_text(encoding='utf-8').splitlines()]
    if (len(registered) != audit['feasible_complete_paths']
            or len({tuple(row['path']) for row in registered}) != len(registered)):
        raise RuntimeError('Registered path count does not match audit')
    attacks = {tuple(row['path']): row for row in attacked}
    if len(attacks) != len(attacked) or set(attacks) != {tuple(row['path']) for row in registered}:
        raise RuntimeError('Million-budget A1 results are missing or duplicated')
    rows = []
    cfg = settings()
    for row in registered:
        attack = attacks[tuple(row['path'])]
        if ([p['budget'] for p in attack['A1_minauto']] != cfg['budgets']
                or {model['name'] for model in attack['models']} != set(cfg['attackers'])
                or any(p['target_weight'] != cfg['data']['users'] for p in attack['A1_minauto'])):
            raise RuntimeError('Attack budget, model set, or target population changed')
        million = next(p for p in attack['A1_minauto'] if p['budget'] == 1_000_000)
        if (million['rate'] is None or not million['complete']
                or any(model['stop_reason'] not in ('reached_budget', 'exhausted')
                       for model in attack['models'])):
            continue
        rows.append({**row, 'A1_minauto': attack['A1_minauto'],
                     'A1_at_million': million['rate']})
    if len(rows) != len(registered):
        raise RuntimeError('Incomplete attacks remain; final Top10 must not be published')
    return audit, rows, 0


def cumulative_curve(row, cfg, data, pool, vocabulary):
    final, policies = replay(row['path'], cfg, data, pool, vocabulary)
    if sum(final.values()) != row['registered_users']:
        raise RuntimeError('Target users changed during chart generation')
    history = Counter()
    curve = []
    for index, (words, rule) in enumerate(zip(data['cohorts'], policies)):
        result = _response(words, rule,
                           range(index * cfg['data']['cohort_size'],
                                 index * cfg['data']['cohort_size'] + len(words)),
                           cfg, pool, vocabulary, records=False)
        history.update(result['final'])
        curve.append(((index + 1) * cfg['data']['cohort_size'],
                      concentration(history)['collision_probability']))
    if abs(curve[-1][1] - row['collision_probability']) > 1e-15:
        raise RuntimeError('Distribution trajectory changed during chart generation')
    return curve


def summarize(output):
    audit, rows, incomplete = load_complete(output)
    cfg = settings()
    fixed = next(row for row in rows if row['path'] == ['S01'] * 10)
    distribution = sorted(rows, key=lambda r: (r['collision_probability'],
                                                r['A1_at_million'], r['modification_rate'], r['path']))[:10]
    security = sorted(rows, key=lambda r: (r['A1_at_million'],
                                            r['collision_probability'], r['modification_rate'], r['path']))[:10]
    friendly_pool = [r for r in rows if r['collision_probability'] <= fixed['collision_probability']
                     and r['A1_at_million'] <= fixed['A1_at_million']]
    friendly = sorted(friendly_pool, key=lambda r: (r['modification_rate'],
                                                    r['batch_costs'][-1],
                                                    r['collision_probability'], r['path']))[:10]
    groups = [('distribution', distribution), ('security', security), ('cost', friendly)]
    data_config = cfg['data']
    source = Path(data_config['path'])
    if not source.is_absolute():
        source = ROOT / source
    data = load_registration(source, source_format=data_config['format'],
                             encoding=data_config['encoding'], users=data_config['users'],
                             development=data_config['development'], seed=cfg['seed'],
                             cohort_size=data_config['cohort_size'])
    pool = weighted_pool(data['development']['train'])
    vocabulary = phrase_vocabulary(data['development']['train'])
    unique_rows = {tuple(row['path']): row for _, group in groups for row in group}
    traces = {path: cumulative_curve(row, cfg, data, pool, vocabulary)
              for path, row in unique_rows.items()}
    titles = {'distribution': '分布最均匀的前 10 种方案：累计碰撞概率',
              'security': '最难被攻击的前 10 种方案：A1 累计命中率',
              'cost': '用户修改最少的前 10 种方案：每批修改率'}
    prefixes = {'distribution': '分布', 'security': '攻击', 'cost': '成本'}
    descriptions = {
        'distribution': '按全部 10 万用户的最终碰撞概率从低到高排名。横轴为累计注册人数；'
                        '纵轴为随机抽取一百万对不同用户，预计有多少对使用相同口令。越低表示口令越分散。',
        'security': '按每模型 100 万次猜测时的 A1 累计命中率从低到高排名。'
                    'A1 使用各方案实施后的独立开发口令重新训练；任意模型猜中即计为命中，同一用户只计一次。'
                    '横轴为每模型猜测预算（对数刻度），纵轴为全部 10 万用户中累计被猜中的比例。',
        'cost': '先要求最终碰撞概率及 A1 百万预算命中率均不高于固定 S01，再按全程修改人数占比从低到高排名。'
                '横轴为累计注册人数，纵轴为这一批 1 万用户中需要修改原口令的比例；后半段体现晚注册用户的成本。',
    }
    summaries = {}
    candidates = [{'id': f'S{i:02}', 'label': policy_label(seed),
                   'eligible_path_count': sum(f'S{i:02}' in row['path'] for row in rows)}
                  for i, seed in enumerate(SEEDS, 1)]
    body = ['<main class="candidate-pool-report"><section><h1>旧口径：固定首批、仅允许加严的受限搜索</h1>',
            '<p class="notice"><strong>本报告不符合最新的全量搜索口径。</strong>'
            '最新要求为每批从 15 条策略中自由选一条，允许重复，10 批共 576,650,390,625 种方案（15¹⁰）。'
            '下方 2,023 条仅覆盖旧约束空间，Top10 不能视为完整空间的最优；新空间尚未完成评价。</p>',
            f'<p>固定 S01 为首批；其余批次只能在同一组 15 条 policy 中保持或加严。'
            f'共 {audit["structural_paths"]:,} 条结构路径；'
            f'{audit["cost_excluded_complete_paths"]:,} 条因共同成本上限被精确排除；'
            f'{audit["feasible_complete_paths"]:,} 条完成注册模拟。'
            f'全部 {len(rows):,} 条均已完成百万预算 A1 评价。</p>',
            '<p>10 万名用户分为 10 批，每批 1 万人，随机种子为 42。'
            '已注册用户保留当时的口令；不模拟用户放弃。没有抽样路径，也没有生成这 15 条以外的新策略。</p>',
            '<p>共同成本上限：每批修改率≤60%；相对沿用上一批规则的新增修改率≤15个百分点；'
            '相对首批的修改率增幅≤15个百分点。某批已超限，其所有后续延伸都不可能满足全程约束，因此全部计入排除数。</p>',
            table(['固定 S01 参考', '数值'], [
                ['最终每百万对同口令数', f'{fixed["collision_probability"] * 1_000_000:.4f}'],
                ['A1 百万预算命中率', f'{fixed["A1_at_million"]:.3%}'],
                ['总体修改率', f'{fixed["modification_rate"]:.3%}'],
            ]), '</section>']
    for key, group in groups:
        if key == 'distribution':
            series = [(f'{prefixes[key]}第{i}名', [(n, value * 1_000_000) for n, value in traces[tuple(row['path'])]])
                      for i, row in enumerate(group, 1)]
            xlabel, ylabel = '累计注册人数', '每百万对不同用户中的同口令对数'
            options = {'x_format': 'count', 'x_ticks': [10_000 * i for i in range(1, 11)]}
        elif key == 'security':
            series = [(f'{prefixes[key]}第{i}名', [(p['budget'], p['rate']) for p in row['A1_minauto']])
                      for i, row in enumerate(group, 1)]
            xlabel, ylabel = '每模型猜测预算 K', '累计猜中的用户比例'
            options = {'log': True, 'x_ticks': cfg['budgets'],
                       'x_format': 'count', 'y_format': 'percent'}
        else:
            series = [(f'{prefixes[key]}第{i}名', [((j + 1) * cfg['data']['cohort_size'], rate)
                                        for j, rate in enumerate(row['batch_costs'])])
                      for i, row in enumerate(group, 1)]
            xlabel, ylabel = '累计注册人数 N', '当前批次修改率'
            options = {'x_format': 'count', 'y_format': 'percent',
                       'x_ticks': [10_000 * i for i in range(1, 11)]}
        name = f'top10_{key}.svg'
        (output / name).write_text(svg_only(series, xlabel=xlabel, ylabel=ylabel, **options), encoding='utf-8')
        summaries[key] = [{'rank': i, 'path': row['path'],
                           'collision_probability': row['collision_probability'],
                           'A1_at_million': row['A1_at_million'],
                           'modification_rate': row['modification_rate'],
                           'batch_costs': row['batch_costs'],
                           'curve': series[i - 1][1]}
                          for i, row in enumerate(group, 1)]
        body.append(f'<section id="top10-{key}"><h2>{titles[key]}</h2><p>{descriptions[key]}</p>')
        body.append(plot(series, xlabel=xlabel, ylabel=ylabel, distinguish=True, **options))
        body.append('<p class="muted">每条线是一套完整十批方案，排名对应下表。全部勾选时显示全部曲线；'
                    '悬停或键盘聚焦某个图例可突出该线。部分曲线可能重合。</p>')
        body.append(table(['排名', '批次规则路径', '最终同口令数／百万对',
                           'A1@百万命中率', '总体修改率'], [
            [i, compact_path(row['path']),
             f"{1_000_000 * row['collision_probability']:.4f}",
             f"{100 * row['A1_at_million']:.3f}%",
             f"{100 * row['modification_rate']:.3f}%"]
            for i, row in enumerate(group, 1)]))
        body.append(f'<p><a href="{name}" download>下载本图 SVG</a></p></section>')
    body.append('<section><h2>这 15 条策略分别是什么？</h2>')
    body.append(table(['编号', '完整规则', '包含它的合格路径数'],
                      [[row['id'], row['label'], row['eligible_path_count']] for row in candidates]))
    body.append('<p>S01×2 → S09×8 表示前两批用 S01，后八批用 S09。字符类别指大写字母、小写字母、数字、符号。'
                '候选包含长度、字符类别、常见黑名单、简单模式检查及少量组合，覆盖常见机制与不同强度；'
                '没有市场份额数据，因此不把这 15 条称为经统计确认的“最热门”。历史热榜黑名单是本研究的反馈机制。'
                '合格路径数为 0 表示包含它的方案都未通过本轮成本约束，不表示漏掉了该策略。</p></section>')
    body.append('<section><p>三个 Top10 均在全部合格且百万预算攻击完成的路径中排名。'
                '用户成本组还要求碰撞概率及 A1 命中率不高于固定 S01。'
                '分布并列时先比较攻击命中率，再比较修改率；攻击并列时先比较碰撞概率，再比较修改率；'
                '成本并列时先比较末批修改率，再比较碰撞概率，最后均按策略编号序列排序。'
                '这里给出的是完整路径的事后最优：能否仅凭历史反馈提前选中这些路径，仍需另行验证。'
                '这些是同一份样本内的穷举排名，不是独立测试集上的泛化结论。</p></section></main>')
    output_json = {'protocol': 'exact-fifteen-policy-timelines-v1',
                   'scope_status': 'superseded-by-free-repeated-selection',
                   'requested_structural_paths': 15 ** 10,
                   'audit': audit, 'attack_incomplete_paths': incomplete,
                   'candidate_policies': candidates,
                   'registration_users': cfg['data']['users'], 'seed': cfg['seed'],
                   'budgets': cfg['budgets'],
                   'cost_eligible_paths': len(friendly_pool),
                   'manifest': json.loads((output / 'manifest.json').read_text(encoding='utf-8')),
                   'fixed_reference': {'path': fixed['path'],
                                       'collision_probability': fixed['collision_probability'],
                                       'A1_at_million': fixed['A1_at_million'],
                                       'modification_rate': fixed['modification_rate']},
                   'top10': summaries}
    (output / 'top10_fragment.html').write_text(''.join(body), encoding='utf-8')
    (output / 'top10_report.html').write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>旧口径受限搜索 · 非15¹⁰结果</title>'
        '<style>' + STYLE + '</style><body>' + ''.join(body) + '</body></html>', encoding='utf-8')
    (output / 'top10_summary.json').write_text(json.dumps(output_json, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return output_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.output_dir.resolve())
    print(json.dumps({'paths': result['audit']['structural_paths'],
                      'feasible': result['audit']['feasible_complete_paths'],
                      'top10_counts': {name: len(rows) for name, rows in result['top10'].items()}},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
