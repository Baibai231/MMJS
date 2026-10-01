"""Three comparable, retrospective rankings of the evaluated site profiles."""
from __future__ import annotations

from html import escape

from policy.site_catalog import site_catalog


def render_path_search_status(result):
    """Make the gap between fixed controls and ten-step path search explicit."""
    cohorts = len(result['cohorts'])
    sites = result.get('site_catalog', site_catalog())['sites']
    current_sites = site_catalog()['sites']
    hard = sum(row['basis'] == 'partial_hard' for row in sites)
    recommended = sum(row['basis'] == 'recommended_scenario' for row in sites)
    unknown = sum(row['basis'] == 'unknown' for row in sites)
    aliases = sum(row['basis'] == 'alias' for row in sites)
    inapplicable = sum(row['basis'] == 'not_applicable' for row in sites)
    evaluated_fixed = sum(name.startswith('site_') for name in result.get('controls', {}))
    cap = result['config']['controller']['max_modification_rate'] * 100
    explicit = result['config']['controller'].get('sequence') is not None
    path_label = ('一条预先指定的十批路径' if explicit else '一条逐批控制器实际路径')
    closing = ('已展示的十批路径由配置直接指定，未通过路径搜索选出。' if explicit else
               '已展示的 Google 路径仅是首批预设与后续控制器保持的结果。')
    from web.sequence_rankings import load_sequence_search, load_exhaustive_status
    search = load_sequence_search(result)
    exhaustive = load_exhaustive_status(result)
    if exhaustive is not None and exhaustive['status'] == 'complete':
        path_status = (f'已完成 {exhaustive["exact_outcome_groups"]:,} 组 A1 重训，'
                       f'覆盖全部 {exhaustive["feasible_distinct_rule_paths"]:,} 条可行规则路径；'
                       '可在本次开发集和固定蒙特卡洛设置下给出全空间最优。')
        closing = ('形式上的 15^10 个标签序列含无可执行规则、成本不合格与执行结果重复的路径；'
                   '全空间结论仅针对可行且已定义的网站规则。')
        formal_note = ('<p>形式空间分类：15^10 = 576,650,390,625 条标签序列；'
                       'Facebook、OpenAI、WhatsApp 无法为本实验赋予可执行普通口令规则。'
                       'YouTube 与 Google 共用认证，计入可执行标签后有 12^10 条；'
                       '再剔除含超出 60% 修改率规则的路径，有 7^10 = 282,475,249 条'
                       '可行标签路径。按完全相同的规则合并为 3^10 = 59,049 条规则路径，'
                       '再按开发集训练和验证口令计数严格合并为 2,304 个 A1 结果组。</p>')
    elif search is not None and search.get('status') == 'candidate_search_complete_global_A1_optimum_unproven':
        formal_note = ''
        path_status = (f'已筛选 {search["screened_paths"]:,} 条不同规则路径，并对 '
                       f'{search["a1_evaluated_paths"]:,} 条候选重新训练 PCFG A1。'
                       '完整路径候选已选出，但尚无全空间 A1 最优性证明。')
        actual = [row['policy']['name'] for row in result['cohorts']]
        if actual == search['a1_rows'][0]['path']:
            closing = ('本报告先前运行的实际路径与开发集所选候选一致；'
                       '运行当时尚未完成路径搜索，属于复用既有同种子测试结果。')
    elif search is not None:
        formal_note = ''
        path_status = (f'正在筛选路径或重训 PCFG A1；已评价 '
                       f'{search.get("a1_evaluated_paths", 0):,} 条候选。')
    else:
        formal_note = ''
        path_status = '尚未搜索完整路径空间，不能发布“最终最优动态策略”。'
    added = [row['site'] for row in current_sites
             if row['basis'] == 'partial_hard' and any(old['id'] == row['id']
             and old['basis'] == 'unknown' for old in sites)]
    current_count = sum(row['basis'] in ('partial_hard', 'recommended_scenario')
                        for row in current_sites)
    current_hard = sum(row['basis'] == 'partial_hard' for row in current_sites)
    from experiments.site_sequence_space import cost_feasible_space
    cost_audit = cost_feasible_space(result)
    cost_note = (
        f'<p>按当前每批 {cap:.0f}% 修改率上限，只做合规成本筛选后，'
        f'强制规则仍有 {cost_audit["hard_label_paths"]:,} 条标签路径；'
        f'含建议场景有 {cost_audit["all_label_paths"]:,} 条。'
        f'按相同可编码规则合并后为 {cost_audit["distinct_all_rule_paths"]:,} 条规则序列。'
        '这一筛选尚未计算任何混合路径的 PCFG A1 命中率，不能由这些计数选出最好路径。</p>'
        if cost_audit['status'].startswith('cost_feasible_structure_only') else '')
    new_evidence = (f'<p>报告生成后新增官方可执行证据：{escape("、".join(added))}。'
                    f'现在有 {current_hard} 条可编码的强制规则子集，'
                    f'仅用这类规则是 {current_hard}^{cohorts} = {current_hard**cohorts:,} 条标签序列；'
                    f'加上建议场景共 {current_count} 条，'
                    f'{current_count}^{cohorts} = {current_count**cohorts:,} 条标签序列。'
                    '新增策略尚未参加本报告评价，不能把旧结果算作已覆盖。</p>' if added else '')
    return (
        '<section id="path-search-status" class="notice">'
        '<h2>十批策略序列：当前搜索状态</h2>'
        f'<p>目标是 {cohorts} 批用户各自从网站策略中选择一条，允许重复且首批也应参与选择。'
        f'15 条标签对应 15^{cohorts} = {15**cohorts:,} 条结构序列。'
        f'当前报告只评价了 {evaluated_fixed} 条全程固定的网站策略路径和{path_label}；'
        '三项 Top10 图是固定策略对照，尚不是十批序列的 Top10。</p>'
        f'<p>队友清单中可编码的场景为 {hard} 条已知强制规则子集和 {recommended} 条建议采纳场景；'
        f'另有 {unknown} 条规则不明、{aliases} 条继承现有认证、{inapplicable} 条不适用传统口令。'
        '这些缺口未补齐前，15 选 1 的实验无法定义完整评价。</p>'
        + new_evidence + cost_note + formal_note
        + f'<p>路径优化目标已定为：各批用户修改率不超过 {cap:.0f}%，'
        '在可行路径中最小化 PCFG A1 于 10^6 次估计猜测的累计命中率，'
        '同分再比较最终口令碰撞概率。路径应在开发/验证数据上选定，再在独立用户集上评价；'
        '如果用未来 10 万用户的结果挑路径，那是事后最优序列，不能称为逐批在线调整。</p>'
        f'<p><strong>结论状态：{escape(path_status)}</strong>'
        f'{escape(closing)}</p>'
        '</section>'
    )


