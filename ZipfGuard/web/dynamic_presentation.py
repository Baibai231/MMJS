"""Aggregate-only HTML for the sequential registration experiment."""
from __future__ import annotations

from web.presentation import STYLE, escape, plot, table
from web.final_distribution import render_final_distribution
from web.registration_distribution import render_registration_distribution, render_modification_cost
from web.dynamic_evidence import (render_comparison_conditions, render_collision,
                                  render_decision_evidence)
from policy.user_response import RESPONSE_PROTOCOL
from web.dynamic_comparison import (PRESET_LABEL, displayed_controls, attack_groups,
                                    attack_entries, attack_series)

from policy.site_catalog import site_labels
CONTROL_LABELS = {'fixed_preset': PRESET_LABEL, 'fixed_length8': PRESET_LABEL, **site_labels()}



def _percent(value):
    return '—' if value is None else f'{value * 100:.2f}%'


def _collision(value):
    return '—' if value is None else f'{value * 1_000_000:,.2f}'


def _point(point):
    if point['rate'] is not None:
        return _percent(point['rate'])
    if point['lower_bound'] is None:
        return '无可用目标'
    return (f"未完成 [{_percent(point['lower_bound'])}, "
            f"{_percent(point['upper_bound'])}]")


def _risk_curve(evaluation):
    return [(row['budget'], row['rate']) for row in evaluation['minauto']]


