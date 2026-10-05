"""Third workbench: reuse the second workbench's plot and SVG legend exporter."""
import numpy as np

from core.cdf_sampling import frequency_sample
from web.presentation import STYLE, escape, plot, table
from web.study_dashboard import DASHBOARD_STYLE
from web.cdf_fit_presentation import fit_chart_specs, render_fit_diagnostics
from web.round_parameter_presentation import round_parameter_html, round_parameter_svg
from policy.intervention_fragments import LABELS as FRAGMENT_LABELS

LABELS = [('baseline', '原始分布（无干预）'), ('google_hold', 'Google 基础策略'),
          ('google_random', 'Google＋随机分批调整'),
          ('google_frozen', 'Google＋初始排序后分批执行'),
          ('google_dynamic', 'Google＋每轮重新评估的动态调整')]


def comparison_labels(result):
    completed = result['google_round_zipf']['experimental']['rounds_completed']
    arms = result['google_round_zipf']['arms']
    labels = [(key, label) for key, label in LABELS if key == 'baseline' or key in arms]
    if completed != 10:
        labels = [(key, f'{label}（{completed} 轮后停止）' if key == 'google_dynamic' else label)
                  for key, label in labels]
    return labels


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


def fitted_rank_mass_from_report(result, rank):
    fit = next((row['fit']['sampling_fit'] for row in result['distribution_fits']['results']
                if row['key'] == 'baseline' and row.get('fit')), None)
    if fit is None:
        return None
    parameters = fit['parameters']
    n = result['baseline']['distribution']['users']
    seed = result['config']['seed']
    masses = []
    for offset in (1001, 1002, 1003):
        uniforms = np.maximum(np.random.default_rng(seed + offset).random(n),
                              np.finfo(float).tiny)
        frequencies = frequency_sample(parameters['c'], parameters['s'], uniforms)
        masses.append(float(frequencies[:rank].sum() / n))
    return sum(masses) / len(masses)


