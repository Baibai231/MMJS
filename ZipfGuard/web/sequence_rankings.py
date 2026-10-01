"""Development-only rankings for ten-cohort site-rule paths."""
from __future__ import annotations

import json
from html import escape
from pathlib import Path

from experiments.dynamic_config import ROOT

SEARCH = ROOT / 'reports' / 'dynamic' / 'top15_sequence_search.json'
EXHAUSTIVE = ROOT / 'reports' / 'dynamic' / 'top15_exhaustive_a1'
NAMES = {'site_google': 'Google', 'site_amazon': 'Amazon', 'site_netflix': 'Netflix'}
METRICS = (
    ('distribution', '分布：最终口令碰撞概率', 'distribution_top10', 'collision', 1_000_000, '次 / 百万对'),
    ('guessing', '猜测：PCFG A1 命中率 @ 10⁶', 'a1_rows', 'a1', 100, '%'),
    ('modification', '用户修改：原口令修改率', 'modification_top10', 'modification_rate', 100, '%'),
)


def load_sequence_search(result):
    try:
        search = json.loads(SEARCH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    same_development = (search.get('dataset', {}).get('development_hashes') ==
                        result.get('dataset', {}).get('development_hashes'))
    if not same_development:
        # The seed-43 100k evaluation is intentionally a new sample. It may
        # display the seed-42 selection evidence only for the selected path.
        try:
            exact = json.loads((EXHAUSTIVE / 'status.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        if not (exact.get('status') == 'complete'
                and result.get('config', {}).get('seed') == 43
                and result.get('config', {}).get('controller', {}).get('sequence') ==
                    exact['best_evaluated']['path']
                and result.get('dataset', {}).get('registration_occurrences') == 100000):
            return None
    if search.get('budget') != 10**6:
        return None
    return search


def load_exhaustive_status(result):
    try:
        status = json.loads((EXHAUSTIVE / 'status.json').read_text(encoding='utf-8'))
        manifest = json.loads((EXHAUSTIVE / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    same_development = (manifest.get('development_hashes') ==
                        result.get('dataset', {}).get('development_hashes'))
    if not same_development:
        if not (status.get('status') == 'complete'
                and result.get('config', {}).get('seed') == 43
                and result.get('config', {}).get('controller', {}).get('sequence') ==
                    status['best_evaluated']['path']
                and result.get('dataset', {}).get('registration_occurrences') == 100000):
            return None
    if status.get('fingerprint') != manifest.get('fingerprint'):
        return None
    return status


def load_exhaustive_rows(result):
    status = load_exhaustive_status(result)
    if status is None or status['status'] != 'complete':
        return None
    rows = {}
    try:
        with (EXHAUSTIVE / 'a1_checkpoint.jsonl').open(encoding='utf-8') as stream:
            for line in stream:
                row = json.loads(line)
                rows[row['group_id']] = row
    except (OSError, ValueError):
        return None
    if len(rows) != status['exact_outcome_groups']:
        return None
    return sorted(rows.values(), key=lambda row: (row['a1']['hits'], row['collision'], row['path']))


def load_formal_space_audit():
    try:
        return json.loads((EXHAUSTIVE / 'formal_space_audit.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def load_high_sample_check(result):
    if load_exhaustive_status(result) is None:
        return None
    try:
        check = json.loads((EXHAUSTIVE / 'top10_high_sample_check.json').read_text(encoding='utf-8'))
        manifest = json.loads((EXHAUSTIVE / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if (check.get('status') != 'complete' or
            check.get('development_hashes') != manifest.get('development_hashes')):
        return None
    return check


def _value(row, field):
    return row['a1']['rate'] if field == 'a1' else row[field]


def _path(row):
    return ' → '.join(NAMES.get(name, name) for name in row['path'])


def sequence_svg(rows, title, field, scale, unit):
    rows = rows[:10]
    width, height = 1200, 120 + 55 * len(rows)
    left, bar_width = 590, 360
    largest = max((_value(r, field) * scale for r in rows), default=1) or 1
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
             'role="img" style="max-width:100%;height:auto;background:#fff">',
             f'<title>{escape(title)}</title>',
             f'<text x="20" y="30" font-size="22" font-weight="700" fill="#173028">{escape(title)}</text>',
             '<text x="20" y="58" font-size="15" fill="#456a61">第 1 → 10 批 · G = Google · A = Amazon · N = Netflix · 数值越低越好</text>']
    for i, row in enumerate(rows):
        y = 88 + i * 55
        value = _value(row, field) * scale
        label = _path(row)
        short = (NAMES.get(row['path'][0], row['path'][0]) + ' × 10 批'
                 if len(set(row['path'])) == 1 else
                 ' → '.join({'site_google': 'G', 'site_amazon': 'A', 'site_netflix': 'N'}.get(n, n)
                            for n in row['path']))
        parts.extend((
            f'<text x="20" y="{y+17}" font-size="16" fill="#173028"><title>{escape(label)}</title>{i+1}. {escape(short)}</text>',
            f'<rect x="{left}" y="{y}" width="{bar_width}" height="24" rx="4" fill="#edf2f0"/>',
            f'<rect x="{left}" y="{y}" width="{max(1, bar_width * value / largest):.2f}" '
            'height="24" rx="4" fill="#277a61"/>',
            f'<text x="{left+bar_width+12}" y="{y+17}" font-size="16" fill="#173028">'
            f'{value:.4f} {escape(unit)}</text>',
        ))
    parts.append('</svg>')
    return ''.join(parts)


def render_sequence_rankings(result):
    search = load_sequence_search(result)
    if search is None:
        return '<section id="sequence-rankings"><h2>十批路径三指标排序</h2><p>当前报告尚无同一开发样本的路径搜索结果。</p></section>'
    exhaustive_rows = load_exhaustive_rows(result)
    guess_scope = ('全部效果不同的可行路径组' if exhaustive_rows is not None else
                   f'{search["a1_evaluated_paths"]:,} 条已重训候选')
    parts = ['<section id="sequence-rankings"><h2>十批路径三指标排序</h2>',
             '<p>每行是一条完整的十批规则路径，三个指标均为越低越好。各图独立排序，猜测排名作为最终选路的主目标。</p>',
             '<details><summary>查看搜索覆盖、等效合并与高采样复核</summary>',
             f'<p>在独立开发/验证样本上，对 {search["screened_paths"]:,} 条成本可行的不同规则路径'
             '完成冻结 PCFG 筛选、分布碰撞与用户修改计算；'
             f'猜测图的排序范围是{guess_scope}。'
             + ('在全空间评价完成前，猜测图不能视为全局前 10。</p>'
                if exhaustive_rows is None else '等效路径只绘制一个代表。</p>')]
    audit = load_formal_space_audit()
    if audit is not None:
        parts.append('<p class="notice">15¹⁰ 条形式路径已逐类核算：'
                     f'{audit["unscorable_label_paths_due_to_unknown_or_inapplicable"]:,} 条包含无法定义普通口令规则的网站，'
                     f'{audit["cost_infeasible_executable_label_paths"]:,} 条违反每批 60% 修改率上限，'
                     f'{audit["cost_feasible_label_paths_including_youtube_alias"]:,} 条标签路径成本可行。'
                     'YouTube 与 Google 共用认证规则；重复规则标签合并后有 '
                     f'{audit["distinct_cost_feasible_rule_paths"]:,} 条不同规则路径。'
                     '只有这部分可赋予可复核的 PCFG A1 分数；前两类不计作已运行 A1。</p>')
    exhaustive = load_exhaustive_status(result)
    if exhaustive is not None:
        state = ('已完成' if exhaustive['status'] == 'complete' else '正在进行')
        parts.append(f'<p class="notice">全空间 A1 逐组重训{state}：'
                     f'{exhaustive["evaluated_groups"]:,}/{exhaustive["exact_outcome_groups"]:,} '
                     '组已完成，严格等效地覆盖 '
                     f'{exhaustive["covered_rule_paths"]:,}/{exhaustive["feasible_distinct_rule_paths"]:,} '
                     '条可行规则路径。只有剩余组为零，才可给出可行路径空间内的全局排序。</p>')
    if result['config']['seed'] == 43:
        parts.append('<p>以上路径排序来自种子 42 的开发/验证样本；本报告的 10 万用户'
                     '采用种子 43，作为选中路径的另一轮全量评价。</p>')
    high_sample = load_high_sample_check(result)
    if high_sample is not None:
        first, second = high_sample['rows'][:2]
        parts.append('<p>前 10 个等效组以 100,000 次蒙特卡洛采样复核：'
                     f'Google × 10 批命中 {first["a1_hits_100k_mc"]:,}/20,000，'
                     f'次名命中 {second["a1_hits_100k_mc"]:,}/20,000；'
                     '第一名保持不变。这次高采样复核仅覆盖原搜索前 10 组，'
                     '不构成 100,000 次采样下的全空间重新排序。</p>')
    parts.append('</details>')
    for slug, title, key, field, scale, unit in METRICS:
        rows = exhaustive_rows if field == 'a1' and exhaustive_rows is not None else search.get(key, [])
        if field == 'a1':
            rows = sorted(rows, key=lambda r: (r['a1']['rate'], r['collision']))[:10]
        parts.append(f'<div class="ranking-chart" id="ranking-{slug}"><h3>{escape(title)} · 前 {min(10, len(rows))}</h3>')
        parts.append(sequence_svg(rows, title, field, scale, unit))
        parts.append(f'<p class="actions"><a class="figure-link" href="sequence_top10_{slug}.svg">下载此图 SVG</a></p>')
        parts.append('</div>')
    if search.get('a1_rows'):
        best = exhaustive_rows[0] if exhaustive_rows is not None else search['a1_rows'][0]
        parts.append('<h3>当前候选策略的选取依据</h3>')
        scope = (f'{len(exhaustive_rows)} 组全部可行路径结果' if exhaustive_rows is not None
                 else f'已重训的 {search["a1_evaluated_paths"]} 条候选')
        final_note = ('下文是选中路径的另一轮 10 万用户结果。</p>'
                      if result['config']['seed'] == 43 else
                      '最终 10 万用户测试须单独运行。</p>')
        parts.append(f'<p>在{scope}中，'
                     f'{escape(_path(best))} 的 PCFG A1 在 10⁶ 次估计猜测下命中率最低：'
                     f'{best["a1"]["rate"]*100:.2f}%。同分时按最终口令碰撞概率排序。'
                     '所有批次均先通过 60% 修改率上限。'
                     + ('这是开发集上的可行路径全空间最优结果；'
                        if exhaustive_rows is not None else
                        '这是一条开发集选出的完整十批候选路径；全空间 A1 最优性尚未证明，')
                     + final_note)
        if exhaustive_rows is not None and all(name == 'site_google' for name in best['path']):
            parts.append('<p>胜出路径十批都采用 Google 规则，与固定 Google 场景的规则和结果相同；'
                         '本实验没有观察到动态切换优于固定 Google 的收益。</p>')
    parts.append('</section>')
    return ''.join(parts)


def export_sequence_figures(result, output_dir):
    search = load_sequence_search(result)
    if search is None:
        return []
    names = []
    exhaustive_rows = load_exhaustive_rows(result)
    for slug, title, key, field, scale, unit in METRICS:
        rows = exhaustive_rows if field == 'a1' and exhaustive_rows is not None else search.get(key, [])
        if field == 'a1':
            rows = sorted(rows, key=lambda r: (r['a1']['rate'], r['collision']))[:10]
        name = f'sequence_top10_{slug}.svg'
        (Path(output_dir) / name).write_text(sequence_svg(rows, title, field, scale, unit), encoding='utf-8')
        names.append(name)
    return names