def render_dynamic_html(result, *, document=True):
    rows = result['cohorts']
    final = result['final_distribution']
    base = result['baseline_distribution']
    paired = result['baseline_completed_distribution']
    budget = result['config']['budgets'][-1]
    informed = result['metadata'].get('response_protocol') == RESPONSE_PROTOCOL
    parts = ['<main class="dynamic-report">',
             '<section><span class="muted">DYNAMIC REGISTRATION · DISTRIBUTION FEEDBACK</span>',
             '<h1>分批注册与网站口令策略对照</h1><div class="metrics">']
    for label, value in [
        ('模拟注册用户', f"{result['dataset']['registration_occurrences']:,}"),
        ('注册批次', len(rows)), ('每模型最高预算', f'{budget:,}'),
        ('最终完成用户', final['users']),
        ('计算时间', f"{result['metadata']['runtime_seconds']:.1f} 秒")]:
        parts.append(f'<div class="metric">{escape(label)}<strong>{escape(value)}</strong></div>')
    parts.append('</div><p>注册顺序由固定种子模拟。每轮只使用之前批次的累计分布选下一批策略；老用户保留注册时的口令。</p></section>')
    if 'monte_carlo' in result['config']:
        from web.monte_carlo_presentation import render_mc_attacks
        from web.policy_rankings import render_path_search_status
        from web.sequence_rankings import render_sequence_rankings
        parts.append(render_path_search_status(result))
        parts.append(render_mc_attacks(result))
        parts.append(render_sequence_rankings(result))
    if result['metadata'].get('research_smoke_only'):
        parts.append('<section class="notice"><strong>模型接入验证，非正式安全结论。</strong>'
                     '<p>本次使用缩小架构的 PassGPT，结果仅检查流程与预算。PassLLM 尚未参与，'
                     '不能据此确定最终策略候选池或宣称完成四模型实验。</p></section>')
    if informed:
        summary = result['registration_summary']
        parts.append('<section><h2>口令修改与重试记录</h2>')
        parts.append('<p>长度和字符类别等显式要求在生成口令时一次满足。黑名单、键盘模式等隐藏限制在提交后检查；'
                     '被阻断就生成另一个满足显式要求的候选。阻断来自实际规则检查，不模拟用户放弃。</p>')
        parts.append(f"<p>连续 {summary['retry_report_after']} 次新候选仍未通过时记录并继续重试；"
                     f"每人最多计算 {summary['candidate_limit']:,} 次新候选，届时仍未通过的记录保留为待处理。</p>")
        parts.append(table(['统计项', '人数 / 次数'], [
            ['原始用户（保留记录）', f"{summary['initial_users']:,}"],
            ['已生成合规口令', f"{summary['registered_users']:,}"],
            ['曾被隐藏规则阻断的用户', f"{summary['hidden_blocked_users']:,}"],
            ['隐藏规则阻断总次数（含原口令）', f"{summary['hidden_rejections']:,}"],
            ['达到重试报告阈值的用户（之后继续重试）', f"{summary['retry_reported_users']:,}"],
            ['计算上限内仍待处理的用户', f"{summary['pending_users']:,}"],
        ]))
        parts.append('<p class="muted">待处理记录保留原口令、用户编号和重试计数；没有最终口令时不计入已完成口令的攻击分母。'
                     '修补、片段重组、重选权重只用于隐藏阻断后的候选生成，不是放弃概率。</p></section>')
    else:
        parts.append('<section class="notice">当前展示的是旧版行为模拟结果，包含随机放弃或重试终止。'
                     '它不是新逻辑的结果；重新运行后才会生成不模拟放弃的分布与攻击指标。</section>')
    if 'monte_carlo' in result['config']:
        parts = [p.replace('没有最终口令时不计入已完成口令的攻击分母', '待处理用户保留在全部原始用户的攻击分母中') for p in parts]
    parts.append(render_final_distribution(result))
    parts.append('<section><h2>注册过程中的分布与用户成本</h2>')
    parts.append(render_comparison_conditions(result, CONTROL_LABELS))
    parts.append(render_registration_distribution(result, CONTROL_LABELS))
    parts.append(render_collision(result, CONTROL_LABELS))
    parts.append(render_modification_cost(result))
    parts.append(table(['批次', '注册区间', '策略', '是否更新', '本批修改率',
                        '本批完成率', '额外尝试/人', '尝试 P90', '编辑距离 P90', '累计每百万对同口令数'], [
        [r['cohort_id'], f"{r['start_user']:,}–{r['end_user']:,}",
         r['policy']['name'], '是' if r['policy_changed'] else '保持',
         _percent(r['response']['modification_rate']),
         _percent(r['response']['completion_rate']),
         f"{r['response']['mean_extra_attempts']:.2f}",
         r['response']['p90_extra_attempts'],
         r['response']['p90_edit_distance_modified'] if r['response']['p90_edit_distance_modified'] is not None else '—',
         _collision(r['cumulative']['collision_probability'])] for r in rows]))
    if informed:
        parts.append(table(['批次', '显式要求一次修正人数', '隐藏阻断人数',
                            '隐藏阻断次数', '多次未通过报告人数', '仍待处理人数'], [
            [r['cohort_id'], r['response']['visible_repaired_users'],
             r['response']['hidden_blocked_users'], r['response']['hidden_rejections'],
             r['response']['retry_reported_users'], r['response']['pending_users']] for r in rows]))
    parts.append('</section><section><h2>每次新增限制后的本批分布</h2>')
    for r in rows:
        if not r['policy_changed']:
            continue
        parts.append(f"<details><summary>第 {r['cohort_id']} 批：{escape(r['step_before_policy']['name'])} → {escape(r['policy']['name'])}</summary>")
        parts.append(plot([('新增前', r['step_before']['rank_curve']),
                           ('新增后', r['step_after']['rank_curve']),
                           ('同批无策略', r['baseline_cohort']['rank_curve'])],
                          xlabel='本批口令热门程度排名（1 = 最热门）', ylabel='使用这一条口令的用户比例', log=True,
                          distinguish=True, x_format='count', y_format='percent'))
        parts.append(table(['同批指标', '新增前', '新增后', '无策略'], [
            ['每百万对同口令数', _collision(r['step_before']['collision_probability']),
             _collision(r['step_after']['collision_probability']),
             _collision(r['baseline_cohort']['collision_probability'])],
            ['Top-10 占比', _percent(r['step_before']['top_mass']['10']),
             _percent(r['step_after']['top_mass']['10']),
             _percent(r['baseline_cohort']['top_mass']['10'])]]))
        parts.append('</details>')
    parts.append('</section><section><h2>历史反馈与选择理由</h2>')
    parts.append(table(['完成批次', '下一批动作', '决策', '历史完成用户'], [
        [r['after_cohort'], r['action'], r['status'], r['history_users']]
        for r in result['decisions']]))
    parts.append(render_decision_evidence(result))
    if result['config']['controller'].get('sequence') is None:
        parts.append('<p class="muted">候选先满足合规生成与修改成本要求，再比较预测累计分布；头部风险是开发数据代理指标。正式攻击结果不回流当批策略。</p>')
    parts.append('</section>')
    if 'monte_carlo' in result['config']:
        parts.append('</main>')
        body = ''.join(parts)
        return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>PCFG 蒙特卡洛 · Top15</title><style>' + STYLE + '</style><body>' + body + '</body></html>') if document else body
    attacks = result['attacks']
    parts.append('<section id="attack-success"><h2>攻击效果：猜测次数增加时，累计猜中多少用户？</h2>')
    parts.append('<p>横轴是每个攻击模型已尝试的不同有效猜测次数，纵轴是截至这一次数累计猜中的用户比例。'
                 '同样的猜测次数下，曲线越低，表示口令越难被这些模型猜中。鼠标停在数据点上可查看次数和比例。</p>')
    parts.append('<p class="muted">每个点对应一个实际评估的预算；连线只帮助看趋势，不代表测量了两个点之间的每一次猜测。'
                 '横轴按 10 倍间隔排列，纵轴直接显示百分比。多模型命中同一用户只计算一次（Min_auto），预算按每模型分别计数。</p>')
    series = attack_series(result)
    parts.append('<div class="strategy-attacks"><style>'
                 '.strategy-attacks > .plot-distinct > .legend{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}'
                 '@media(max-width:800px){.strategy-attacks > .plot-distinct > .legend{display:flex}}'
                 '</style>')
    parts.append(plot(series, xlabel='每个模型的累计猜测次数',
                      ylabel='累计猜中的用户比例', log=True, distinguish=True,
                      x_ticks=result['config']['budgets'], x_format='count', y_format='percent'))
    parts.append('</div>')
    parts.append('<p class="muted">F：沿用原始开发数据训练的攻击流。A0：知道注册规则，并过滤不合规则的猜测。'
                 'A1：根据策略实施后的模拟开发口令重新训练。未完成预算的点不连线，其上下界在下表显示，缺失不代表 0%。</p>')
    if 'by_strategy' in attacks:
        parts.append('<p>无策略不筛除猜测，也不改变开发口令，因此 F、A0、A1 三条曲线完全重合；'
                     '它们仍可独立勾选。固定预设策略全程为长度至少 8，动态策略从相同规则起步。</p>')
    else:
        parts.append('<p class="notice">旧报告缺少三组策略完整的 F、A0、A1 评价，请重新运行；未计算的曲线不补画。</p>')
    if displayed_controls(result):
        modified = sum(row['response']['modified_users'] for row in rows)
        attempted = result['dataset']['registration_occurrences']
        points = [('无策略', [(0, attacks['baseline_F']['minauto'][-1]['rate'])]),
                  ('动态策略', [(modified / attempted, attacks['A1']['minauto'][-1]['rate'])])]
        for name, control in displayed_controls(result).items():
            if name in attacks['controls']:
                label = CONTROL_LABELS.get(name, name)
                points.append((label, [(control['modified_users'] / attempted,
                                        attacks['controls'][name]['minauto'][-1]['rate'], label)]))
        parts.append('<h3>用户修改率与自适应攻击风险</h3>')
        parts.append(plot(points, xlabel='原始用户中的修改率',
                          ylabel=f'每模型 {budget:,} 次猜测下的累计命中比例（A1）', scatter=True,
                          distinguish=True, x_format='percent', y_format='percent'))
    entries = attack_entries(result)
    parts.append('<details><summary>查看各策略、各攻击层次的数值与未完成界限</summary>')
    parts.append(table(['策略与攻击', '每模型累计猜测次数', '累计命中比例', '统计用户数'], [
        [name, f"{p['budget']:,}", _point(p), p['target_weight']]
        for name, ev in entries for p in ev['minauto']]))
    parts.append('</details>')
    parts.append('<h3>达到指定命中率所需猜测数</h3>')
    difficulty = attacks['guess_counts']
    parts.append(table(['目标命中率', '无策略 F', '动态 F', '动态 A1'], [
        [_percent(float(fraction))] + [
            (str(difficulty[key]['quantiles'][fraction]['guess_count'])
             if difficulty[key]['quantiles'][fraction]['status'] == 'exact'
             else difficulty[key]['quantiles'][fraction]['status'])
            for key in ('baseline_F', 'dynamic_F', 'dynamic_A1')]
        for fraction in ('0.1', '0.25', '0.5')]))
    if attacks.get('paired_dynamic_minus_control'):
        parts.append('<h3>与固定策略的同用户配对差</h3>')
        parts.append(table(['固定策略', '共同完成用户', '动态减固定的命中率差',
                            '条件 95% 区间'], [
            [CONTROL_LABELS.get(name, name), point['paired_users'],
             f"{100*point['difference']:+.2f} 个百分点" if point['difference'] is not None else '预算未完成',
             (f"[{100*point['ci95_lower']:+.2f}, {100*point['ci95_upper']:+.2f}]"
              if point['ci95_lower'] is not None else '—')]
            for name, points in attacks['paired_dynamic_minus_control'].items()
            if name in displayed_controls(result)
            for point in points if point['budget'] == budget]))
        parts.append('<p class="muted">正值表示动态策略在同一批完成用户上更容易被猜中。区间条件于已固定的攻击流与策略，不包含重新训练和重新选择的不确定性。</p>')
    parts.append('<details><summary>攻击器完成状态与计算量</summary>')
    for name in ('F', 'A1'):
        parts.append(f'<h3>{escape(name)}</h3>')
        parts.append(table(['模型', '有效猜测', '原始输出', '停止原因'], [
            [r['run']['model'], r['run']['charged_count'],
             r['run']['raw_generated_count'], r['run']['stop_reason']]
            for r in attacks[name]['models']]))
        coverage_rows = [
            [r['run']['model'], r['support_coverage']['target_weight'],
             r['support_coverage']['outside_declared_support_weight']]
            for r in attacks[name]['models'] if 'support_coverage' in r]
        if coverage_rows:
            parts.append(table(['模型', '评价用户总数（分母）', '超出模型声明范围的用户'], coverage_rows))
            parts.append('<p class="muted">超出范围的用户仍保留在命中率分母中；未被模型覆盖不代表口令安全。</p>')
    parts.append('</details><p class="muted">A0 使用已知批次规则投影冻结候选流；各批结果在 JSON 中。未完成预算显示区间，不作为零风险。</p></section>')
    strength = result['user_strength']
    parts.append('<section><h2>逐用户猜测难度变化</h2>')
    parts.append(table(['状态', '用户数'], list(strength['status_counts'].items())))
    histogram = strength['exact_log2_gain_histogram']
    if histogram:
        parts.append(plot([('精确可算用户', [(int(key), count) for key, count in
                                   sorted(histogram.items(), key=lambda item: int(item[0]))])],
                          xlabel='log2(修改后排名 / 修改前排名) 的整数区间',
                          ylabel='用户数'))
    parts.append('<p>每名用户的原口令、最终口令、前后猜测排名、精确提升率或界限保存在本机私有文件：</p>')
    parts.append(f"<p><code>{escape(strength['local_private_file'])}</code></p>")
    parts.append('<p class="muted">公开页面与 JSON 不包含口令字符串。百分比基于同一不按新策略过滤的攻击流；超过预算的用户只报告界限或未决。</p></section>')
    parts.append('</main>')
    body = ''.join(parts)
    if not document:
        return body
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>ZipfGuard · 分批注册实验</title><style>' + STYLE +
            '</style><body>' + body + '</body></html>')