METRICS = (
    ('distribution', '分布：最终口令碰撞概率', '每百万对用户中使用同一口令的对数', '次 / 百万对'),
    ('guessing', '猜测：PCFG A1 累计命中率', '10^6 次估计猜测预算；分母为全部原始用户', '%'),
    ('modification', '用户修改：原口令需要修改的比例', '分母为全部原始用户', '%'),
)


def ranking_rows(result):
    """Only completed, named website scenarios enter a ranking; never pad to ten."""
    population = result['dataset']['registration_occurrences']
    budget = 10**6 if 10**6 in result['config']['budgets'] else result['config']['budgets'][-1]
    attacks = result['attacks']['by_strategy']
    rows = []
    for site in result.get('site_catalog', site_catalog())['sites']:
        key = 'site_' + site['id']
        control = result['controls'].get(key)
        evaluation = attacks.get(key, {}).get('A1', {}).get('minauto', [])
        point = next((p for p in evaluation if p['budget'] == budget), None)
        if control is None or point is None:
            continue
        rows.append({
            'key': key, 'site': site['site'], 'position': site['list_position'],
            'basis': site['basis'],
            'distribution': control['final']['collision_probability'] * 1_000_000,
            'guessing': (point['rate'] * 100 if point['rate'] is not None
                         and (point.get('estimated') or point.get('complete')) else None),
            'modification': control['modified_users'] / population * 100,
            'outside_model_support': point.get('outside_model_support_weight'),
            'outside_model_support_rate': (point.get('outside_model_support_weight') or 0) / population * 100,
            'budget': budget,
        })
    return rows


