"""Compare full cumulative password distributions at each registration batch."""
import math

from web.presentation import plot, table
from web.dynamic_comparison import PRESET_LABEL, displayed_controls


def strategy_label(result, name, labels):
    label = '动态策略' if name == 'dynamic' else labels.get(name, PRESET_LABEL)
    audit = (result.get('comparison', {}).get('dynamic_cost_audit') if name == 'dynamic' else
             result.get('controls', {}).get(name, {}).get('cost_audit'))
    return label + ('（成本超限，仅参考）' if audit and not audit['eligible'] else '')


def batch_distributions(result, index, control_labels):
    row = result['cohorts'][index]
    items = [('无策略', row['baseline_cumulative']),
             (strategy_label(result, 'dynamic', control_labels), row['cumulative'])]
    for name, arm in displayed_controls(result).items():
        items.append((strategy_label(result, name, control_labels), arm['timeline'][index]['cumulative']))
    return items


def rank_series(items):
    complete = all('full_rank_frequency' in summary for _, summary in items)
    series = [(name, summary.get('full_rank_frequency',
               [(rank, round(rate * summary['users'])) for rank, rate in summary['rank_curve']]))
              for name, summary in items]
    return series, complete


def comparison_domains(result, control_labels):
    summaries = [summary for i in range(len(result['cohorts']))
                 for _, summary in batch_distributions(result, i, control_labels)]
    max_rank = max((s['unique'] for s in summaries), default=1)
    max_count = max((round((s['top_mass']['1'] or 0) * s['users']) for s in summaries), default=1)
    return (1, 10 ** max(1, math.ceil(math.log10(max(1, max_rank))))), (
        1, 10 ** max(1, math.ceil(math.log10(max(1, max_count)))))


def normalized_rank_series(items, users):
    series, complete = rank_series(items)
    return [(name, [(rank / users, count / users) for rank, count in curve])
            for name, curve in series], complete


