"""Observed dual-attack reports, separate from historical PCFG estimates."""
from web.presentation import STYLE, escape, plot, table
from web.study_dashboard import DASHBOARD_STYLE


def render_dual_report(result, document=True):
    from web.intervention_presentation import chart_specs, comparison_labels, pct, ideal_reference_html
    from web.round_parameter_presentation import round_parameter_html
    arms, cfg = result['arms'], result['config']
    labels, specs = comparison_labels(result), chart_specs(result)
    dynamic = arms['google_dynamic']
    n, budget = cfg['data']['users'], cfg['attack_models']['threshold']
    sizes = result['google_round_zipf']['comparison_budget']
    parts = ['<main class="intervention-report"><section id="intervention-overview"><h1>分布选群体，双攻击验证个体</h1>',
             f'<p>本次 {n:,} 个模拟账户；动态组执行 {len(dynamic["rounds"])} 轮，'
             f'基础规则之后通知 {dynamic["final"]["ledger"]["adaptive_affected"]:,} 人。</p>',
             f'<p>每个新口令均须通过固定 PCFG 与作者版 OMEN 各 {budget:,} 次尝试检查，'
             '随后按 W1 改善选择方案。通过门槛不要求每个人的猜测次数严格增加。'
             '攻击曲线面积仅作诊断，不再决定方案准入。</p>',
             '<p>联合曲线横轴 B 表示每模型 B 次尝试，最多 2B 次总尝试；'
             '重复候选占用尝试次数，同一账户只计一次命中。曲线来自实际候选前缀，'
             '不是蒙特卡洛估计。未命中仅说明本次预算内未猜中；两种模型都不覆盖的新口令不能通过门槛。</p>',
             f'<p>OMEN 使用开发样本中的字符和 {cfg["attack_models"]["order"]}—19 字符长度范围。'
             '10⁶ 门槛参考 <a href="https://www.usenix.org/system/files/conference/lisa14/lisa14-paper-florencio.pdf">'
             'Florêncio 等，LISA 2014</a> 的在线猜测讨论，不能解释为充分抵抗离线破解。'
             'A1 在独立参考群体上重训两个模型。</p></section>',
             '<section><h2>同一起点、匹配实际通知人数</h2><p>基础规则仅要求至少 8 个字符，'
             '不代表完整 Google 策略。两组对照申请动态组实际逐轮通知人数；未完成相同人数时明确标记，'
             '不按不同成本的终态排名。</p>']
    rows = []
    for key, label in labels[1:]:
        arm = arms[key]
        matched = sizes.get('matched_notification_counts', {}).get(key)
        rows.append([label, arm['final']['ledger']['adaptive_affected'], len(arm['rounds']),
                     '参照' if key == 'google_hold' else '动态预算' if key == 'google_dynamic' else '已匹配' if matched else '未完成相同预算',
                     arm['stop_label']])
    parts.append(table(['方案', '后续通知人数', '实际轮次', '预算匹配', '停止原因'], rows)+'</section>')
    parts.append(ideal_reference_html(result, labels, arms, include_yahoo=False))
    parts.append('<section id="intervention-rounds"><h2>每轮个体检查</h2><p>确定提升包含旧口令被命中、新口令超过已测试预算的账户。'
                 '新旧均未命中时，列为无法比较，不冒充持平。</p>')
    rows = []
    for row in dynamic['rounds']:
        guard, diag = row['aggregate_attack_guard'], row['strength_diagnostics']
        rows.append([row['round'], row['action']['selected'], row['changed'], guard['individual_passed'],
                     diag['improved'], diag['weakened'], diag['equal'], diag['uncomparable'],
                     row.get('strength_rejections', 0), row.get('security_completion_attempts', 0)])
    parts.append(table(['轮次', '通知', '成功修改', '双攻击达标', '确定提升', '确定退步', '持平', '无法比较',
                        '被拒候选次数', '补全构造次数'], rows))
    parts.append('<p>模拟包含攻击检查后的重选及显式构造，不是自然用户响应率的实测结果；'
                 '所有方法使用同一修改机制。构造失败的方案不下发。</p></section>')
    for name, (title, series, opts) in specs.items():
        if name.startswith(('yahoo_', 'cdf_fit_yahoo')):
            continue
        if name == 'cdf_fit_baseline':
            parts.append('<section id="distribution-fit"><h2>CDF 采样拟合</h2>'
                         '<p>以下各图核对实际累计频次与拟合曲线；拟合质量不等于攻击安全性。</p></section>')
        parts.append('<section id="'+escape(name)+'"><h2>'+escape(title)+'</h2>'+plot(series, distinguish=True, **opts)+
                     f'<p><a href="/api/intervention/figure/{result["metadata"]["run_id"]}/{name}.svg">下载 SVG</a></p></section>')
    yahoo = result.get('site_controls', {}).get('yahoo_japan')
    if yahoo:
        parts.append('<section id="yahoo_control"><h2>Yahoo! JAPAN：独立全站迁移</h2>'
                     '<p>从原始账户出发执行可编码的公开规则；修改规模与局部调整不同，不加入局部方法排名。</p>')
        audit = yahoo['target_audit']
        parts.append(table(['要求修改人数', '要求修改比例', '迁移后不合规人数'],
                           [[audit['required_modifications'], pct(audit['required_rate']), audit['noncompliant_remaining']]]))
        hybrid = result.get('site_controls', {}).get('yahoo_dynamic_hybrid')
        if hybrid:
            design, costs = hybrid['hybrid_design'], hybrid['target_audit']
            parts.append('<h3>部分用户用强策略，其余用户动态调整</h3>'
                f'<p>预先设定最多 {pct(design["requested_strong_fraction"])} 的全站账户采用 Yahoo!；'
                '只从未满足 Yahoo! 规则的账户中，按原始完整口令的重复频次从高到低选择，'
                '相同频次用固定种子打散。这是分布驱动的选人办法，不声称已经找到最优比例。</p>'
                f'<p>其余 {design["remaining_accounts"]:,} 个账户先满足至少 8 字符的基础规则，'
                f'随后使用相同 18 条候选规则动态调整。每轮最多 {design["round_cap"]:,} 人，'
                f'累计最多 {design["total_local_cap"]:,} 人，最多 {design["maximum_rounds"]} 轮；'
                '所有分布和攻击比例均按全站账户计算。Yahoo! 分组不重复参与动态调整；'
                '其余账户有资格参加，并非每个人都要修改。</p>'
                f'<p>混合组实际执行 {len(hybrid["rounds"])} 轮动态调整；停止原因：'
                f'{escape(hybrid["stop_label"])}。动态阶段沿用 PCFG 与 OMEN 个体门槛，'
                'Yahoo! 阶段与全站 Yahoo! 对照使用同一合规修改机制；A1 仅在独立参考群体上重训。</p>')
            parts.append(table(['方案', 'Yahoo! 修改人数', '其余账户基础规则修改', '后续动态修改',
                                '累计修改的不同账户', '累计修改比例', '修改事件总数'], [
                ['全站 Yahoo!', audit['required_modifications'], 0, 0,
                 yahoo['final']['ledger']['changed'], pct(yahoo['final']['ledger']['changed_rate']),
                 yahoo['final']['ledger']['modification_events']],
                [hybrid['label'], costs['strong_modified'], costs['baseline_modified'], costs['local_modified'],
                 costs['total_distinct_modified'], pct(costs['total_distinct_modified_rate']), costs['modification_events']]]))
            parts.append('<p>基础规则修改与后续动态修改可能发生在同一账户上；'
                         '累计不同账户按账户去重，不能把三个阶段人数简单相加。</p>')
            rows = []
            for method in (yahoo, hybrid):
                distance = method['final']['ideal_distance']
                evaluations = []
                for level in ('F', 'A1'):
                    ev = method['attacks'][level]
                    from core.intervention_attack_area import curve_area
                    evaluations += [pct(ev['minauto'][-1]['rate']), f'{curve_area(ev):.6f}']
                rows.append([method['label'], f'{distance["empirical"]:.9f}',
                             '—' if distance['fitted'] is None else f'{distance["fitted"]:.9f}', *evaluations])
            parts.append(table(['方案', '实际频次 W1', 'CDF 拟合 W1', 'F 联合终点命中', 'F 联合对数面积',
                                'A1 联合终点命中', 'A1 联合对数面积'], rows))
            summary = result.get('hybrid_comparison')
            if summary:
                gaps = summary['distribution']
                parts.append(f'<p>混合方案保留全站 Yahoo! 相对原始分布改善的比例：实际频次 '
                    f'{pct(gaps["empirical"]["yahoo_improvement_retained"])}，CDF 拟合 '
                    f'{pct(gaps["fitted"]["yahoo_improvement_retained"])}。'
                    '预先设定的探索容差为保留至少 95% 的 W1 改善，且 F、A1 联合曲线对数面积'
                    '分别不高于全站 Yahoo! 超过 0.005。逐项差距如实报告，不能据此宣称统计等效。</p>')
                parts.append(table(['检查项', '混合−全站 Yahoo!', '是否落入预设探索容差'],
                    [[k+' W1', f'{v["hybrid_minus_yahoo"]:+.9f}', '是' if v['within_exploratory_95pct'] else '否']
                     for k, v in [('实际频次', gaps['empirical']), ('CDF 拟合', gaps['fitted'])]] +
                    [[k+' 联合对数面积', f'{v["area_gap"]:+.6f}', '是' if v['within_exploratory_half_pp_area'] else '否']
                     for k, v in summary['attacks'].items()]))
            extension = result['metadata'].get('hybrid_extension', {})
            parts.append(f'<p>本次只新增混合组计算。原有五组与全站 Yahoo! 结果沿用实验 '
                         f'{escape(extension.get("parent_run_id", ""))}，已核对相同抽样、起始账户和固定 F 结果。'
                         '30% 是预先固定的试验值；单次模拟不确定最低可行比例，也不等同于真实网站用户实验。</p>')
        for name, (title, series, opts) in specs.items():
            if name.startswith(('yahoo_', 'cdf_fit_yahoo')):
                parts.append('<div id="'+escape(name)+'"><h3>'+escape(title)+'</h3>'+plot(series, distinguish=True, **opts)+
                    f'<p><a href="/api/intervention/figure/{result["metadata"]["run_id"]}/{name}.svg">下载带图例 SVG</a></p></div>')
        parts.append('</section>')
    parameter_rows = result.get('round_parameter_fits', {}).get('rows', [])
    parts.append('<section id="round_parameters"><h2>逐轮拟合参数</h2>'+
                 round_parameter_html(parameter_rows, requested_rounds=result['google_round_zipf']['requested_rounds'])+'</section>')
    parts.append('<section><h2>攻击范围与可复核信息</h2>')
    rows = []
    for key, label in labels:
        for level in ('F', 'A1'):
            ev = result['baseline']['risk'] if key == 'baseline' else arms[key]['attacks'][level]
            if ev:
                p = next(p for p in ev['minauto'] if p['budget'] == budget)
                rows.append([label, level + ('（与 F 相同）' if key == 'baseline' and level == 'A1' else ''), pct(p['rate']), p['outside_model_support_weight'],
                             p['charged_attempts'], p['unique_guesses']])
    parts.append(table(['方案', '攻击层次', '联合命中比例', '两模型范围外账户', '总尝试次数', '联合不同候选数'], rows))
    parts.append(f'<p>实验编号：{escape(result["metadata"]["run_id"])}；协议：{escape(result["metadata"]["protocol"])}。'
                 '训练仅使用开发记录；原始口令和攻击候选不进入公开报告。</p></section></main>')
    body = ''.join(parts)
    if not document:
        return body
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width,initial-scale=1"><title>ZipfGuard 双攻击个体验证</title><style>'+
            STYLE+DASHBOARD_STYLE+'</style><body>'+body+'</body></html>')
