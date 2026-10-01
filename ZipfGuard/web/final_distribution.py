"""Full rank-frequency comparison for the final-distribution panel only."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from functools import lru_cache
from pathlib import Path

from web.presentation import plot, table


def _plateau_endpoints(ranked):
    """Retain each plateau's first/last rank; no tail truncation or bin averaging."""
    points = []
    start = 1
    for rank, count in enumerate(ranked, 1):
        if rank == 1:
            previous = count
        elif count != previous:
            points.append((start, previous))
            if rank - 1 > start:
                points.append((rank - 1, previous))
            start, previous = rank, count
    if ranked:
        points.append((start, previous))
        if len(ranked) > start:
            points.append((len(ranked), previous))
    return points


@lru_cache(maxsize=4)
def _read_frequencies(filename, digest, size, modified_ns):
    # Cache aggregates only. Password strings never leave this local reader.
    before, after = Counter(), Counter()
    sha = hashlib.sha256()
    with Path(filename).open('rb') as stream:
        for line in stream:
            sha.update(line)
            record = json.loads(line)
            final = record['final_password']
            if final is not None:
                before[record['original_password']] += 1
                after[final] += 1
    if sha.hexdigest() != digest:
        raise ValueError('逐用户结果与本次报告的校验值不一致')
    return tuple(sorted(before.values(), reverse=True)), tuple(sorted(after.values(), reverse=True))


def _matches(ranked, summary):
    n = summary['users']
    return (sum(ranked) == n and len(ranked) == summary['unique']
            and all(ranked[rank - 1] == round(rate * n)
                    for rank, rate in summary['rank_curve']))


def final_rank_series(result):
    """Recover the full aggregate locally; never invent an unavailable tail."""
    if 'fixed_preset' in result.get('controls', {}):
        from web.dynamic_comparison import PRESET_LABEL
        summaries = [('无策略', result['baseline_distribution']),
                     ('动态策略', result['final_distribution']),
                     (PRESET_LABEL, result['controls']['fixed_preset']['final'])]
        return [(name, summary['full_rank_frequency']) for name, summary in summaries], True
    summaries = [result['baseline_completed_distribution'], result['final_distribution']]
    if all('full_rank_frequency' in summary for summary in summaries):
        return [(name, summary['full_rank_frequency'])
                for name, summary in zip(('策略前', '策略后'), summaries)], True
    strength = result.get('user_strength', {})
    try:
        path = Path(strength['local_private_file'])
        stat = path.stat()
        ranked = _read_frequencies(str(path), strength['private_file_sha256'],
                                   stat.st_size, stat.st_mtime_ns)
        if not all(_matches(values, summary) for values, summary in zip(ranked, summaries)):
            raise ValueError('完整频数与当前报告不一致')
        curves = [_plateau_endpoints(values) for values in ranked]
        complete = True
    except (OSError, KeyError, ValueError, TypeError, IndexError):
        curves = [[(rank, round(rate * summary['users']))
                   for rank, rate in summary['rank_curve']] for summary in summaries]
        complete = all(len(curve) == summary['unique'] for curve, summary in zip(curves, summaries))
    return [('策略前', curves[0]), ('策略后', curves[1])], complete


