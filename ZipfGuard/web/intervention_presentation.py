"""Third workbench: reuse the second workbench's plot and SVG legend exporter."""
import numpy as np

from core.cdf_sampling import frequency_sample
from core.intervention_distribution import fitted_log_cdf_area_from_parameters
from core.intervention_attack_area import curve_area
from core.ideal_distribution import (METRIC as DISTANCE_METRIC, IDEAL_LABEL,
                                     ideal_frequency_curve, distance_from_area)
from web.presentation import STYLE, escape, plot, table
from web.study_dashboard import DASHBOARD_STYLE
from web.cdf_fit_presentation import fit_chart_specs, render_fit_diagnostics
from web.round_parameter_presentation import round_parameter_html, round_parameter_svg
from policy.intervention_fragments import LABELS as FRAGMENT_LABELS

LABELS = [('baseline', '原始分布（无干预）'), ('google_hold', '8 字符基础规则'),
          ('google_random', '8 字符基础规则＋随机分批调整'),
          ('google_frozen', '8 字符基础规则＋初始排序后分批执行'),
          ('google_dynamic', '8 字符基础规则＋每轮重新评估的动态调整')]


def comparison_labels(result, *, include_site_controls=False):
    completed = result['google_round_zipf']['experimental']['rounds_completed']
    arms = result['google_round_zipf']['arms']
    labels = [(key, label) for key, label in LABELS if key == 'baseline' or key in arms]
    if completed != 10:
        labels = [(key, f'{label}（{completed} 轮后停止）' if key == 'google_dynamic' else label)
                  for key, label in labels]
    if include_site_controls:
        labels += [(key, arm['label']) for key, arm in result.get('site_controls', {}).items()]
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
    if result.get('ideal_distribution'):
        series.append((IDEAL_LABEL, ideal_frequency_curve(result['ideal_distribution']['accounts'])))
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


def fitted_log_area_from_report(result):
    fit = next((row['fit']['sampling_fit'] for row in result['distribution_fits']['results']
                if row['key'] == 'baseline' and row.get('fit')), None)
    if fit is None:
        return None
    return fitted_log_cdf_area_from_parameters(
        fit['parameters'], result['baseline']['distribution']['users'],
        result['config']['seed'])['score']


def ideal_reference_html(result, labels, arms, *, include_yahoo=True):
    ideal = result['ideal_distribution']
    n = ideal['accounts']
    states = [('原始分布（无干预）', result['baseline'])]
    states += [(label, arms[key]['final']) for key, label in labels[1:]]
    baseline = result['baseline']['ideal_distance']['empirical']
    rows = []
    for label, state in states:
        distance = state['ideal_distance']
        fitted = distance['fitted']
        reduction = 1-distance['empirical']/baseline if baseline > 0 else None
        rows.append([label, f'{distance["empirical"]:.9f}',
                     '—' if fitted is None else f'{fitted:.9f}', pct(reduction)])
    rows.append(['理想分布（N 个不同口令，各 1 人）', '0.000000000', '不拟合', '参照'])
    html = ('<section id="ideal_distribution_reference"><h2>如何评价距理想分布有多远</h2>'
            f'<p>本次固定 N={n:,} 个账户，理想参照为 N 个不同的完整口令，每个仅 1 人。'
            '统计相同完整口令的频次，不合并结构相似的口令；所有状态都使用排名 1—N 的同一支持范围。'
            '这是一种频次分散的理想，不指定口令内容，也不代表不可被猜中。</p>'
            '<p>把排名转换为 x=ln(r)/ln(N)，用累计账户占比 C(x) 定义 '
            'D=∫₀¹|C(x)−C*(x)|dx。它是一维 Wasserstein-1 距离，衡量账户质量在对数排名轴上的搬运距离。'
            '排名靠前的集中有更大影响；距离为零表示观测口令全部只出现一次。'
            'Panaretos 与 Zemel（2019，§1.2）给出一维 W1 等于累计分布绝对差面积的公式；'
            '固定 N 的参照和对数排名坐标是本项目明确选择的评价设定。</p>'
            '<p>下表“实际频次距离”由全部口令频次精确计算；“CDF 拟合距离”沿用现有 c、s 采样拟合，'
            '用于新实验的候选比较。二者分别报告，避免把拟合误差当作真实变化。'
            '没有论文支持的统一安全合格阈值，因此报告绝对距离和相对原始差距缩小比例；'
            '同成本下距离更低表明频次更接近理想，安全性另由 F、A1 检验。</p>')
    html += table(['方案', '实际频次 W1 距离', 'CDF 拟合 W1 距离', '相对原始差距缩小'], rows)
    html += ('<p>论文依据：<a href="https://doi.org/10.1146/annurev-statistics-030718-104938">'
             'Panaretos &amp; Zemel, Statistical Aspects of Wasserstein Distances (2019)</a>；'
             '<a href="https://www.ieee-security.org/TC/SP2012/papers/4681a538.pdf">'
             'Bonneau, The Science of Guessing, IEEE S&amp;P (2012)</a> '
             '提供口令猜测与均匀参照的背景，不是本项目对数排名距离公式的来源。</p></section>')
    yahoo = result.get('site_controls', {}).get('yahoo_japan')
    if yahoo and include_yahoo:
        audit = yahoo['target_audit']
        html += ('<section id="yahoo_control"><h2>Yahoo! JAPAN 全站强策略对照</h2>'
                 '<p>从与原始组完全相同的初始账户出发，一次应用 15—32 字符、公开半角字符集'
                 '和已公开禁止组合的规则，不额外要求字母数字混合。'
                 '公开说明还存在未枚举的禁止组合，因此这里只模拟可编码的公开规则。</p>'
                 f'<p>{escape(yahoo["premise"])}。仍使用相同三类修改提案及有限尝试次数，'
                 '失败后显式补全到合规状态；这是完成迁移的假设，不能当作实测用户行为。'
                 'A1 在独立参考账户按同一策略迁移后的分布上训练。</p>')
        html += table(['要求修改人数', '要求修改比例', '有限尝试后显式补全人数', '迁移后不合规人数'], [
            [f'{audit["required_modifications"]:,}', pct(audit['required_rate']),
             f'{audit["explicit_completions"]:,}', audit['noncompliant_remaining']]])
        html += ('<p><a href="https://support.yahoo-net.jp/SccLogin/s/article/H000004639">'
                 'Yahoo! JAPAN 官方规则</a>。此组的攻击结果单独展示，'
                 '不加入共同起点后的局部干预排名。</p></section>')
    return html


