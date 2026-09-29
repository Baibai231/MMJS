"""Aggregate-only HTML for the sequential registration experiment."""
from __future__ import annotations

from web.presentation import STYLE, escape, plot, table

CONTROL_LABELS = {
    'fixed_length8': '固定长度 8',
    'fixed_complexity': '固定长度与字符类别',
    'static_selected': '开发集选定固定规则',
    'fixed_schedule': '固定加严日程',
}


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
    parts = ['<main class="dynamic-report">',
             '<section><span class="muted">DYNAMIC REGISTRATION · DISTRIBUTION FEEDBACK</span>',
             '<h1>分批注册的动态口令策略</h1><div class="metrics">']
    for label, value in [
        ('模拟注册用户', f"{result['dataset']['registration_occurrences']:,}"),
        ('注册批次', len(rows)), ('每模型最高预算', f'{budget:,}'),
        ('最终完成用户', final['users']),
        ('计算时间', f"{result['metadata']['runtime_seconds']:.1f} 秒")]:
        parts.append(f'<div class="metric">{escape(label)}<strong>{escape(value)}</strong></div>')
    parts.append('</div><p>注册顺序由固定种子模拟。每轮只使用之前批次的累计分布选下一批策略；老用户保留注册时的口令。</p></section>')
    parts.append('<section id="final-distribution"><h2>最终分布：热门口令还有多少人在用？</h2>')
    parts.append('<p>把口令按使用人数从多到少排列：横轴 1 是最热门口令，10 是第 10 热门口令；'
                 '纵轴是使用这一条口令的人占多少。头部曲线越低，说明用户越少集中在少数热门口令上。</p>')
    parts.append(table(['指标', '策略前：全部用户', '策略前：最终完成注册的用户', '策略后：同一批完成用户'], [
        ['同口令的用户对数 / 百万对（越低越好）', _collision(base['collision_probability']),
         _collision(paired['collision_probability']),
         _collision(final['collision_probability'])],
        ['使用最热门 10 个口令的用户比例（越低越好）', _percent(base['top_mass']['10']),
         _percent(paired['top_mass']['10']),
         _percent(final['top_mass']['10'])],
        ['不同口令个数 ÷ 用户人数（越高越分散）', _percent(base['unique_rate']),
         _percent(paired['unique_rate']),
         _percent(final['unique_rate'])],
        ['用户数', base['users'], paired['users'], final['users']]]))
    parts.append('<p class="muted">比较策略前后时，重点看后两列，它们对应同一批人。'
                 '“同口令的用户对数”是随机配成 100 万对不同用户时，预计有多少对使用完全相同的口令。'
                 '“不同口令个数 ÷ 用户人数”例如 100 人使用 80 个不同口令，值就是 80%，不等于 80% 的用户口令从未重复。</p>')
    parts.append(plot([('策略前：全部用户', base['rank_curve']),
                       ('策略前：最终完成注册的用户', paired['rank_curve']),
                       ('策略后：同一批完成用户', final['rank_curve'])],
                      xlabel='口令热门程度排名（1 = 最热门）', ylabel='使用这一条口令的用户比例',
                      log=True, distinguish=True, x_format='count', y_format='percent'))
    parts.append('<p class="muted">例：点 (10, 0.10%) 表示第 10 热门口令被该组 0.10% 的用户使用，不是前 10 个口令的合计。'
                 '各条线独立排序，同一名次不一定是同一个口令；这里只画前 1,000 名。横轴每向右一大格，排名扩大 10 倍。</p>')
    parts.append('<p class="muted">勾选显示对应曲线，取消勾选隐藏；悬停图例或用键盘聚焦复选框可突出该曲线。曲线重合时可单独勾选查看。</p></section>')
    parts.append('<section><h2>注册过程中的分布与用户成本</h2>')
    for metric, title in [('collision_probability', '每百万对用户同口令数（估计）'),
                          ('unique_rate', '不同口令比例')]:
        scale = 1_000_000 if metric == 'collision_probability' else 1
        series = [
            ('无策略', [(r['end_user'], scale * r['baseline_cumulative'][metric]) for r in rows]),
            ('相同完成用户的原口令', [(r['end_user'], scale * r['baseline_completed_cumulative'][metric])
                                           for r in rows]),
            ('动态策略', [(r['end_user'], scale * r['cumulative'][metric]) for r in rows])]
        for name, control in result['controls'].items():
            series.append((CONTROL_LABELS.get(name, name), [(rows[i]['end_user'], scale * value['cumulative'][metric])
                                  for i, value in enumerate(control['timeline'])]))
        parts.append(f'<h3>{escape(title)}</h3>')
        parts.append(plot(series, xlabel='已尝试注册人数', ylabel=title, distinguish=True,
                          x_format='count', y_format='percent' if metric == 'unique_rate' else None))
    parts.append(plot([('修改率', [(r['end_user'], r['response']['modification_rate'])
                                   for r in rows]),
                       ('未完成率', [(r['end_user'], 1-r['response']['completion_rate'])
                                     for r in rows])],
                      xlabel='已尝试注册人数', ylabel='本批用户比例', distinguish=True,
                      x_format='count', y_format='percent'))
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
    parts.append('<p class="muted">候选先满足完成率与修改成本，再比较预测累计分布；头部风险是开发数据代理指标。正式攻击结果不回流当批策略。</p></section>')
    attacks = result['attacks']
    parts.append('<section id="attack-success"><h2>攻击效果：猜测次数增加时，累计猜中多少用户？</h2>')
    parts.append('<p>横轴是每个攻击模型已尝试的不同有效猜测次数，纵轴是截至这一次数累计猜中的用户比例。'
                 '同样的猜测次数下，曲线越低，表示口令越难被这些模型猜中。鼠标停在数据点上可查看次数和比例。</p>')
    parts.append('<p class="muted">每个点对应一个实际评估的预算；连线只帮助看趋势，不代表测量了两个点之间的每一次猜测。'
                 '横轴按 10 倍间隔排列，纵轴直接显示百分比。多模型命中同一用户只计算一次（Min_auto），预算按每模型分别计数。</p>')
    series = [('动态策略 · 冻结攻击 F', _risk_curve(attacks['F'])),
              ('动态策略 · 仅知道规则 A0', _risk_curve(attacks['A0'])),
              ('动态策略 · 自适应攻击 A1', _risk_curve(attacks['A1'])),
              ('无策略 · 冻结攻击 F', _risk_curve(attacks['baseline_F'])),
              ('同一批用户的原口令 · 冻结攻击 F', _risk_curve(attacks['baseline_completed_F']))]
    for name, ev in attacks['controls'].items():
        series.append((CONTROL_LABELS.get(name, name) + ' A1', _risk_curve(ev)))
    parts.append(plot(series, xlabel='每个模型的累计猜测次数',
                      ylabel='累计猜中的用户比例', log=True, distinguish=True,
                      x_ticks=result['config']['budgets'], x_format='count', y_format='percent'))
    parts.append('<p class="muted">F：沿用原始开发数据训练的攻击流。A0：知道注册规则，并过滤不合规则的猜测。'
                 'A1：根据策略实施后的模拟开发口令重新训练。未完成预算的点不连线，其上下界在下表显示，缺失不代表 0%。</p>')
    if attacks['controls']:
        modified = sum(row['response']['modified_users'] for row in rows)
        attempted = result['dataset']['registration_occurrences']
        points = [('动态历史反馈', [(modified / attempted,
                                  attacks['A1']['minauto'][-1]['rate'],
                                  '动态历史反馈')])]
        for name, control in result['controls'].items():
            if name in attacks['controls']:
                label = CONTROL_LABELS.get(name, name)
                points.append((label, [(control['modified_users'] / attempted,
                                        attacks['controls'][name]['minauto'][-1]['rate'], label)]))
        parts.append('<h3>用户修改率与自适应攻击风险</h3>')
        parts.append(plot(points, xlabel='原始用户中的修改率',
                          ylabel=f'每模型 {budget:,} 次猜测下的累计命中比例（A1）', scatter=True,
                          distinguish=True, x_format='percent', y_format='percent'))
    parts.append(table(['每模型累计猜测次数', '动态 F', '动态 A0', '动态 A1', '相同完成用户原口令 F', '无策略 F'], [
        [f'{k:,}', _point(attacks['F']['minauto'][i]),
         _point(attacks['A0']['minauto'][i]),
         _point(attacks['A1']['minauto'][i]),
         _point(attacks['baseline_completed_F']['minauto'][i]),
         _point(attacks['baseline_F']['minauto'][i])]
        for i, k in enumerate(result['config']['budgets'])]))
    parts.append('<h3>达到指定命中率所需猜测数</h3>')
    difficulty = attacks['guess_counts']
    parts.append(table(['目标命中率', '相同完成用户原口令 F', '动态 F', '动态 A1'], [
        [_percent(float(fraction))] + [
            (str(difficulty[key]['quantiles'][fraction]['guess_count'])
             if difficulty[key]['quantiles'][fraction]['status'] == 'exact'
             else difficulty[key]['quantiles'][fraction]['status'])
            for key in ('baseline_completed_F', 'dynamic_F', 'dynamic_A1')]
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
            for point in points if point['budget'] == budget]))
        parts.append('<p class="muted">正值表示动态策略在同一批完成用户上更容易被猜中。区间条件于已固定的攻击流与策略，不包含重新训练和重新选择的不确定性。</p>')
    parts.append('<details><summary>攻击器完成状态与计算量</summary>')
    for name in ('F', 'A1'):
        parts.append(f'<h3>{escape(name)}</h3>')
        parts.append(table(['模型', '有效猜测', '原始输出', '停止原因'], [
            [r['run']['model'], r['run']['charged_count'],
             r['run']['raw_generated_count'], r['run']['stop_reason']]
            for r in attacks[name]['models']]))
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