def ranked(rows, metric):
    return sorted((row for row in rows if row[metric] is not None),
                  key=lambda row: (row[metric], row['position']))[:10]


def ranking_svg(rows, metric, title, subtitle, unit, selected):
    ordered = ranked(rows, metric)
    width, left, bar_width, line_height = 1130, 292, 520, 42
    height = 103 + line_height * len(ordered)
    largest = max((row[metric] for row in ordered), default=1) or 1
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
             'role="img" style="max-width:100%;height:auto;background:#fff">',
             f'<title>{escape(title)}</title>',
             f'<text x="20" y="29" font-size="20" font-weight="700" fill="#173028">{escape(title)}</text>',
             f'<text x="20" y="52" font-size="13" fill="#456a61">{escape(subtitle)} · 数值越低越好</text>']
    for index, row in enumerate(ordered):
        y = 80 + index * line_height
        fill = '#277a61' if row['basis'] == 'partial_hard' else '#5278b3'
        selected_here = row['key'] == selected
        label = f'{index + 1}. {row["site"]}' + (' ★' if selected_here else '')
        value = row[metric]
        bar = max(1, bar_width * value / largest)
        display = (f'{value:.2f}% · 模型未覆盖 {row["outside_model_support_rate"]:.2f}%'
                   if metric == 'guessing' else
                   f'{value:.4f}{unit}' if metric == 'distribution' and value < 0.1 else
                   f'{value:.2f}{unit}')
        parts.extend((
            f'<text x="20" y="{y+17}" font-size="14" fill="#173028">{escape(label)}</text>',
            f'<rect x="{left}" y="{y}" width="{bar_width}" height="24" rx="4" fill="#edf2f0"/>',
            f'<rect x="{left}" y="{y}" width="{bar:.2f}" height="24" rx="4" fill="{fill}"/>',
            f'<text x="{left+bar_width+12}" y="{y+17}" font-size="13" fill="#173028">{escape(display)}</text>',
        ))
    parts.append(f'<text x="20" y="{height-8}" font-size="12" fill="#456a61">'
                 '绿色：已知强制规则子集　蓝色：建议采纳场景　★：本次动态策略使用</text></svg>')
    return ''.join(parts)


