"""Third workbench: reuse the second workbench's plot and SVG legend exporter."""
from web.presentation import STYLE, escape, plot, table
from web.study_dashboard import DASHBOARD_STYLE
from web.cdf_fit_presentation import fit_chart_specs, render_fit_diagnostics
from web.round_parameter_presentation import round_parameter_html, round_parameter_svg
from policy.intervention_fragments import LABELS as FRAGMENT_LABELS

LABELS = [('baseline', '无政策'), ('google_hold', 'Google 政策不变'),
          ('google_dynamic', 'Google 起点＋10 轮逐步干预')]


def comparison_labels(result):
    completed = result['google_round_zipf']['experimental']['rounds_completed']
    if completed == 10:
        return LABELS
    return [*LABELS[:2], ('google_dynamic', f'Google 起点＋{completed} 轮干预（提前停止）')]


def pct(value):
    return '—' if value is None else f'{value:.2%}'


def main_point(evaluation, budget):
    return next(p for p in evaluation['minauto'] if p['budget'] == budget)


def google_round_zipf_series(result):
    comparison = result.get('google_round_zipf')
    if not comparison:
        return []
    series = [(comparison['control']['label'],
               comparison['control']['distribution']['full_rank_frequency'])]
    for snapshot in comparison['experimental']['snapshots']:
        series.append((f"实验组调整第 {snapshot['round']} 轮",
                       snapshot['distribution']['full_rank_frequency']))
    return series


def chart_specs(result):
    if not result.get('google_round_zipf', {}).get('google_baseline'):
        return fit_chart_specs(result)
    arms = result.get('google_round_zipf', {}).get('arms')
    if not arms:
        return {}
    base, cfg = result['baseline'], result['config']
    labels = comparison_labels(result)
    budget = cfg['risk_budget']
    maximum = max((a['final']['ledger']['affected_rate'] for a in arms.values()), default=0)
    # A horizontal baseline explicitly represents taking no further action, not
    # invented observations from intervening on baseline accounts.
    initial = main_point(base['risk'], budget)['rate']
    cost_series = [('无政策', [(0, initial), (maximum, initial)])]
    cost_series += [(label, [(s['ledger']['affected_rate'], main_point(s['risk'], budget)['rate'])
                            for s in arms[key]['trajectory']]) for key, label in labels[1:]]
    guarded = [('无政策', [(0, base['risk']['guarded_risk']), (maximum, base['risk']['guarded_risk'])])]
    guarded += [(label, [(s['ledger']['affected_rate'], s['risk']['guarded_risk'])
                         for s in arms[key]['trajectory']]) for key, label in labels[1:]]
    specs = {
        'risk_cost': ('同样影响一部分账户，风险降了多少？', cost_series,
                      dict(xlabel='累计受影响账户比例', ylabel=f'PCFG F 累计猜中比例（{budget:,} 次）',
                           x_format='percent', y_format='percent',
                           x_domain=(0, min(1, max(cfg['controller']['total_fraction'], maximum*1.08))), y_domain=(0, 1))),
        'guarded_cost': ('选择动作时使用的保守风险代理', guarded,
                         dict(xlabel='累计受影响账户比例', ylabel='估计命中比例 + 模型未覆盖比例',
                              x_format='percent', y_format='percent',
                              x_domain=(0, min(1, max(cfg['controller']['total_fraction'], maximum*1.08))), y_domain=(0, 1))),
    }
    for level, title in [('F', 'F：攻击者不更新模型'), ('A1', 'A1：攻击者学习局部干预后的参考分布')]:
        if level == 'A1' and not any(a['attacks']['A1'] for a in arms.values()):
            continue
        series = [('无政策', [(p['budget'], p['rate']) for p in base['risk']['minauto']])]
        for key, label in labels[1:]:
            ev = arms[key]['attacks'][level]
            if ev:
                series.append((label, [(p['budget'], p['rate']) for p in ev['minauto']]))
        specs['attack_'+level] = (title, series,
            dict(xlabel='累计攻击次数（估计猜测预算）', ylabel='累计猜出的口令比例（全部账户）',
                 log=True, x_format='power10', y_format='percent', y_domain=(0, 1)))
    mutation = [('无政策', [(p['effective_guesses'], p['rate']) for p in result['baseline_mutations']['points']])]
    mutation += [(label, [(p['effective_guesses'], p['rate']) for p in arms[key]['attacks']['reference_mutations']['points']])
                 for key, label in labels[1:]]
    specs['attack_mutations'] = ('公开常见变换：检查追加后缀造成的表面改善', mutation,
        dict(xlabel='实际不同猜测次数', ylabel='累计猜出的口令比例（全部账户）',
             log=True, x_format='power10', y_format='percent', y_domain=(0, 1)))
    dist = [('无政策', base['distribution']['full_rank_frequency'])]
    dist += [(label, arms[key]['final']['distribution']['full_rank_frequency']) for key, label in labels[1:]]
    specs['final_distribution'] = ('最终口令分布', dist,
        dict(xlabel='口令排名 r', ylabel='使用该口令的人数 f(r)', log=True, log_y=True,
             x_format='count', y_format='count', markers=False))
    round_zipf = google_round_zipf_series(result)
    if round_zipf:
        specs['google_round_zipf'] = ('Google 固定对照与动态调整 10 轮的 Zipf 分布', round_zipf,
            dict(xlabel='口令排名 r', ylabel='使用该口令的人数 f(r)', log=True, log_y=True,
                 x_format='count', y_format='count', markers=False))
    # Match the second workbench: zero-based percentage axes with 12% headroom.
    for name, (_, series, opts) in specs.items():
        if name not in ('final_distribution', 'google_round_zipf'):
            ceiling = max((row[1] for _, values in series for row in values if row[1] is not None), default=0)
            opts['y_domain'] = (0, min(1, max(.01, ceiling*1.12)))
    specs.update(fit_chart_specs(result))
    return specs