def render_final_distribution(result):
    if 'fixed_preset' in result.get('controls', {}):
        return _render_three_strategy_distribution(result)
    before = result['baseline_completed_distribution']
    after = result['final_distribution']
    users = after['users']
    attempted = result['dataset']['registration_occurrences']
    parts = ['<section id="final-distribution"><h2>最终分布：策略前后的口令分布</h2>',
             '<style>#final-distribution .series-1 polyline,'
             '#final-distribution .series-key-1 svg path{stroke-dasharray:8 5}</style>',
             '<p>采用口令分布常用的频数—排名图（Rank–frequency / Zipf 图）：'
             '将不同口令按使用人数从高到低排序，比较策略前后完整分布的头部和长尾。两个坐标轴均使用对数刻度。</p>']
    if users < attempted:
        label = '本次结果' if result.get('registration_summary') else '本次旧结果'
        parts.append(f'<p class="notice">{label}中，{attempted:,} 人里有 {attempted-users:,} 人没有策略后口令。'
                     f'本图按同一批 {users:,} 人比较，展示这批人的全部口令；'
                     f'它尚不代表全部 {attempted:,} 人的策略前后分布。</p>')
    else:
        parts.append(f'<p>策略前后均统计同一批 {users:,} 名用户。</p>')
    series, complete = final_rank_series(result)
    parts.append(plot(series, xlabel='口令排名 r', ylabel='使用该口令的人数 f(r)',
                      log=True, log_y=True, markers=False, distinguish=True,
                      x_format='count', y_format='count'))
    if not complete:
        parts.append('<p class="notice">当前只能读取报告中保存的头部排名，完整本机记录缺失或校验不通过。'
                     '长尾数据暂不可用，图中没有补画或推测。</p>')
    parts.append('<p>横轴：第 1、10、100、1,000…热门的口令。纵轴：该口令分别被 1、10、100…人使用。'
                 '蓝线表示策略前，橙线表示策略后；两条线各自排序，同一排名可能对应不同口令。'
                 '头部降低表示热门口令重复减少；尾端向右延伸表示不同口令的种类增多。</p>')
    parts.append(table(['指标', '策略前', '策略后'], [
        ['统计用户数', f"{before['users']:,}", f"{after['users']:,}"],
        ['不同口令的种类数', f"{before['unique']:,}", f"{after['unique']:,}"],
        ['最热门的单条口令使用人数',
         f"{round(before['top_mass']['1'] * before['users']):,}",
         f"{round(after['top_mass']['1'] * after['users']):,}"],
        ['最热门 10 个口令合计覆盖的用户比例',
         f"{100 * before['top_mass']['10']:.2f}%", f"{100 * after['top_mass']['10']:.2f}%"],
    ]))
    parts.append('<p class="muted">两轴相邻的十倍刻度间距相同；频数为 1 的尾部表示这些口令各被一人使用。'
                 '相同频数的连续排名合并为水平线段，保留完整排名范围。勾选控制显示，悬停图例可突出对应曲线。</p></section>')
    return ''.join(parts)


def _render_three_strategy_distribution(result):
    from web.dynamic_comparison import PRESET_LABEL
    summaries = [('无策略', result['baseline_distribution']),
                 ('动态策略', result['final_distribution']),
                 (PRESET_LABEL, result['controls']['fixed_preset']['final'])]
    series, _ = final_rank_series(result)
    users = result['dataset']['registration_occurrences']
    parts = ['<section id="final-distribution"><h2>最终分布：策略前后的口令分布</h2>',
             '<p>频数—排名图将口令按使用人数从高到低排序，两轴均为对数刻度。'
             '无策略保留原口令；固定预设策略全程要求长度至少 8；动态策略从同一规则开始，逐批根据历史分布调整。</p>']
    if any(s['users'] != users for _, s in summaries):
        parts.append('<p class="notice">部分方案仍有待处理口令，实际统计人数见表；不能把缺失口令造成的下降解释成改善。</p>')
    else:
        parts.append(f'<p>三种方案均统计同一批全部 {users:,} 名用户。</p>')
    parts.append(plot(series, xlabel='口令排名 r', ylabel='使用该口令的人数 f(r)',
                      log=True, log_y=True, markers=False, distinguish=True,
                      x_format='count', y_format='count'))
    parts.append('<p>每条线独立排序，同一排名可能对应不同口令。头部降低表示热门口令重复减少，'
                 '尾端向右延伸表示口令种类增多。相同使用人数形成水平段，未平滑或截断长尾。</p>')
    parts.append(table(['指标', *[name for name, _ in summaries]], [
        ['统计用户数', *[f"{s['users']:,}" for _, s in summaries]],
        ['不同口令的种类数', *[f"{s['unique']:,}" for _, s in summaries]],
        ['最热门的单条口令使用人数', *[round(s['top_mass']['1'] * s['users']) for _, s in summaries]],
        ['最热门 10 个口令合计覆盖的用户比例', *[f"{100 * s['top_mass']['10']:.2f}%" for _, s in summaries]],
    ]))
    parts.append('</section>')
    return ''.join(parts)