def render_registration_distribution(result, control_labels):
    rows = result['cohorts']
    x_domain, y_domain = comparison_domains(result, control_labels)
    minimum = 10 ** math.floor(math.log10(1 / max(r['end_user'] for r in rows)))
    maximum = max((s['top_mass']['1'] or 0) * s['users'] / r['end_user']
                  for i, r in enumerate(rows) for _, s in batch_distributions(result, i, control_labels))
    normalized_y = (minimum, 10 ** math.ceil(math.log10(max(maximum, minimum * 10))))
    parts = ['<div class="cohort-distributions"><h3>各策略的累计口令分布</h3>',
             '<p>选择注册进度，比较截至该批的全部用户在不同策略下的口令分布。'
             '各策略都除以当前累计注册人数 N：横轴为排名/N，纵轴为使用该口令的人数/N。'
             '两轴均为对数刻度，切换批次时保持相同范围。每条线独立排序，同一排名可能对应不同口令。</p>'
             '<p class="muted">相对排名的终点等于不同口令种类数/N，不把每条线各自缩放到 100%。'
             '例如同一口令始终被 22 人使用，在 1 万人时占 0.22%，在 10 万人时占 0.022%。'
             '相同频数仍会形成水平段，原始人数图可在下方展开。</p>',
             '<style>.cohort-distributions > .batch-distribution{display:none}'
             '.batch-options{display:flex;flex-wrap:wrap;gap:6px}'
             '.batch-options label{flex-direction:row;align-items:center;margin:2px;padding:5px}'
             '.batch-options label:has(input:checked){background:#e3f1ec;border-radius:6px}</style>',
             '<div class="batch-options" role="radiogroup" aria-label="选择注册进度">']
    for index, row in enumerate(rows):
        checked = ' checked' if index == len(rows) - 1 else ''
        parts.append(f'<label><input type="radio" name="distribution-batch" value="{index}"{checked}>'
                     f"{row['end_user']:,} 人</label>")
    parts.append('</div>')
    for index, row in enumerate(rows):
        parts.append(f'<style>.cohort-distributions:has(> .batch-options input[value="{index}"]:checked)'
                     f' > .batch-{index}{{display:block}}</style>')
        items = batch_distributions(result, index, control_labels)
        series, complete = normalized_rank_series(items, row['end_user'])
        parts.append(f'<div class="batch-distribution batch-{index}" data-cohort="{row["cohort_id"]}">'
                     f'<p><strong>第 {row["cohort_id"]} 批结束：前 {row["end_user"]:,} 名用户</strong></p>')
        if any(s['users'] != row['end_user'] for _, s in items):
            parts.append('<p class="notice">部分方案缺少最终口令。各线按实际可用口令统计，人数见辅助统计；'
                         '不能将人数减少造成的曲线降低解释成分布改善。</p>')
        if not complete:
            parts.append('<p class="notice">旧报告仅保存部分策略的头部排名，当前曲线可能缺少长尾。'
                         '重新运行后可比较各策略的完整分布。</p>')
        parts.append('<div class="normalized-rank">' + plot(
                          series, xlabel='相对排名 r/N', ylabel='使用该口令的用户占比 f(r)/N',
                          log=True, log_y=True, distinguish=True, markers=False,
                          x_format='probability', y_format='probability',
                          x_domain=(minimum, 1), y_domain=normalized_y) + '</div>')
        raw_series, _ = rank_series(items)
        parts.append('<details class="raw-distribution"><summary>查看原始排名与使用人数</summary>')
        parts.append(plot(raw_series, xlabel='口令排名 r', ylabel='使用该口令的人数 f(r)',
                          log=True, log_y=True, distinguish=True, markers=False,
                          x_format='count', y_format='count',
                          x_domain=x_domain, y_domain=y_domain))
        parts.append('</details>')
        parts.append('<details class="distribution-statistics"><summary>查看本批结束时的辅助统计</summary>')
        parts.append(table(['策略', '统计用户数', '不同口令种类', '种类数 / 用户数',
                            '每百万对用户同口令数'], [
            [name, f"{s['users']:,}", f"{s['unique']:,}",
             f"{100 * s['unique_rate']:.2f}%" if s['unique_rate'] is not None else '—',
             f"{1_000_000 * s['collision_probability']:.2f}" if s['collision_probability'] is not None else '—']
            for name, s in items]))
        parts.append('</details></div>')
    parts.append('<details><summary>图例与指标依据</summary>')
    legend_rows = [
        ['无策略', '这些用户的原始口令。'],
        ['动态策略', '每批参考此前分布调整规则，老用户保留注册时的口令。'],
    ]
    if result.get('config', {}).get('controls', {}).get('preset') == 'top15':
        legend_rows.extend([control_labels.get(name, name), '全程固定采用该网站已知规则子集；网站的其他检测未模拟。']
                           for name in displayed_controls(result))
    else:
        legend_rows.append([PRESET_LABEL, '所有批次要求长度至少 8，全程固定；动态策略首批使用同一规则。'])
    parts.append(table(['图例', '含义'], legend_rows))
    parts.append('<p>同口令概率 = Σ nᵢ(nᵢ−1) / [N(N−1)]，对应 Simpson 集中度的样本估计；'
                 '表中乘以一百万便于读数。它对热门口令的集中程度敏感。'
                 '种类数 / 用户数只是样本内多样性的描述，会随样本规模变化，不能独立证明均匀或安全。</p>'
                 '<p>依据：<a href="https://doi.org/10.1038/163688a0" target="_blank" rel="noopener">'
                 'Simpson（1949），Measurement of Diversity</a>；'
                 '口令猜测难度另见攻击曲线，参考 '
                 '<a href="https://doi.org/10.1109/SP.2012.49" target="_blank" rel="noopener">'
                 'Bonneau（2012），The Science of Guessing</a>。</p>'
                 '<p>固定策略可能要求更多用户修改。分布形状、修改成本和攻击命中率需要一起比较；'
                 '动态方法的优势需要由实验验证。</p></details></div>')
    return ''.join(parts)


def modification_series(result):
    rows = result['cohorts']
    series = [('无策略', [(row['end_user'], 0) for row in rows]),
              ('动态策略', [(row['end_user'], row['response']['modification_rate']) for row in rows])]
    from web.dynamic_comparison import STRATEGY_LABELS
    for name, arm in displayed_controls(result).items():
        series.append((STRATEGY_LABELS.get(name, PRESET_LABEL),
                       [(row['end_user'], item['response']['modification_rate'])
                        for row, item in zip(rows, arm['timeline'])]))
    return series


def render_modification_cost(result):
    parts = ['<h3>每批需要修改口令的用户比例</h3>',
             '<p>横轴为该批结束时的累计注册人数，纵轴只统计当前批次中最终口令与原口令不同的用户比例。'
             '此图衡量有多少人需要改口令；修改幅度和重试次数见下表。</p>',
             plot(modification_series(result), xlabel='该批结束时的累计注册人数',
                  ylabel='本批修改口令的用户比例', distinguish=True,
                  x_format='count', y_format='percent')]
    pending = result.get('registration_summary', {}).get('pending_users', 0)
    if pending:
        parts.append(f'<p class="notice">有 {pending:,} 名用户达到计算上限仍待处理，记录已保留。'
                     '该状态不表示用户放弃；逐批人数见下表。</p>')
    return ''.join(parts)