def render_intervention_html(result, *, document=True):
    if not result.get('google_round_zipf', {}).get('google_baseline'):
        body = ('<main><section id="intervention-overview"><h2>历史实验需要重算 Google 起点</h2>'
                '<p>这份报告只对首批账户执行 Google 要求，不能作为全站 Google 起点的对照。'
                '旧政策比较暂不展示；保留同一批账户的原始分布，使用当前 CDF 采样方法拟合。'
                '原始实验数据完整保留。</p></section>'+render_fit_diagnostics(result)+'</main>')
        return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>'+STYLE+
                DASHBOARD_STYLE+'</style><body>'+body+'</body></html>') if document else body
    arms = result.get('google_round_zipf', {}).get('arms')
    if not arms:
        return '<main><section><h2>此旧报告尚未补算三方案对照</h2><p>请重新运行实验，生成共同 Google 起点后的风险、攻击与成本结果。</p></section></main>'
    cfg = result['config']
    labels = comparison_labels(result)
    dynamic = arms['google_dynamic']
    completed = result['google_round_zipf']['experimental']['rounds_completed']
    budget = cfg['risk_budget']
    final, ledger = dynamic['final'], dynamic['final']['ledger']
    before = main_point(result['baseline']['risk'], budget)
    after = main_point(final['risk'], budget)
    parts = ['<main class="intervention-report"><section class="study-overview" id="intervention-overview">',
             '<span class="eyebrow">第三展示台 · 存量账户局部干预</span>',
             '<h2>改谁、怎么改、改多少</h2>',
             '<p>账户总数固定。每轮从尚未干预的账户中选择一批，提出局部要求；'
             '未响应者保留旧口令与风险。修改后重新分析分布。</p><div class="metrics">']
    for label, value, note in [
        ('模拟账户', f'{ledger["accounts"]:,}', '本次各组使用相同初始账户'),
        ('累计受影响', pct(ledger['affected_rate']), f'{ledger["affected"]:,} 个账户被通知'),
        ('实际修改', pct(ledger['changed_rate']), f'{ledger["changed"]:,} 个账户完成修改'),
        ('F 累计猜中比例', pct(after['rate']), f'初始 {pct(before["rate"])} · {budget:,} 次估计猜测')]:
        parts.append(f'<div class="metric">{escape(label)}<strong>{escape(value)}</strong><small>{escape(note)}</small></div>')
    parts.append(f'</div><p>以上为 Google 起点后逐步干预方案：已完成共同起点后的 {completed} 轮干预；停止原因：{escape(dynamic["stop_label"])}。</p></section>')
    parts.append('<section id="intervention-conditions"><h2>先看懂这次比较</h2>'
                 '<p>图例支持勾选显示或隐藏曲线，悬停突出对应曲线；'
                 '重叠线使用线型与点形辅助区分。每张图保持相同方案顺序和颜色。</p>'
                 '<p>六张对照图统一比较：无政策、Google 政策不变、Google 起点后逐步干预。'
                 '无政策保留原始账户；后两组先把全体账户迁移到满足 Google 最低 8 字符要求的状态，'
                  '固定组此后保持不变，实验组再根据新分布最多干预 10 轮。'
                  '攻击曲线和最终口令分布使用各组终态，风险—成本图展示实际执行过程。</p>')
    parts.append(f'<p>每轮先从热门口令和结构群体中预筛最多 {cfg["controller"]["max_groups"]} 个高风险组，'
                 '再从文档第 1—18 条片段中选择预计全站保守风险下降最大的可行动作；'
                 '各次响应模拟均须显示正收益。预计口令集中度用于约束和同收益时排序，'
                 '逐轮 CDF 拟合参数用于事后核验分布变化。</p>')
    parts.append('<p>第 15 条的字典词使用独立开发样本中的纯字母词作为可复现代理，'
                 '第 17 条名单随每轮热门完整口令更新，第 18 条使用独立开发样本中的完整口令。'
                 '三者的名单覆盖范围不同，不能解释为对所有字典词或所有泄露口令的完整拦截。</p>')
    parts.append('<details><summary>查看本次使用的 18 条候选规则</summary><ol>' +
                 ''.join(f'<li>{escape(FRAGMENT_LABELS[n])}</li>' for n in FRAGMENT_LABELS) +
                 '</ol></details>')
    baseline_policy = result['google_round_zipf']['google_baseline']['target']
    parts.append(f'<p>Google 起点有 {baseline_policy["eligible"]:,} 个账户原口令短于 8 字符，'
                 f'均在起始迁移阶段改为符合规则的口令（占 {pct(baseline_policy["affected_rate"])}）。'
                 '为构造严格符合 Google 规则的起点，起始迁移按全部账户完成处理；之后 10 轮使用配置中的通知比例、'
                 '用户响应率和累计附加预算。起始迁移成本也包含在风险—成本图中。</p>')
    parts.append(f'<p>共同起点属于全体合规的模拟设定：有限次修改尝试后仍未合规的 '
                 f'{baseline_policy.get("explicit_length_completions", 0):,} 个账户，通过固定种子的长度补全构造合规口令。'
                 '该构造只用于起点；后续局部干预仍保留未响应和修改失败。</p>')
    parts.append(table(['设置', '本次取值'], [
        ['后续每轮 / 累计新增通知上限', f'{pct(cfg["controller"]["round_fraction"])} / {pct(cfg["controller"]["total_fraction"])}'],
        ['每账户最多干预', '1 次；已通知但未修改也计入覆盖'],
        ['未响应概率（情景假设）', pct(cfg['response']['nonresponse'])],
        ['响应者：简单修补 / 片段重组 / 重选', ' / '.join(pct(x) for x in cfg['response']['weights'])],
        ['停止条件', '共同起点后最多 10 轮；预算不足、无有效动作或连续停滞可提前停止'],
        ['基础要求', 'Google 两组全站至少 8 字符；动态局部要求在此基础上叠加'],
        ['实验编号', result['metadata']['run_id']],
    ]))
    parts.append('<p class="notice">PCFG 曲线是蒙特卡洛估计，不是实际破解记录。模型未覆盖不等于安全。'
                 '控制器把未覆盖质量计入保守风险代理，防止只因超出模型支持范围就获得收益；'
                 '该代理不是现实风险或置信上界。不同方案可能提前停止，应按实际覆盖率比较。</p></section>')
    specs = chart_specs(result)
    for name, (title, series, opts) in specs.items():
        if name.startswith('cdf_fit_'):
            continue  # Fit diagnostics have their own explanation and tables below.
        parts.append(f'<section id="{escape(name)}"><h2>{escape(title)}</h2>')
        if name in ('risk_cost', 'guarded_cost'):
            parts.append('<p>横轴是累计被要求修改的不同账户比例，纵轴越低表示该模型的估计命中比例越低。'
                         '无政策是水平风险参照线，其实际成本为零。Google 固定组停在全体账户满足长度要求后的实际成本，'
                         '实验组从该点继续；固定组没有后续同成本观测，不把它延伸到实验组终态成本。</p>'
                         if name == 'risk_cost' else '<p>展示三方案实际执行过程中的保守风险代理。Google 固定组在全体账户达到长度要求后保持分布不变，曲线停在其实际迁移成本。</p>')
        if name == 'attack_A1':
            parts.append('<p>A1 只用独立开发参考群体的模拟迁移结果训练。参考群体构成不同，'
                         '实际干预比例可能与目标群体不同，见下方覆盖与参考账本。</p>')
        if name == 'attack_mutations':
            parts.append(f'<p>从独立开发口令生成常见数字、年份和符号变换，共 '
                         f'{result["baseline_mutations"]["unique_guesses"]:,} 个不同猜测。'
                         '横轴只显示实际猜测次数；有限词表耗尽后没有继续猜测。此项不是全部攻击的上界。</p>')
        if name == 'final_distribution':
            parts.append('<p>横轴为频次排名，纵轴为使用人数，两轴均为对数刻度；'
                         '曲线更分散本身不能证明更安全。</p>')
        if name == 'google_round_zipf':
            comparison = result['google_round_zipf']
            completed = comparison['experimental']['rounds_completed']
            parts.append('<p>蓝线是双方完成全体账户的 Google 最低 8 字符迁移后的共同起点；'
                         '固定对照从这个时点起不再调整，实验组随后根据最新分布连续决策 10 轮。'
                         '每条实验曲线对应一次调整后的全站分布，两轴均为对数刻度。</p>')
            parts.append(f'<p>初始 Google 迁移修改了 {pct(comparison["control"]["affected_rate"])} 的账户；'
                         '完成后全体账户均符合至少 8 字符规则。该迁移覆盖率计入两条 Google 方案的横轴成本。</p>')
            if completed != comparison['requested_rounds']:
                parts.append(f'<p class="notice">本次实验请求 {comparison["requested_rounds"]} 轮，实际完成 {completed} 轮；'
                             f'停止原因：{escape(comparison["experimental"]["stop_label"])}。图中没有复制末态补足轮数。</p>')
            elif comparison['common_google_start_verified']:
                parts.append('<p>已核对双方起点的账户状态完全一致。图中共 11 条线：'
                             '共同起点的固定对照 1 条，加上实验组后续第 1–10 轮的 10 条分布。</p>')
        parts.append(plot(series, distinguish=True, **opts))
        parts.append(f'<p><a href="/api/intervention/figure/{result["metadata"]["run_id"]}/{name}.svg">下载带图例 SVG</a></p>')
        if name == 'google_round_zipf':
            parts.append(table(['实验组轮次', '本轮规则', '本轮对象', '累计受影响账户', '低于 8 字符的账户'], [
                [s['round'], s['action']['rule'], s['action']['group'], pct(s['affected_rate']),
                 s.get('google_compliance', {}).get('short_password_accounts', '未记录')]
                for s in result['google_round_zipf']['experimental']['snapshots']]))
        parts.append('</section>')
    parameter_fits = result.get('round_parameter_fits')
    if parameter_fits and parameter_fits.get('rows'):
        rows = parameter_fits['rows']
        parts.append('<section id="round_parameters"><h2>动态策略每轮的分布拟合参数</h2>'
                     '<p>第 0 轮是双方共同的 Google 起点，后续各点只取实验组实际完成的轮次。'
                     '对每轮完整口令频次分别使用相同种子的 CDF 采样方法拟合。'
                     '实线表示 c，虚线表示 s；圆点和通向该点的线段用颜色标识轮次。'
                     '两项参数使用上下独立纵轴，以免数值量级不同掩盖变化。</p>')
        parts.append(round_parameter_html(rows, requested_rounds=result['google_round_zipf']['requested_rounds']))
        parts.append(f'<p><a href="/api/intervention/figure/{result["metadata"]["run_id"]}/round_parameters.svg">下载带图例 SVG</a></p>')
        parts.append(table(['轮次', '参数 c', '参数 s', '平均最大 CDF 误差', '累计受影响账户'], [
            [row['round'], f'{row["parameters"]["c"]:.7f}', f'{row["parameters"]["s"]:.6f}',
             '—' if row['mean_max_cdf_error'] is None else f'{row["mean_max_cdf_error"]:.3%}',
             pct(row['affected_rate'])] for row in rows]))
        parts.append('<p>c、s 描述拟合后的频次形状，没有单独的“越大越安全”方向或统一合格阈值。'
                     '拟合误差反映模型与实际分布的差距；分布参数变化只能作为辅助评价，'
                     '安全效果仍需结合攻击命中比例和账户修改成本判断。纵轴按各自参数范围缩放，比较变化量请看表中原值。</p></section>')
    parts.append(render_fit_diagnostics(result))
    parts.append('<section id="intervention-rounds"><h2>每轮具体做了什么</h2>')
    if not dynamic['rounds']:
        parts.append('<p>本次没有执行动作。候选没有通过风险收益或预算检查时，系统会停止，'
                     '不会为了展示效果强制修改账户。</p>')
    else:
        parts.append(table(['轮次', '改谁', '怎么改', '通知 / 成功', '累计受影响',
                            '预计 / 实现风险代理下降'], [
            [r['round'], r['action']['group'], r['action']['rule'], f'{r["action"]["selected"]} / {r["changed"]}',
             pct(dynamic['trajectory'][i+1]['ledger']['affected_rate']),
             f'{100*r["prediction"]["predicted_guarded_gain"]:.3f} / {100*r["realized_guarded_gain"]:.3f} 个百分点']
            for i, r in enumerate(dynamic['rounds'])]))
        for r in dynamic['rounds']:
            round_label = f'调整第 {r["round"]} 轮'
            parts.append(f'<details><summary>{round_label}选择依据与候选比较</summary>'
                         f'<p>{escape(r["selection_reason"])}。未响应 {r["nonresponse"]} 人，'
                         f'尝试后未完成 {r["failed_to_comply"]} 人。</p>')
            ordered = sorted(r['candidate_audit'], key=lambda x: -x['score'])[:12]
            parts.append(table(['对象', '要求', '人数', '预计风险代理下降', '判断'], [
                [a['action']['group'], a['action']['rule'], a['action']['selected'],
                 f'{100*a["predicted_guarded_gain"]:.3f} 个百分点',
                 '可行' if a['feasible'] else '；'.join(a['rejection_reasons'])] for a in ordered]))
            parts.append(f'<p>本轮共评价 {r["candidate_count"]} 个候选，表中显示评分最高的 12 个；完整记录见公开报告。</p></details>')
    if dynamic.get('terminal_candidate_audit'):
        parts.append('<details><summary>停止前最后一轮候选为什么未执行</summary>')
        parts.append(table(['对象', '要求', '人数', '未执行原因'], [
            [a['action']['group'], a['action']['rule'], a['action']['selected'], '；'.join(a['rejection_reasons'])]
            for a in sorted(dynamic['terminal_candidate_audit'], key=lambda x: -x['score'])[:12]]))
        parts.append('</details>')
    parts.append('</section><section id="intervention-audit"><h2>风险覆盖、预算与停止状态</h2>')
    rows = []
    baseline_point = main_point(result['baseline']['risk'], budget)
    for level in ('F', 'A1'):
        rows.append(['无政策', level, pct(baseline_point['rate']),
                     pct(baseline_point['outside_model_support_weight']/baseline_point['target_weight']),
                     pct(baseline_point['low_sample_support_weight']/baseline_point['target_weight'])])
    for key, label in labels[1:]:
        a = arms[key]
        for level in ('F', 'A1'):
            ev = a['attacks'][level]
            if ev is None:
                rows.append([label, level, '未运行', '—', '—'])
            else:
                p = main_point(ev, budget)
                rows.append([label, level, pct(p['rate']), pct(p['outside_model_support_weight']/p['target_weight']),
                             pct(p['low_sample_support_weight']/p['target_weight'])])
    parts.append(table(['方案', '攻击层次', '估计猜中比例', '模型未覆盖比例', '低采样支持比例'], rows))
    parts.append(table(['方案', '累计通知', '实际修改', '编辑成本 / 全部账户', 'A1 参考通知比例', '停止原因'], [
        ['无政策', pct(0), pct(0), '0.0000', pct(0), '保持原始账户'], *[
        [label, pct(arms[key]['final']['ledger']['affected_rate']), pct(arms[key]['final']['ledger']['changed_rate']),
         f'{arms[key]["final"]["ledger"]["edit_cost"]:.4f}',
         pct(arms[key]['adaptive_reference_ledger']['affected_rate']), arms[key]['stop_label']]
        for key, label in labels[1:]]]))
    parts.append('<p>A0 未运行：局部规则不能用于过滤全站未干预或未响应账户。'
                 '本页展示 F、A1 和独立参考变换攻击，各自含义单独标注。</p></section></main>')
    body = ''.join(parts)
    if not document:
        return body
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width,initial-scale=1"><title>ZipfGuard · 局部干预报告</title><style>'
            + STYLE + DASHBOARD_STYLE + '</style><body>'+body+'</body></html>')


def export_intervention_figures(result, directory):
    # Exact same exporter used by the second workbench: embed legend in SVG.
    from tools.run_dynamic_study import svg_only
    for name, (_, series, opts) in chart_specs(result).items():
        (directory/(name+'.svg')).write_text(svg_only(series, **opts), encoding='utf-8')
    parameter_fits = result.get('round_parameter_fits')
    if parameter_fits and parameter_fits.get('rows'):
        (directory/'round_parameters.svg').write_text(
            round_parameter_svg(parameter_fits['rows'],
                                requested_rounds=result['google_round_zipf']['requested_rounds'])+'\n',
            encoding='utf-8')