def chart_specs(result):
    if not result.get('google_round_zipf', {}).get('google_baseline'):
        return fit_chart_specs(result)
    arms = result.get('google_round_zipf', {}).get('arms')
    if not arms:
        return {}
    base, cfg = result['baseline'], result['config']
    labels = comparison_labels(result)
    budget = cfg['risk_budget']
    google_cost = arms['google_hold']['final']['ledger']['affected_rate']
    def added_cost(state):
        ledger = state['ledger']
        return ledger.get('adaptive_affected_rate', max(0, ledger['affected_rate']-google_cost))
    maximum = max((added_cost(a['final']) for a in arms.values()), default=0)
    initial = main_point(base['risk'], budget)['rate']
    cost_series = [(labels[0][1], [(0, initial)])]
    cost_series += [(label, [(added_cost(s), main_point(s['risk'], budget)['rate'])
                            for s in arms[key]['trajectory']]) for key, label in labels[1:]]
    rank = result['google_round_zipf']['comparison_budget'].get('distribution_top_k')
    distribution_series = []
    baseline_mass = (fitted_rank_mass_from_report(result, rank)
                     if rank and result.get('distribution_fits') else None)
    if baseline_mass is not None:
        google_mass = arms['google_dynamic']['distribution_goal']['start_fitted_top_mass']
        distribution_series = [(labels[0][1], [(0, baseline_mass)])]
        for key, label in labels[1:]:
            arm = arms[key]
            points = [(0, google_mass)]
            points += [(added_cost(arm['trajectory'][i+1]),
                        row['selection_risk_after_same_model'])
                       for i, row in enumerate(arm['rounds'])]
            distribution_series.append((label, points))
    specs = {
        **({'distribution_cost': ('累计受影响账户比例—拟合分布前段占比', distribution_series,
            dict(xlabel='Google 起点后累计通知的不同账户比例',
                 ylabel=f'CDF 拟合曲线前 {rank:,} 位累计账户占比',
                 x_format='percent', y_format='percent',
                 x_domain=(0, min(1, maximum*1.08)), y_domain=(0, 1)))}
           if distribution_series else {}),
        'coverage_F': ('累计受影响账户比例—猜测成功率', cost_series,
                        dict(xlabel='Google 起点后累计通知的不同账户比例',
                            ylabel=f'F 模型估计猜中比例（{budget:,} 次）',
                            x_format='percent', y_format='percent',
                            x_domain=(0, min(1, maximum*1.08)), y_domain=(0, 1))),
    }
    for level, title in [('F', 'F：攻击者不更新模型'), ('A1', 'A1：攻击者学习局部干预后的参考分布')]:
        if level == 'A1' and not any(a['attacks']['A1'] for a in arms.values()):
            continue
        series = [(labels[0][1], [(p['budget'], p['rate']) for p in base['risk']['minauto']])]
        for key, label in labels[1:]:
            ev = arms[key]['attacks'][level]
            if ev:
                series.append((label, [(p['budget'], p['rate']) for p in ev['minauto']]))
        specs['attack_'+level] = (title, series,
            dict(xlabel='累计攻击次数（估计猜测预算）', ylabel='累计猜出的口令比例（全部账户）',
                 log=True, x_format='power10', y_format='percent', y_domain=(0, 1)))
    dist = [(labels[0][1], base['distribution']['full_rank_frequency'])]
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
        return '<main><section><h2>此旧报告尚未补算共同 Google 起点</h2><p>请重新运行实验。</p></section></main>'
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
             '<p>五组使用相同初始账户。后三组从同一个 Google 合规状态出发，'
             '共享 18 条候选规则、用户响应假设和攻击评估。</p><div class="metrics">']
    for label, value, note in [
        ('模拟账户', f'{ledger["accounts"]:,}', '本次各组使用相同初始账户'),
        ('后续受影响', pct(ledger.get('adaptive_affected_rate', ledger['affected_rate'])),
         f'{ledger.get("adaptive_affected", ledger["affected"]):,} 个账户在 Google 起点后被通知'),
        ('实际修改', pct(ledger['changed_rate']), f'{ledger["changed"]:,} 个账户完成修改'),
        ('拟合前段累计占比', pct(dynamic.get('distribution_goal', {}).get('final_fitted_top_mass')),
         f'固定前 {result["google_round_zipf"]["comparison_budget"].get("distribution_top_k", "—")} 位完整口令；越低越分散')]:
        parts.append(f'<div class="metric">{escape(label)}<strong>{escape(value)}</strong><small>{escape(note)}</small></div>')
    parts.append(f'</div><p>以上为 Google 起点后逐步干预方案：已完成共同起点后的 {completed} 轮干预；停止原因：{escape(dynamic["stop_label"])}。</p></section>')
    parts.append('<section id="intervention-conditions"><h2>先看懂这次比较</h2>'
                 '<p>图例支持勾选显示或隐藏曲线，悬停突出对应曲线；'
                 '重叠线使用线型与点形辅助区分。每张图保持相同方案顺序和颜色。</p>'
                 '<p>原始组保留原口令；另外四组先把全体账户迁移到满足 Google 最低 8 字符要求的同一状态。'
                 'Google 基础组此后不再调整；随机组先抽人再选规则；初始排序组只在起点规划一次；'
                 '完整动态组每轮重新分析分布。</p>')
    parts.append(table(['实验组', '要回答的问题'], [
        ['原始分布（无干预）', '原始风险有多高'],
        ['Google 基础策略', '基础策略本身改善多少'],
        ['Google＋随机分批调整', '优先选择影响分布的账户是否有价值'],
        ['Google＋初始排序后分批执行', '每轮重新分析分布是否有价值'],
        ['Google＋每轮重新评估的动态调整', '完整方法的结果'],
    ]))
    replications = result.get('paired_replications')
    if replications and replications.get('protocol') == 'cost-matched-F-v1' and replications.get('runs'):
        def pp(value):
            return f'{value*100:+.3f} 个百分点'

        rows = []
        for run in replications['runs']:
            values = run['arms']
            random, frozen, dynamic_value = (values[key]['F'] for key in
                                             ('google_random', 'google_frozen', 'google_dynamic'))
            rows.append([run['seed'], pct(random), pct(frozen), pct(dynamic_value),
                         pp(dynamic_value-random), pp(dynamic_value-frozen)])
        means = replications['mean']
        rows.append(['平均', pct(means['google_random']['F']), pct(means['google_frozen']['F']),
                     pct(means['google_dynamic']['F']), pp(means['dynamic_minus_random']['F']),
                     pp(means['dynamic_minus_frozen']['F'])])
        parts.append('<section id="paired-replications"><h2>配对随机种子复验</h2>'
                     f'<p>三个 10 万账户实验分别用种子 42、43、44；每行的后三组共享该种子的 Google 起点。'
                     f'下表统一在后续通知 {pct(replications["comparison_cost"])} 账户时比较固定 F 模型'
                     '在主猜测预算下的估计命中比例。轮次之间按相邻观测点线性插值，'
                     '未达到该成本的种子不纳入比较。Δ 为动态组减对照组，负值表示动态组更低。'
                     'A1 仅在各组终态重训，终态成本不同，不能拿来做这张等成本表的差值。</p>')
        parts.append(table(['种子', '随机组 F', '初始排序 F', '动态组 F',
                            '动态−随机 Δ', '动态−初始排序 Δ'], rows))
        parts.append('</section>')
    elif replications and replications.get('runs'):
        def pp(value):
            return f'{value*100:+.3f} 个百分点'

        rows = []
        for run in replications['runs']:
            values = run['arms']
            d, f, random = values['google_dynamic'], values['google_frozen'], values['google_random']
            rows.append([run['seed'], pct(d['affected_rate']),
                         f'{pct(random["F"])} / {pct(random["A1"])}',
                         f'{pct(f["F"])} / {pct(f["A1"])}',
                         f'{pct(d["F"])} / {pct(d["A1"])}',
                         f'{pp(d["F"]-f["F"])} / {pp(d["A1"]-f["A1"])}'])
        means = replications['mean']
        rows.append(['平均', pct(means['google_dynamic']['affected_rate']),
                     f'{pct(means["google_random"]["F"])} / {pct(means["google_random"]["A1"])}',
                     f'{pct(means["google_frozen"]["F"])} / {pct(means["google_frozen"]["A1"])}',
                     f'{pct(means["google_dynamic"]["F"])} / {pct(means["google_dynamic"]["A1"])}',
                     f'{pp(means["dynamic_minus_frozen"]["F"])} / '
                     f'{pp(means["dynamic_minus_frozen"]["A1"])}'])
        parts.append('<section id="paired-replications"><h2>配对随机种子复验</h2>'
                     f'<p>下方单次曲线对应种子 {cfg["seed"]}；表格列出三个种子的配对结果。'
                      '每行使用同一随机种子下的共同 Google 起点和相同逐轮通知上限；各组可能提前停止或未用满预算。'
                     'Δ 为动态组减初始排序组，负值表示动态组估计风险更低；这是模拟点估计，'
                     '种子数量较少，不代表统计显著性。</p>')
        parts.append(table(['种子', '累计受影响账户', '随机组 F / A1',
                            '初始排序 F / A1', '逐轮动态 F / A1', '动态−初始排序 Δ F / A1'], rows))
        parts.append('</section>')
    parts.append('<p>初始排序组只在 Google 起点按分布改善排好后续账户和规则，之后不重新排序；'
                 '动态组每轮执行后，按新的完整口令分布重新选账户和规则。'
                 '随机组的账户顺序不看口令分布，选规则时使用相同的分布评分。'
                  '三组在运行前固定每轮与累计通知预算；未用完的预算如实保留。</p>')
    parts.append(f'<p>动态组比较热门口令、结构和跨结构账户中的局部动作，'
                  '随机组先抽取账户，再从文档第 1—18 条片段中分配修改方法。'
                  '三组均先比较完整口令的排名累计分布，再用 CDF 采样拟合参数复核靠前动作。'
                  '拟合曲线在固定前段排名处的累计占比越低，表示热门完整口令覆盖的账户越少。'
                  '修改成功的账户还必须满足固定 F 模型的估计猜测次数严格增加；无法估计新旧次数时不批准修改。'
                  'F 和独立参考 A1 的攻击曲线保留作事后检验，不与分布指标加权。'
                   '随机账户按与风险无关的种子排序，只通知至少违反一条候选规则且被分配了合格修改方法的账户。</p>')
    parts.append('<p>动态组在相同起点、相同预测模型下，也会评价达到相同单轮人数的随机组动作。'
                 '若随机组动作不足一整轮，两组只能按实际累计通知成本比较。执行后的账户分布会分叉，'
                 '用户响应有波动，攻击终点评估也不同于选动作时的分布目标；'
                 '这些因素都可能使最终曲线不按当轮预测排序。结论以实际等成本曲线为准。</p>')
    parts.append('<p>第 15 条的字典词使用独立开发样本中的纯字母词作为可复现代理，'
                 '第 17 条名单随每轮热门完整口令更新，第 18 条使用独立开发样本中的完整口令。'
                 '三者的名单覆盖范围不同，不能解释为对所有字典词或所有泄露口令的完整拦截。</p>')
    parts.append('<details><summary>查看本次使用的 18 条候选规则</summary><ol>' +
                 ''.join(f'<li>{escape(FRAGMENT_LABELS[n])}</li>' for n in FRAGMENT_LABELS) +
                 '</ol></details>')
    baseline_policy = result['google_round_zipf']['google_baseline']['target']
    parts.append(f'<p>Google 起点有 {baseline_policy["eligible"]:,} 个账户原口令短于 8 字符，'
                 f'均在起始迁移阶段改为符合规则的口令（占 {pct(baseline_policy["affected_rate"])}）。'
                 '为构造严格符合 Google 长度规则的起点，起始迁移按全部账户完成处理；之后三组分批干预使用相同的通知上限、'
                  '用户响应率和累计附加预算。下方成本图从 Google 起点开始计算后续新增通知。</p>')
    parts.append(f'<p>共同起点属于全体合规的模拟设定：有限次修改尝试后仍未合规的 '
                 f'{baseline_policy.get("explicit_length_completions", 0):,} 个账户，通过固定种子的长度补全构造合规口令。'
                 '该构造只用于起点；后续局部干预仍保留未响应和修改失败。</p>')
    parts.append(table(['设置', '本次取值'], [
        ['后续每轮 / 累计新增通知上限', f'{pct(cfg["controller"]["round_fraction"])} / {pct(cfg["controller"]["total_fraction"])}'],
        ['每账户最多干预', 'Google 起点一次、后续局部干预一次；已通知但未修改也计入后续成本'],
        ['未响应概率（情景假设）', pct(cfg['response']['nonresponse'])],
        ['响应者：简单修补 / 片段重组 / 重选', ' / '.join(pct(x) for x in cfg['response']['weights'])],
        ['停止条件', '共同起点后最多 10 轮；无可行动作或预算不足时可提前停止'],
        ['基础要求', '四个 Google 组全站至少 8 字符；局部要求在此基础上叠加'],
        ['分布主目标', f'CDF 拟合排名曲线前 {result["google_round_zipf"]["comparison_budget"].get("distribution_top_k", "—")} 位累计占比下降'],
        ['个体安全门槛', '仅通过固定 F 模型可比较且新估计猜测次数严格更大的局部修改'],
        ['实验编号', result['metadata']['run_id']],
    ]))
    random_arm, frozen_arm = arms['google_random'], arms['google_frozen']
    same_schedule = (len(random_arm['rounds']) == len(frozen_arm['rounds']) == len(dynamic['rounds'])
                     and all(random_arm['rounds'][i]['action']['selected'] ==
                             frozen_arm['rounds'][i]['action']['selected'] ==
                             dynamic['rounds'][i]['action']['selected']
                             for i in range(len(dynamic['rounds']))))
    if same_schedule:
        parts.append(f'<p class="notice">本次后三组每轮通知人数相同，后续各组均通知 '
                      f'{pct(ledger.get("adaptive_affected_rate", ledger["affected_rate"]))} 的账户。'
                      'Google 起点的通知另行统计；20% 是后续通知预算上限。</p>')
    if all(left['state_sha256'] == right['state_sha256'] for left, right in
           zip(frozen_arm['trajectory'], dynamic['trajectory'])) and len(frozen_arm['trajectory']) == len(dynamic['trajectory']):
        parts.append('<p class="notice">本次初始排序组与逐轮动态组的每轮账户状态、F 和 A1 终值完全重合。'
                      '本次未观察到逐轮重新分析相对初始排序的额外收益；与随机组选人的效果须按共同实际成本比较。'
                     '冻结组只在共同 Google 起点规划账户和规则；相同轨迹不是重新规划的结果。</p>')
    random_already = random_arm['final']['ledger'].get('already_compliant', 0)
    if random_already:
        parts.append(f'<p>随机组有 {random_already:,} 名被通知账户已符合选中的规则，计入通知成本但没有修改口令。'
                     '因此相同通知人数不代表相同实际修改人数。</p>')
    parts.append('<p class="notice">PCFG 猜测次数和攻击曲线是模型估计，不是实际破解记录。'
                 '无法估计猜测次数的候选修改不会被当作个人强度提升；'
                 'Google 起点的构造迁移与后续局部强度门槛分开。后三组共用每轮与累计预算上限；'
                 '只在曲线共同覆盖的成本区间内比较，不外推到未达到的 20% 附加覆盖。</p></section>')
    specs = chart_specs(result)
    rank = result['google_round_zipf']['comparison_budget'].get('distribution_top_k')
    for name, (title, series, opts) in specs.items():
        if name.startswith('cdf_fit_'):
            continue  # Fit diagnostics have their own explanation and tables below.
        parts.append(f'<section id="{escape(name)}"><h2>{escape(title)}</h2>')
        if name == 'distribution_cost':
            parts.append(f'<p>这是动作选择的主指标：把 c、s 拟合参数生成的排名累计曲线固定在前 {rank:,} 位，'
                         '纵轴越低，表示热门完整口令覆盖的账户越少。横轴只计共同 Google 起点后的新增通知；'
                         '原始组和 Google 基础组各只有一个点，Google 起点迁移的通知成本另列。'
                         '后三组只在实际到达的成本范围内比较，不外推。'
                         '这是单次模拟拟合；组间差异接近拟合误差时，不能认定某方法稳定占优。</p>')
        if name == 'coverage_F':
            parts.append(f'<p>横轴从共同 Google 起点开始，统计后续被通知的不同账户，纵轴是固定 F 攻击模型在 {budget:,} 次猜测下的估计命中比例。'
                         '原始组和 Google 基础组各只有一个真实成本点；后三组曲线连接实际轮次观测。'
                         '相同横轴位置才能比较方法差异，未到达的成本不作外推。</p>')
        if name == 'attack_A1':
            parts.append('<p>A1 只用独立开发参考群体的模拟迁移结果训练。参考群体构成不同，'
                         '实际干预比例可能与目标群体不同，见下方覆盖与参考账本。</p>')
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
                          '完成后全体账户均符合至少 8 字符规则；后续干预成本图从此时的零新增通知起算。</p>')
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
                     '对每轮完整口令频次分别使用与动作选择相同的 CDF 采样方法拟合。'
                     '实线表示 c，虚线表示 s；圆点和通向该点的线段用颜色标识轮次。'
                     '两项参数使用上下独立纵轴，以免数值量级不同掩盖变化。</p>')
        parts.append(round_parameter_html(rows, requested_rounds=result['google_round_zipf']['requested_rounds']))
        parts.append(f'<p><a href="/api/intervention/figure/{result["metadata"]["run_id"]}/round_parameters.svg">下载带图例 SVG</a></p>')
        parts.append(table(['轮次', '参数 c', '参数 s', '平均最大 CDF 误差', '累计受影响账户'], [
            [row['round'], f'{row["parameters"]["c"]:.7f}', f'{row["parameters"]["s"]:.6f}',
             '—' if row['mean_max_cdf_error'] is None else f'{row["mean_max_cdf_error"]:.3%}',
             pct(row['affected_rate'])] for row in rows]))
        parts.append('<p>c、s 描述拟合后的频次形状，没有单独的“越大越安全”方向或统一合格阈值。'
                     '控制器用二者生成的排名累计曲线比较固定前段的账户占比，不把参数直接相加。'
                     '拟合误差反映模型与实际分布的差距；攻击命中比例和账户修改成本仍单独展示。</p></section>')
    parts.append(render_fit_diagnostics(result))
    parts.append('<section id="intervention-rounds"><h2>每轮具体做了什么</h2>')
    if not dynamic['rounds']:
        parts.append('<p>本次没有执行动作。候选没有通过分布收益、个体强度或预算检查时，系统会停止，'
                     '不会为了展示效果强制修改账户。</p>')
        parts.append('<h3>动态调整每轮口令强度增加的账户比例</h3>'
                     '<p>本次动态组没有完成干预轮次，因此没有可计算的逐轮变化。</p>')
    else:
        parts.append(table(['轮次', '改谁', '怎么改', '通知 / 成功', '后续累计通知',
                             '预计 / 实现拟合前段占比下降'], [
            [r['round'], r['action']['group'], r['action']['rule'], f'{r["action"]["selected"]} / {r["changed"]}',
              pct(dynamic['trajectory'][i+1]['ledger'].get('adaptive_affected_rate',
                  dynamic['trajectory'][i+1]['ledger']['affected_rate'])),
              f'{100*r["prediction"].get("predicted_fitted_cdf_gain", 0):.3f} / '
              f'{100*r.get("realized_distribution_gain", 0):.3f} 个百分点']
            for i, r in enumerate(dynamic['rounds'])]))
        cumulative_strength = 0
        strength_rows = []
        for r in dynamic['rounds']:
            improved = r.get('strength_improved', r['changed'])
            notified = r['action']['selected']
            cumulative_strength += improved
            strength_rows.append([r['round'], notified, improved,
                                  pct(improved/notified if notified else 0),
                                  pct(improved/ledger['accounts']),
                                  pct(cumulative_strength/ledger['accounts']),
                                  r.get('strength_rejections', 0)])
        parts.append('<h3>动态调整每轮口令强度增加的账户比例</h3>'
                     '<p>只统计动态组后续局部干预。强度增加指固定 F 模型对新旧口令均给出'
                     '估计猜测次数，且新口令严格更难猜；未响应或不能证明增加者保持旧口令。'
                     '“被拒尝试”计口令候选次数，不是独立用户数。</p>')
        parts.append(table(['轮次', '本轮通知', '猜测次数增加账户', '占本轮通知',
                            '本轮占全站', '累计占全站', '未通过强度检查的候选尝试'], strength_rows))
        for r in dynamic['rounds']:
            round_label = f'调整第 {r["round"]} 轮'
            parts.append(f'<details><summary>{round_label}选择依据与候选比较</summary>'
                         f'<p>{escape(r["selection_reason"])}。未响应 {r["nonresponse"]} 人，'
                         f'尝试后未完成 {r["failed_to_comply"]} 人。</p>')
            ordered = sorted(r['candidate_audit'], key=lambda x: -x['score'])[:12]
            parts.append(table(['对象', '要求', '人数', '预计分布前段占比下降', '判断'], [
                [a['action']['group'], a['action']['rule'], a['action']['selected'],
                  f'{100*(a.get("predicted_fitted_cdf_gain") if a.get("predicted_fitted_cdf_gain") is not None else a.get("predicted_empirical_cdf_gain", 0)):.3f} 个百分点',
                 ('两步桥接（首轮单独可能无改善）' if a.get('bridge') else '可行') if a['feasible']
                 else '；'.join(a['rejection_reasons'])] for a in ordered]))
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
        rows.append([labels[0][1], level, pct(baseline_point['rate']),
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
        [labels[0][1], pct(0), pct(0), '0.0000', pct(0), '保持原始账户'], *[
        [label, pct(arms[key]['final']['ledger']['affected_rate']), pct(arms[key]['final']['ledger']['changed_rate']),
         f'{arms[key]["final"]["ledger"]["edit_cost"]:.4f}',
         pct(arms[key]['adaptive_reference_ledger']['affected_rate']), arms[key]['stop_label']]
        for key, label in labels[1:]]]))
    parts.append('<p>A0 未运行：局部规则不能用于过滤全站未干预或未响应账户。'
                 '原始无干预组的 A1 与 F 相同；其他组的 A1 使用各自独立参考群体的模拟迁移训练。</p></section></main>')
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