def render_policy_rankings(result):
    rows = ranking_rows(result)
    if not rows:
        return '<section id="policy-rankings"><h2>三项策略排序</h2><p>尚无可比较的网站策略结果。</p></section>'
    selected = result['cohorts'][0]['policy']['name']
    parts = ['<section id="policy-rankings"><h2>固定网站策略的三项指标排序</h2>',
             '<p>在同一批原始用户、同一随机种子和同一 PCFG 估计口径下，分别按分布碰撞、'
             'A1 猜测命中、用户修改比例从低到高排序。每图最多列出前 10 个已完成评价的网站场景；'
             '各图是单目标排序，不能把三个第一名直接合并成一条策略。猜测图中的模型未覆盖用户仍留在分母中；低命中率不能单独解释为安全。</p>']
    for metric, title, subtitle, unit in METRICS:
        ordered = ranked(rows, metric)
        parts.append(f'<h3>{escape(title)} · 前 {len(ordered)}</h3>')
        parts.append(ranking_svg(rows, metric, title, subtitle, unit, selected))
        parts.append('<p class="actions">'
                     f'<a class="figure-link" href="top10_{metric}.svg">下载此图 SVG</a></p>')
    incomplete = len(rows) - len(ranked(rows, 'guessing'))
    parts.append(f'<p class="muted">排序中的“更好”仅指该图的指标更低。本报告评价了 {len(rows)} 个网站场景；'
                 'YouTube 与 Google 共用认证，其余规则不明或不适用的网站不进入排序。'
                 '建议场景表示假设用户采纳建议，不代表网站真实强制规则。相同规则的站点可能完全并列。'
                 f'{"猜测评价未完成的场景不参与该图排名。" if incomplete else ""}</p>')
    if result['config'].get('controller', {}).get('sequence') is not None:
        from web.sequence_rankings import load_exhaustive_status
        exact = load_exhaustive_status(result)
        selected = (exact is not None and exact['status'] == 'complete'
                    and result['config']['controller']['sequence'] ==
                        exact['best_evaluated']['path'])
        if selected:
            parts.append('<h3>本次指定路径的性质</h3><p>这条十批路径由开发集的可行规则'
                         '全空间 A1 搜索选出，再在本报告的用户集上评价。它与固定 Google '
                         '规则完全相同，不能证明动态切换优于固定策略。</p></section>')
        else:
            parts.append('<h3>本次指定路径的性质</h3><p>这条十批规则序列由配置直接指定。'
                         '其分布、猜测与用户修改结果可以与固定策略比较；'
                         '尚无证据表明它是路径搜索的最优策略。</p></section>')
        return ''.join(parts)
    decisions = result.get('decisions', [])
    changed = sum(d['status'] == 'updated' for d in decisions)
    first = next((r for r in rows if r['key'] == selected), None)
    chosen_label = first['site'] if first else selected
    parts.append('<h3>当前逐批控制器为何保持首批预设</h3>')
    parts.append(f'<p>首批预设为 {escape(chosen_label)} 的可编码规则，并非由事后三项指标挑出；后续 {len(decisions)} 次决策中'
                 f'更新了 {changed} 次。控制器只读取已完成批次的分布和独立开发预览，'
                 '先要求无待处理用户、满足修改率上限及头部风险约束，再按预测碰撞概率选择。'
                 'A1 攻击结果是事后对照，不进入当批选择，因此不能拿这三张事后排名图冒充控制器的在线依据。</p>')
    if decisions:
        first_decision = decisions[0]
        rejected = [(c['action'], c['rejection_reasons']) for c in first_decision['candidates']
                    if c['action'] != 'hold' and c['rejection_reasons']]
        examples = '; '.join(f'{escape(name)}：{escape("、".join(reasons))}'
                             for name, reasons in rejected[:3])
        if examples:
            parts.append(f'<p>例如第 {first_decision["next_cohort"]} 批的预览中，{examples}。'
                         '这解释了为何没有只按最低碰撞率或最低修改率切换策略。</p>')
    by_key = {row['key']: row for row in rows}
    if selected == 'site_google' and all(key in by_key for key in
                                         ('site_google', 'site_github', 'site_netflix', 'site_amazon')):
        google, github = by_key['site_google'], by_key['site_github']
        netflix, amazon = by_key['site_netflix'], by_key['site_amazon']
        cap = result.get('config', {}).get('controller', {}).get('max_modification_rate')
        cap_text = f'，超过 {cap * 100:.0f}% 的控制器上限' if cap is not None else ''
        parts.append(f'<p>三图给出的取舍是：GitHub 的最终碰撞概率低于 Google，但需修改 '
                     f'{github["modification"]:.2f}% 用户{cap_text}；Netflix 与 Amazon 只需修改约 '
                     f'{min(netflix["modification"], amazon["modification"]):.2f}%，'
                     f'但碰撞概率约为每百万对 {netflix["distribution"]:.2f} 次，'
                     f'高于 Google 的 {google["distribution"]:.2f} 次。'
                     'X、TikTok、Yahoo、LinkedIn 等较严的建议场景会让更多用户修改口令，且其较低 PCFG 命中率伴随较高的模型未覆盖比例。'
                     '因此保留 Google 是本次预设与约束下的控制器结果，不能作为十批序列的最优性证明。</p>')
        if 'site_reddit' in by_key and 'site_wikipedia' in by_key:
            parts.append('<p>Google、Wikimedia 与 Reddit 建议场景在本实验都退化为至少 8 字符，三项指标并列。'
                         'Google 是首批预设，不表示它在这些等效规则中统计上更优。</p>')
    if changed == 0:
        parts.append('<p class="notice">本次后续批次没有发生策略切换，因此只能称为“动态选择机制保持了首批规则”。'
                     '它与对应固定规则表现重合，当前实验不能证明动态调整优于固定策略，也没有找到十批路径的全局最优。</p>')
    parts.append('</section>')
    return ''.join(parts)