def chart_specs(result):
    if not result.get('google_round_zipf', {}).get('google_baseline'):
        return fit_chart_specs(result)
    arms = result.get('google_round_zipf', {}).get('arms')
    if not arms:
        return {}
    base, cfg = result['baseline'], result['config']
    labels = comparison_labels(result, include_site_controls=False)
    endpoint_labels = comparison_labels(result)
    endpoint_arms = {**arms, **result.get('site_controls', {})}
    distance_mode = bool(result.get('ideal_distribution'))
    budget = cfg['risk_budget']
    dual = bool(base['risk'].get('attack_curves'))
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
    area_mode = arms['google_dynamic'].get('distribution_goal', {}).get('metric') in (
        'fitted_complete_log_rank_cdf_area', DISTANCE_METRIC)
    baseline_mass = ((fitted_log_area_from_report(result) if area_mode else
                      fitted_rank_mass_from_report(result, rank))
                     if rank and result.get('distribution_fits') else None)
    if baseline_mass is not None:
        goal = arms['google_dynamic']['distribution_goal']
        google_mass = (goal.get('start_fitted_distance', goal.get('start_fitted_log_area'))
                       if area_mode else goal['start_fitted_top_mass'])
        if distance_mode:
            baseline_mass = base['ideal_distance']['fitted']
            google_mass = arms['google_hold']['final']['ideal_distance']['fitted']
        distribution_series = [(labels[0][1], [(0, baseline_mass)])]
        for key, label in labels[1:]:
            arm = arms[key]
            points = [(0, google_mass)]
            points += [(added_cost(arm['trajectory'][i+1]),
                        (arm['trajectory'][i+1]['ideal_distance']['fitted'] if distance_mode else
                         row['selection_risk_after_same_model']))
                       for i, row in enumerate(arm['rounds'])]
            distribution_series.append((label, points))
    specs = {
        **({'distribution_cost': ('累计受影响账户比例—距理想分布的 W1 距离' if distance_mode else
                                  '累计受影响账户比例—完整分布集中度' if area_mode else
                                  '累计受影响账户比例—拟合分布前段占比', distribution_series,
            dict(xlabel='8 字符基础规则 起点后累计通知的不同账户比例',
                 ylabel='对数排名 Wasserstein-1 距离（越低越接近理想）' if distance_mode else
                          'CDF 拟合累计曲线的对数排名面积（越低越分散）' if area_mode else
                          f'CDF 拟合曲线前 {rank:,} 位累计账户占比',
                 x_format='percent', y_format='decimal6' if area_mode else 'percent',
                 x_domain=(0, min(1, maximum*1.08)), y_domain=(0, 1)))}
           if distribution_series else {}),
        'coverage_F': ('累计受影响账户比例—猜测成功率', cost_series,
                        dict(xlabel='8 字符基础规则 起点后累计通知的不同账户比例',
                            ylabel=(f'固定 F 联合命中比例（每模型 {budget:,} 次）' if dual else
                                    f'F 模型估计猜中比例（{budget:,} 次）'),
                            x_format='percent', y_format='percent',
                            x_domain=(0, min(1, maximum*1.08)), y_domain=(0, 1))),
    }
    if result.get('metadata', {}).get('aggregate_attack_guard'):
        area_series = [(labels[0][1], [(0, curve_area(base['risk']))])]
        area_series += [(label, [(added_cost(s), curve_area(s['risk']))
                                 for s in arms[key]['trajectory']]) for key, label in labels[1:]]
        specs['attack_area_cost'] = ('累计通知比例—总体猜测曲线面积', area_series,
            dict(xlabel='8 字符基础规则 起点后累计通知的不同账户比例',
                 ylabel=('固定 F 联合曲线对数面积（仅作诊断）' if dual else
                         '固定 F 猜测成功曲线对数面积（越低估计命中越少）'),
                 x_format='percent', y_format='decimal6',
                 x_domain=(0, min(1, maximum*1.08))))
    if area_mode and arms['google_dynamic']['rounds']:
        dynamic = arms['google_dynamic']
        start_round = max(0, len(dynamic['rounds'])-3)
        scores = [(0, (dynamic['trajectory'][0]['ideal_distance']['fitted'] if distance_mode else
                       dynamic['distribution_goal'].get('start_fitted_distance',
                           dynamic['distribution_goal'].get('start_fitted_log_area'))))]
        scores += [(i, (dynamic['trajectory'][i]['ideal_distance']['fitted'] if distance_mode else
                        row['selection_risk_after_same_model']))
                   for i, row in enumerate(dynamic['rounds'], 1)]
        detail = scores[start_round:]
        values = [value for _, value in detail]
        margin = max((max(values)-min(values))*.1, .00002)
        specs['dynamic_distribution_detail'] = (
            '动态组末段分布变化（局部放大）',
            [(labels[-1][1], detail)],
            dict(xlabel='动态调整轮次', ylabel='距理想分布的 W1 距离' if distance_mode else
                 '拟合完整累计分布面积（越低越分散）',
                 x_ticks=[round_id for round_id, _ in detail],
                 x_format='count', y_format='decimal6',
                 x_domain=(detail[0][0], detail[-1][0]),
                 y_domain=(max(0, min(values)-margin), max(values)+margin)))
    for level, title in [('F', 'F：攻击者不更新模型'), ('A1', 'A1：攻击者学习局部干预后的参考分布')]:
        if level == 'A1' and not any(a['attacks']['A1'] for a in endpoint_arms.values()):
            continue
        series = ([(labels[0][1], [(p['budget'], p['rate']) for p in base['risk']['minauto']])]
                  if level == 'F' else [])
        for key, label in endpoint_labels[1:]:
            ev = endpoint_arms[key]['attacks'][level]
            if ev:
                series.append((label, [(p['budget'], p['rate']) for p in ev['minauto']]))
        specs['attack_'+level] = (title, series,
            dict(xlabel='累计攻击次数（估计猜测预算）', ylabel='累计猜出的口令比例（全部账户）',
                 log=True, x_format='power10', y_format='percent', y_domain=(0, 1)))
        if base['risk'].get('attack_curves'):
            for attacker, label in [('pcfg', 'PCFG'), ('markov', '马尔可夫 OMEN'), ('union', 'PCFG ∪ OMEN 联合攻击')]:
                attack_series = [(labels[0][1], [(p['budget'], p['rate'])
                                  for p in base['risk']['attack_curves'][attacker]['minauto']])]
                for key, method_label in endpoint_labels[1:]:
                    ev = endpoint_arms[key]['attacks'][level]
                    if ev:
                        attack_series.append((method_label, [(p['budget'], p['rate'])
                                             for p in ev['attack_curves'][attacker]['minauto']]))
                name = 'attack_'+level if attacker == 'pcfg' else 'attack_'+attacker+'_'+level
                specs[name] = (level+'：'+label, attack_series,
                    dict(xlabel='每模型实际尝试预算 B（联合总尝试 ≤ 2B）' if attacker == 'union' else '该模型实际尝试预算 B',
                         ylabel='命中账户比例（全部账户）', log=True, x_format='power10',
                         y_format='percent', y_domain=(0, 1)))
    yahoo = result.get('site_controls', {}).get('yahoo_japan')
    if yahoo:
        for level in ('F', 'A1'):
            ev = yahoo['attacks'].get(level)
            if ev:
                curves = ev.get('attack_curves', {'pcfg': ev})
                yseries = [(label, [(p['budget'], p['rate']) for p in curves[key]['minauto']])
                           for key, label in [('pcfg', 'PCFG'), ('markov', 'OMEN'), ('union', 'PCFG ∪ OMEN')] if key in curves]
                specs['yahoo_attack_'+level] = ('Yahoo! JAPAN 独立全站对照 · '+level, yseries,
                    dict(xlabel='每模型尝试预算 B；联合最多 2B' if base['risk'].get('attack_curves') else '估计猜测预算',
                         ylabel='命中账户比例', log=True, x_format='power10', y_format='percent', y_domain=(0, 1)))
    dist = [(labels[0][1], base['distribution']['full_rank_frequency'])]
    dist += [(label, endpoint_arms[key]['final']['distribution']['full_rank_frequency'])
             for key, label in endpoint_labels[1:]]
    if distance_mode:
        dist.append((IDEAL_LABEL, ideal_frequency_curve(base['distribution']['users'])))
    specs['final_distribution'] = ('最终口令分布', dist,
        dict(xlabel='口令排名 r', ylabel='使用该口令的人数 f(r)', log=True, log_y=True,
             x_format='count', y_format='count', markers=False,
             reference_series=[IDEAL_LABEL] if distance_mode else []))
    round_zipf = google_round_zipf_series(result)
    if round_zipf:
        rounds = len(arms['google_dynamic']['rounds'])
        specs['google_round_zipf'] = (f'8 字符基础规则固定对照与动态调整 {rounds} 轮的 Zipf 分布', round_zipf,
            dict(xlabel='口令排名 r', ylabel='使用该口令的人数 f(r)', log=True, log_y=True,
                 x_format='count', y_format='count', markers=False,
                 reference_series=[IDEAL_LABEL] if distance_mode else []))
    # Match the second workbench: zero-based percentage axes with 12% headroom.
    for name, (_, series, opts) in specs.items():
        if name not in ('final_distribution', 'google_round_zipf',
                        'dynamic_distribution_detail'):
            ceiling = max((row[1] for _, values in series for row in values if row[1] is not None), default=0)
            opts['y_domain'] = (0, min(1, max(.01, ceiling*1.12)))
        if name == 'distribution_cost' and area_mode:
            values = [row[1] for _, points in series for row in points if row[1] is not None]
            floor, ceiling = min(values), max(values)
            margin = max((ceiling-floor)*.08, .00005)
            opts['y_domain'] = (max(0, floor-margin), ceiling+margin)
    specs.update(fit_chart_specs(result))
    return specs


def render_intervention_html(result, *, document=True):
    if result.get('baseline', {}).get('risk', {}).get('attack_curves'):
        from web.dual_intervention_presentation import render_dual_report
        return render_dual_report(result, document=document)
    if not result.get('google_round_zipf', {}).get('google_baseline'):
        body = ('<main><section id="intervention-overview"><h2>历史实验需要重算 8 字符基础规则 起点</h2>'
                '<p>这份报告只对首批账户执行 8 字符基础规则 要求，不能作为全站 8 字符基础规则 起点的对照。'
                '旧政策比较暂不展示；保留同一批账户的原始分布，使用当前 CDF 采样方法拟合。'
                '原始实验数据完整保留。</p></section>'+render_fit_diagnostics(result)+'</main>')
        return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>'+STYLE+
                DASHBOARD_STYLE+'</style><body>'+body+'</body></html>') if document else body
    arms = result.get('google_round_zipf', {}).get('arms')
    if not arms:
        return '<main><section><h2>此旧报告尚未补算共同 8 字符基础规则 起点</h2><p>请重新运行实验。</p></section></main>'
    cfg = result['config']
    labels = comparison_labels(result)
    endpoint_arms = {**arms, **result.get('site_controls', {})}
    dynamic = arms['google_dynamic']
    aggregate_mode = bool(result['metadata'].get('aggregate_attack_guard'))
    area_only_mode = result['metadata'].get('aggregate_attack_guard', {}).get('protocol') == 'fixed-F-area-only-batch-v2'
    distance_mode = bool(result.get('ideal_distribution'))
    area_mode = dynamic.get('distribution_goal', {}).get('metric') in (
        'fitted_complete_log_rank_cdf_area', DISTANCE_METRIC)
    completed = result['google_round_zipf']['experimental']['rounds_completed']
    budget = cfg['risk_budget']
    final, ledger = dynamic['final'], dynamic['final']['ledger']
    before = main_point(result['baseline']['risk'], budget)
    after = main_point(final['risk'], budget)
    parts = ['<main class="intervention-report"><section class="study-overview" id="intervention-overview">',
             '<span class="eyebrow">第三展示台 · 存量账户局部干预</span>',
             '<h2>改谁、怎么改、改多少</h2>',
             '<p>五组使用相同初始账户。后三组从同一个 8 字符基础规则 合规状态出发，'
             '共享 18 条候选规则、用户响应假设和攻击评估。</p><div class="metrics">']
    for label, value, note in [
        ('模拟账户', f'{ledger["accounts"]:,}', '本次各组使用相同初始账户'),
        ('后续受影响', pct(ledger.get('adaptive_affected_rate', ledger['affected_rate'])),
         f'{ledger.get("adaptive_affected", ledger["affected"]):,} 个账户在 8 字符基础规则 起点后被通知'),
        ('实际修改', pct(ledger['changed_rate']), f'{ledger["changed"]:,} 个账户完成修改'),
        (('距理想分布的 W1 距离' if distance_mode else '完整分布集中度' if area_mode else '拟合前段累计占比'),
         (f'{final["ideal_distance"]["fitted"]:.6f}' if distance_mode else
          f'{dynamic["distribution_goal"]["final_fitted_log_area"]:.6f}' if area_mode else
          pct(dynamic.get('distribution_goal', {}).get('final_fitted_top_mass'))),
         ('基于 CDF 拟合；越低越接近固定 N 单例理想' if distance_mode else
          '拟合累计曲线对数排名面积；越低越分散' if area_mode else
          f'固定前 {result["google_round_zipf"]["comparison_budget"].get("distribution_top_k", "—")} 位完整口令；越低越分散'))]:
        parts.append(f'<div class="metric">{escape(label)}<strong>{escape(value)}</strong><small>{escape(note)}</small></div>')
    parts.append(f'</div><p>以上为 8 字符基础规则 起点后逐步干预方案：已完成共同起点后的 {completed} 轮干预；停止原因：{escape(dynamic["stop_label"])}。</p></section>')
    if not aggregate_mode:
        parts.append('<section class="notice"><strong>历史实验：逐账户强度门槛</strong>'
                     '<p>以下结果仍属于原来的“每个成功修改账户都需变强”协议。'
                     '当前新实验采用全员成功修改与 F 面积单一准入条件；旧结果不能当作新方法的实验结果。</p></section>')
    if area_only_mode:
        parts.append('<section class="notice"><strong>本次实验：全员成功修改，面积下降才执行</strong>'
                     '<p>每个被选中账户均构造不同且合规的新口令。模拟方案的固定 F 曲线面积严格下降才下发并计入通知；'
                     '不满足条件的方案不通知、不计为执行轮次。未覆盖比例只披露，不否决方案；'
                     '模型未覆盖不能等同于更难猜，安全效果须结合 F、A1 与覆盖情况判断。</p></section>')
    extension = result['metadata'].get('analysis_extension')
    if extension:
        parts.append('<section class="notice"><strong>本次更新的结果来源</strong><p>'
                     f'五组轨迹沿用历史 10 万账户实验 {escape(extension["parent_run_id"])}，'
                     '没有重新执行动态选人和修改。W1 距离由这些完整分布和原有拟合结果重新计算；'
                     '固定人数下，它与原完整对数排名累计面积只相差同一个理想参照常数。'
                     'Yahoo! JAPAN 是在同一批原始账户上新运行的一次强策略对照，'
                     '不代表历史动态实验已经改用当前新协议。</p></section>')
    if distance_mode:
        parts.append(ideal_reference_html(result, labels, endpoint_arms))
    parts.append('<section id="intervention-conditions"><h2>先看懂这次比较</h2>'
                 '<p>图例支持勾选显示或隐藏曲线，悬停突出对应曲线；'
                 '重叠线使用线型与点形辅助区分。每张图保持相同方案顺序和颜色。</p>'
                 '<p>原始组保留原口令；另外四组先把全体账户迁移到满足 8 字符基础规则 最低 8 字符要求的同一状态。'
                 '8 字符基础规则 基础组此后不再调整；随机组先抽人再选规则；初始排序组只在起点规划一次；'
                 '完整动态组每轮重新分析分布。</p>')
    parts.append(table(['实验组', '要回答的问题'], [
        ['原始分布（无干预）', '原始风险有多高'],
        ['8 字符基础规则 基础策略', '基础策略本身改善多少'],
        ['8 字符基础规则＋随机分批调整', '优先选择影响分布的账户是否有价值'],
        ['8 字符基础规则＋初始排序后分批执行', '每轮重新分析分布是否有价值'],
        ['8 字符基础规则＋每轮重新评估的动态调整', '完整方法的结果'],
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
                     f'<p>三个 10 万账户实验分别用种子 42、43、44；每行的后三组共享该种子的 8 字符基础规则 起点。'
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
                      '每行使用同一随机种子下的共同 8 字符基础规则 起点和相同逐轮通知上限；各组可能提前停止或未用满预算。'
                     'Δ 为动态组减初始排序组，负值表示动态组估计风险更低；这是模拟点估计，'
                     '种子数量较少，不代表统计显著性。</p>')
        parts.append(table(['种子', '累计受影响账户', '随机组 F / A1',
                            '初始排序 F / A1', '逐轮动态 F / A1', '动态−初始排序 Δ F / A1'], rows))
        parts.append('</section>')
    parts.append('<p>初始排序组只在 8 字符基础规则 起点按分布改善排好后续账户和规则，之后不重新排序；'
                 '动态组每轮执行后，按新的完整口令分布重新选账户和规则。'
                 '随机组的账户顺序不看口令分布，选规则时使用相同的分布评分。'
                  '三组在运行前固定每轮与累计通知预算；未用完的预算如实保留。</p>')
    parts.append(f'<p>动态组比较热门口令、结构和跨结构账户中的局部动作，'
                  '随机组先抽取账户，再从文档第 1—18 条片段中分配修改方法。'
                   '三组均先比较完整口令的排名累计分布，再用 CDF 采样拟合参数复核靠前动作。'
                   + ('到固定 N 单例参照的对数排名 W1 距离越低，表示分布越接近理想。' if distance_mode else
                      '拟合后的完整累计曲线在对数排名上的面积越低，表示分布越分散。' if area_mode else
                      '拟合曲线在固定前段排名处的累计占比越低，表示热门完整口令覆盖的账户越少。') +
                  ('所有通知账户均成功修改；唯一提交条件是固定 F 曲线对数面积下降。未覆盖比例不参与否决，A1 用于独立终态评估。' if area_only_mode else
                   '整批修改须使固定 F 猜测成功曲线的对数面积下降，且模型未覆盖比例不增加；允许个体变弱。'
                   'F 面积作为独立准入条件，不与分布指标加权；A1 保留作事后检验。' if aggregate_mode else
                   '修改成功的账户还必须满足固定 F 模型的估计猜测次数严格增加；无法估计新旧次数时不批准修改。'
                   'F 和独立参考 A1 的攻击曲线保留作事后检验，不与分布指标加权。') +
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
    parts.append(f'<p>8 字符基础规则 起点有 {baseline_policy["eligible"]:,} 个账户原口令短于 8 字符，'
                 f'均在起始迁移阶段改为符合规则的口令（占 {pct(baseline_policy["affected_rate"])}）。'
                 '为构造严格符合 8 字符基础规则 长度规则的起点，起始迁移按全部账户完成处理；之后三组分批干预使用相同的通知上限、'
                  '用户响应率和累计附加预算。下方成本图从 8 字符基础规则 起点开始计算后续新增通知。</p>')
    parts.append(f'<p>共同起点属于全体合规的模拟设定：有限次修改尝试后仍未合规的 '
                 f'{baseline_policy.get("explicit_length_completions", 0):,} 个账户，通过固定种子的长度补全构造合规口令。'
                 + ('新协议的后续局部干预同样保证合规且不同的新口令；构造补全次数另行统计，不能解释为真实用户行为。</p>' if area_only_mode else
                    '该构造只用于起点；后续局部干预仍保留未响应和修改失败。</p>'))
    parts.append(table(['设置', '本次取值'], [
        ['后续每轮 / 累计新增通知上限', f'{pct(cfg["controller"]["round_fraction"])} / {pct(cfg["controller"]["total_fraction"])}'],
        ['每账户最多干预', ('8 字符基础规则 起点一次、后续局部干预一次；后续通知人数等于成功修改人数' if area_only_mode else
                            '8 字符基础规则 起点一次、后续局部干预一次；已通知但未修改也计入后续成本')],
        ['未响应概率（情景假设）', pct(cfg['response']['nonresponse'])],
        ['响应者：简单修补 / 片段重组 / 重选', ' / '.join(pct(x) for x in cfg['response']['weights'])],
        ['停止条件', '共同起点后最多 10 轮；无可行动作或预算不足时可提前停止'],
        ['基础要求', '四个 8 字符基础规则 组全站至少 8 字符；局部要求在此基础上叠加'],
        ['分布主目标', ('到固定 N 单例参照的对数排名 W1 距离下降' if distance_mode else
                        'CDF 拟合完整累计曲线的对数排名面积下降' if area_mode else
                    f'CDF 拟合排名曲线前 {result["google_round_zipf"]["comparison_budget"].get("distribution_top_k", "—")} 位累计占比下降')],
        (['方案接受条件', '固定 F 曲线面积严格下降；全员修改成功，未覆盖比例只作诊断'] if area_only_mode else
         ['总体安全门槛', '整批 F 猜测成功曲线对数面积严格下降，模型未覆盖比例不增加'] if aggregate_mode else
         ['个体安全门槛', '仅通过固定 F 模型可比较且新估计猜测次数严格更大的局部修改']),
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
                      '8 字符基础规则 起点的通知另行统计；20% 是后续通知预算上限。</p>')
    if all(left['state_sha256'] == right['state_sha256'] for left, right in
           zip(frozen_arm['trajectory'], dynamic['trajectory'])) and len(frozen_arm['trajectory']) == len(dynamic['trajectory']):
        parts.append('<p class="notice">本次初始排序组与逐轮动态组的每轮账户状态、F 和 A1 终值完全重合。'
                      '本次未观察到逐轮重新分析相对初始排序的额外收益；与随机组选人的效果须按共同实际成本比较。'
                     '冻结组只在共同 8 字符基础规则 起点规划账户和规则；相同轨迹不是重新规划的结果。</p>')
    random_already = random_arm['final']['ledger'].get('already_compliant', 0)
    if random_already:
        parts.append(f'<p>随机组有 {random_already:,} 名被通知账户已符合选中的规则，计入通知成本但没有修改口令。'
                     '因此相同通知人数不代表相同实际修改人数。</p>')
    parts.append('<p class="notice">PCFG 猜测次数和攻击曲线是模型估计，不是实际破解记录。'
                 '无法估计猜测次数的候选修改不会被当作个人强度提升；'
                 '8 字符基础规则 起点的构造迁移单独记账。后三组共用每轮与累计预算上限；'
                 '只在曲线共同覆盖的成本区间内比较，不外推到未达到的 20% 附加覆盖。</p></section>')
    specs = chart_specs(result)
    rank = result['google_round_zipf']['comparison_budget'].get('distribution_top_k')
    for name, (title, series, opts) in specs.items():
        if name.startswith('cdf_fit_'):
            continue  # Fit diagnostics have their own explanation and tables below.
        parts.append(f'<section id="{escape(name)}"><h2>{escape(title)}</h2>')
        if name == 'distribution_cost':
            parts.append(('<p>纵轴是 CDF 拟合分布与固定 N 单例理想分布的 Wasserstein-1 距离，'
                          '在归一化对数排名轴上计算累计曲线之间的面积；越低越接近理想。'
                          '这不是猜测成功率，也不是排名频次图的像素距离。' if distance_mode else
                          '<p>这是动作选择的主指标：用 c、s 生成完整的拟合累计曲线，计算对数排名面积。'
                          '纵轴越低表示口令分布越分散；该分数不是猜测成功率。' if area_mode else
                          f'<p>这是动作选择的主指标：把 c、s 拟合参数生成的排名累计曲线固定在前 {rank:,} 位，'
                          '纵轴越低，表示热门完整口令覆盖的账户越少。') +
                          '横轴只计共同 8 字符基础规则 起点后的新增通知；'
                         '原始组和 8 字符基础规则 基础组各只有一个点，8 字符基础规则 起点迁移的通知成本另列。'
                         '后三组只在实际到达的成本范围内比较，不外推。'
                         '这是单次模拟拟合；组间差异接近拟合误差时，不能认定某方法稳定占优。</p>')
        if name == 'dynamic_distribution_detail':
            parts.append('<p>只放大动态组最后四个实际观测点，纵轴范围与五组总图不同。'
                         '水平线段表示本轮拟合分数没有可见变化；未执行的轮次不补点。'
                         '精确数值见下方逐轮表。</p>')
        if name == 'attack_area_cost':
            parts.append(f'<p>在 {cfg["budgets"][0]:,}—{cfg["budgets"][-1]:,} 次猜测范围内，'
                         '沿现有对数横轴对 F 曲线各观测点连线做梯形积分，再除以横轴跨度。'
                         '每个数量级等权，纵轴为归一化面积；同一账户总数下与累计猜中人数面积的比较方向一致。'
                         '允许部分用户变弱、部分预算点命中率上升，只要求整条曲线面积下降。'
                         + ('方案面积预检未通过时不下发、不计通知；已执行轮次全员修改成功。</p>' if area_only_mode else
                            '整批候选未通过执行前复核时保持旧口令，通知仍计成本；此时曲线呈水平段。</p>'))
        if name == 'coverage_F':
            parts.append(f'<p>横轴从共同 8 字符基础规则 起点开始，统计后续被通知的不同账户，纵轴是固定 F 攻击模型在 {budget:,} 次猜测下的估计命中比例。'
                         '原始组和 8 字符基础规则 基础组各只有一个真实成本点；后三组曲线连接实际轮次观测。'
                         '相同横轴位置才能比较方法差异，未到达的成本不作外推。</p>')
        if name == 'attack_A1':
            parts.append('<p>A1 只用独立开发参考群体的模拟迁移结果训练。参考群体构成不同，'
                         '实际干预比例可能与目标群体不同，见下方覆盖与参考账本。</p>')
        if name in ('attack_F', 'attack_A1') and result.get('site_controls'):
            parts.append('<p>Yahoo! JAPAN 是从原始数据独立进行一次全站强制合规迁移；'
                         '其通知成本通常远高于局部预算，不能据此认定同成本下更优。'
                         '长口令超出模型支持时，低命中比例可能来自覆盖不足，请同时查看下方覆盖表。</p>')
        if name == 'final_distribution':
            parts.append('<p>横轴为频次排名，纵轴为使用人数，两轴均为对数刻度；'
                         '曲线更分散本身不能证明更安全。</p>')
            if distance_mode:
                parts.append('<p>理想参照为同样 N 个账户各使用一个不同完整口令，因此 f(r)=1，'
                             '一直延伸到排名 N；不根据每组当前不同口令数改变参照。'
                             '曲线可能与实际分布的单例尾部重合，可通过图例单独查看。</p>')
        if name == 'google_round_zipf':
            comparison = result['google_round_zipf']
            completed = comparison['experimental']['rounds_completed']
            parts.append('<p>蓝线是双方完成全体账户的 8 字符基础规则 最低 8 字符迁移后的共同起点；'
                         '固定对照从这个时点起不再调整，实验组随后根据最新分布最多决策 10 轮。'
                         '每条实验曲线对应一次调整后的全站分布，两轴均为对数刻度。</p>')
            parts.append(f'<p>初始 8 字符基础规则 迁移修改了 {pct(comparison["control"]["affected_rate"])} 的账户；'
                          '完成后全体账户均符合至少 8 字符规则；后续干预成本图从此时的零新增通知起算。</p>')
            if completed != comparison['requested_rounds']:
                parts.append(f'<p class="notice">本次实验请求 {comparison["requested_rounds"]} 轮，实际完成 {completed} 轮；'
                             f'停止原因：{escape(comparison["experimental"]["stop_label"])}。图中没有复制末态补足轮数。</p>')
            elif comparison['common_google_start_verified']:
                parts.append('<p>已核对双方起点的账户状态完全一致。图中共 11 条线：'
                             '共同起点的固定对照 1 条，加上实验组后续第 1–10 轮的 10 条分布。</p>')
            if distance_mode:
                parts.append(f'<p>另加入固定 N 单例理想分布参照，共 {completed+2} 条实际曲线。'
                             '理想线是评价参照，不是另一个模拟实验或补足的轮次。</p>')
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
                     '<p>第 0 轮是双方共同的 8 字符基础规则 起点，后续各点只取实验组实际完成的轮次。'
                     '对每轮完整口令频次分别使用与动作选择相同的 CDF 采样方法拟合。'
                     '实线表示 c，虚线表示 s；圆点和通向该点的线段用颜色标识轮次。'
                     '两项参数使用上下独立纵轴，以免数值量级不同掩盖变化。</p>')
        parts.append(round_parameter_html(rows, requested_rounds=result['google_round_zipf']['requested_rounds']))
        parts.append(f'<p><a href="/api/intervention/figure/{result["metadata"]["run_id"]}/round_parameters.svg">下载带图例 SVG</a></p>')
        parts.append(table(['轮次', '参数 c', '参数 s', '平均最大 CDF 误差', '累计受影响账户'] +
                           (['实际频次 W1', 'CDF 拟合 W1'] if distance_mode else []), [
            [row['round'], f'{row["parameters"]["c"]:.7f}', f'{row["parameters"]["s"]:.6f}',
             '—' if row['mean_max_cdf_error'] is None else f'{row["mean_max_cdf_error"]:.3%}',
             pct(row['affected_rate'])] +
             ([f'{dynamic["trajectory"][row["round"]]["ideal_distance"]["empirical"]:.9f}',
               f'{dynamic["trajectory"][row["round"]]["ideal_distance"]["fitted"]:.9f}'] if distance_mode else [])
             for row in rows]))
        parts.append('<p>c、s 描述拟合后的频次形状，没有单独的“越大越安全”方向或统一合格阈值。'
                      + ('新控制器用二者生成的完整排名累计曲线比较距理想的 W1 距离，不把参数直接相加。' if distance_mode else
                         '控制器用二者生成的完整排名累计曲线比较对数排名面积，不把参数直接相加。' if area_mode else
                         '控制器用二者生成的排名累计曲线比较固定前段的账户占比，不把参数直接相加。') +
                     '拟合误差反映模型与实际分布的差距；攻击命中比例和账户修改成本仍单独展示。</p></section>')
    parts.append(render_fit_diagnostics(result))
    parts.append('<section id="intervention-rounds"><h2>每轮具体做了什么</h2>')
    if not dynamic['rounds']:
        parts.append('<p>本次没有执行动作。候选没有通过分布收益、安全门槛或预算检查时，系统会停止，'
                     '不会为了展示效果强制修改账户。</p>')
        parts.append('<h3>动态调整每轮口令强度增加的账户比例</h3>'
                     '<p>本次动态组没有完成干预轮次，因此没有可计算的逐轮变化。</p>')
    else:
        parts.append(table(['轮次', '改谁', '怎么改', '通知 / 成功', '后续累计通知',
                             '预计 / 实现完整分布分数下降' if area_mode else '预计 / 实现拟合前段占比下降'], [
            [r['round'], r['action']['group'], r['action']['rule'], f'{r["action"]["selected"]} / {r["changed"]}',
              pct(dynamic['trajectory'][i+1]['ledger'].get('adaptive_affected_rate',
                  dynamic['trajectory'][i+1]['ledger']['affected_rate'])),
               (f'{r["prediction"].get("predicted_fitted_log_area_gain", 0):.6f} / '
                f'{r.get("realized_distribution_gain", 0):.6f}' if area_mode else
                f'{100*r["prediction"].get("predicted_fitted_cdf_gain", 0):.3f} / '
                f'{100*r.get("realized_distribution_gain", 0):.3f} 个百分点')]
            for i, r in enumerate(dynamic['rounds'])]))
        cumulative_strength = 0
        strength_rows = []
        for r in dynamic['rounds']:
            improved = r.get('strength_improved', r['changed'])
            notified = r['action']['selected']
            cumulative_strength += improved
            if aggregate_mode:
                diagnostic = r['strength_diagnostics']
                strength_rows.append([r['round'], notified, r['changed'], improved,
                                      diagnostic['weakened'], diagnostic['equal'], diagnostic['uncomparable'],
                                      pct(improved/notified if notified else 0),
                                      pct(improved/ledger['accounts']), pct(cumulative_strength/ledger['accounts'])])
            else:
                strength_rows.append([r['round'], notified, improved,
                                      pct(improved/notified if notified else 0),
                                      pct(improved/ledger['accounts']),
                                      pct(cumulative_strength/ledger['accounts']),
                                      r.get('strength_rejections', 0)])
        parts.append('<h3>动态调整每轮口令强度增加的账户比例</h3>'
                     '<p>只统计动态组后续局部干预。强度增加指固定 F 模型对新旧口令均给出'
                     '估计猜测次数，且新口令严格更难猜。' +
                     ('所有被通知账户都成功修改；变弱、持平和无法比较分别统计，个体变化仅作诊断。</p>' if area_only_mode else
                      '变弱、持平和无法比较分别统计；个体变化仅作诊断，总体面积决定整批能否提交。</p>'
                      if aggregate_mode else '未响应或不能证明增加者保持旧口令。'
                      '“被拒尝试”计口令候选次数，不是独立用户数。</p>'))
        headers = (['轮次', '通知', '成功修改', '变强', '变弱', '持平', '无法比较',
                    '变强占通知', '变强占全站', '累计变强占全站'] if aggregate_mode else
                   ['轮次', '本轮通知', '猜测次数增加账户', '占本轮通知',
                    '本轮占全站', '累计占全站', '未通过强度检查的候选尝试'])
        parts.append(table(headers, strength_rows))
        if aggregate_mode:
            parts.append('<h3>动态组每轮总体猜测面积复核</h3>'
                         '<p>在模拟中先收集一批新口令提案，统一检查后再写入账户状态。'
                         + ('面积预检未通过的方案不下发、不计通知；每个候选仅用既定执行随机流检查，不重抽响应直到通过。</p>' if area_only_mode else
                            '未通过的整批修改保留旧口令，仍计通知成本；不会重抽响应直至通过。</p>'))
            parts.append(table(['轮次', '修改前面积', '提案预计下降', '实际修改后面积', '整批是否通过', '未通过原因'], [
                [r['round'], f'{r["aggregate_attack_guard"]["before_area"]:.9f}',
                 f'{r["aggregate_attack_guard"]["proposed_area_gain"]:.9f}',
                 f'{r["aggregate_attack_guard"]["after_area"]:.9f}',
                 '通过' if r['aggregate_attack_guard']['accepted'] else '未通过，保留旧口令',
                 '；'.join(r['aggregate_attack_guard']['rejection_reasons']) or '—']
                for r in dynamic['rounds']]))
            if area_only_mode:
                parts.append('<p>下表披露模型未覆盖的净变化，它不参与方案否决；正值表示更多账户无法由 F 评估。</p>')
                parts.append(table(['轮次', '成功修改', '显式规则补全', 'F 未覆盖账户净变化', '覆盖诊断'], [
                    [r['round'], r['changed'], r.get('explicit_completions', 0),
                     f'{r["aggregate_attack_guard"]["proposed_uncovered_rate_change"]*ledger["accounts"]:+.0f}',
                     '；'.join(r['aggregate_attack_guard'].get('diagnostic_warnings', [])) or '—']
                    for r in dynamic['rounds']]))
        for r in dynamic['rounds']:
            round_label = f'调整第 {r["round"]} 轮'
            parts.append(f'<details><summary>{round_label}选择依据与候选比较</summary>'
                         f'<p>{escape(r["selection_reason"])}。未响应 {r["nonresponse"]} 人，'
                         f'尝试后未完成 {r["failed_to_comply"]} 人。</p>')
            ordered = sorted(r['candidate_audit'], key=lambda x: -x['score'])[:12]
            parts.append(table(['对象', '要求', '人数',
                                '预计完整分布分数下降' if area_mode else '预计分布前段占比下降', '判断'], [
                [a['action']['group'], a['action']['rule'], a['action']['selected'],
                  (f'{a["score"]:.6f}' if area_mode else
                   f'{100*(a.get("predicted_fitted_cdf_gain") if a.get("predicted_fitted_cdf_gain") is not None else a.get("predicted_empirical_cdf_gain", 0)):.3f} 个百分点'),
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
        a = endpoint_arms[key]
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
        [label, pct(endpoint_arms[key]['final']['ledger']['affected_rate']), pct(endpoint_arms[key]['final']['ledger']['changed_rate']),
         f'{endpoint_arms[key]["final"]["ledger"]["edit_cost"]:.4f}',
         pct(endpoint_arms[key]['adaptive_reference_ledger']['affected_rate']), endpoint_arms[key]['stop_label']]
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
