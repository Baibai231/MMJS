"""Cost eligibility, collision trajectories, prefix attacks and decision evidence."""
from web.presentation import escape, plot, table
from web.registration_distribution import strategy_label
from web.dynamic_comparison import PRESET_LABEL, displayed_controls


def pct(value):
    return '—' if value is None else f'{100 * value:.2f}%'


def render_comparison_conditions(result, labels):
    comparison = result.get('comparison')
    if not comparison:
        return '<p class="notice">此报告生成于共同首批与统一成本核查之前。新版公平对照需要重新运行。</p>'
    limits = comparison['cost_limits']
    parts = ['<div class="comparison-conditions"><h3>共同起点与成本条件</h3>',
             '<p>固定预设策略在全部批次要求长度至少 8，不参与后期调整；动态策略第一批使用完全相同的规则，'
             '从第二批起根据已完成批次的分布调整。无策略保留原口令。各方案使用相同原始用户、注册顺序及口令修改算法。</p>',
             f"<p>每批修改率上限 {pct(limits['max_modification_rate'])}；"
             f"相对继续使用上一条规则，新增修改率最多 {100 * limits['max_incremental_modification']:.1f} 个百分点；"
             f"相对首批最多增加 {100 * limits['max_late_cost_increase']:.1f} 个百分点。"
             '这些是共同的成本上限，不表示各方案实际成本相等。预览或实测超限的固定方案只作参考，不自动改弱规则。</p>']
    if result['config']['controls']['preset'] == 'top15':
        parts[0] = '<div class="comparison-conditions"><h3>网站策略与成本条件</h3>'
        parts[1] = '<p>动态策略从 Google 的已知长度规则起步，后续在资料可编码的网站策略中选择；各固定网站策略全程不变。所有方案使用相同原始用户、顺序与响应算法，首批规则可能不同。</p>'
    audits = [('动态策略', comparison['dynamic_cost_audit'])] + [
        (labels.get(name, PRESET_LABEL), arm['cost_audit']) for name, arm in displayed_controls(result).items()]
    rows = []
    for name, audit in audits:
        bad = sorted({row['cohort_id'] for row in audit['observed'] if not row['within_cost_limits']})
        predicted_bad = sorted({row['cohort_id'] for row in audit['development_preview'] if not row['within_cost_limits']})
        rows.append([name, '可比' if audit['eligible'] else '成本超限，仅参考',
                     pct(max(r['modification_rate'] for r in audit['observed'])),
                     '、'.join(map(str, predicted_bad)) or '无', '、'.join(map(str, bad)) or '无'])
    parts.append(table(['方案', '成本条件', '实测最高单批修改率', '预览超限批次', '实测超限批次'], rows))
    parts.append('</div>')
    return ''.join(parts)


def collision_series(result, labels):
    rows = result['cohorts']
    series = [('无策略', [(r['end_user'], r['baseline_cumulative']['collision_probability']) for r in rows]),
              (strategy_label(result, 'dynamic', labels),
               [(r['end_user'], r['cumulative']['collision_probability']) for r in rows])]
    for name, arm in displayed_controls(result).items():
        series.append((strategy_label(result, name, labels), [
            (r['end_user'], c['cumulative']['collision_probability'])
            for r, c in zip(rows, arm['timeline'])]))
    return series


def render_collision(result, labels):
    series = collision_series(result, labels)
    positive = all(y is not None and y > 0 for _, points in series for _, y in points)
    return ('<div class="collision-timeline"><h3>碰撞概率随注册人数的变化</h3>'
            '<p>从截至当前的用户中随机选出两名不同用户，口令相同的概率。'
            '数值越低，口令重复越少；这是 Simpson 集中度的样本统计，不能代替攻击评价。'
            + ('纵轴使用对数刻度，便于同时观察高低概率。' if positive else '') + '</p>'
            + plot(series, xlabel='累计注册人数 N', ylabel='两名用户使用同一口令的概率',
                   log_y=positive, x_format='count', y_format='probability', distinguish=True,
                   x_ticks=[row['end_user'] for row in result['cohorts']])
            + '</div>')


def stage_attack_series(result, labels, budget):
    stages = result['attacks']['registration_stages_F']['stages']
    names = ['baseline', 'dynamic', *displayed_controls(result)]
    series = []
    for name in names:
        points = []
        for stage in stages:
            point = next(p for p in stage['evaluations'][name]['minauto'] if p['budget'] == budget)
            points.append((stage['users'], point['rate']))
        series.append(('无策略' if name == 'baseline' else strategy_label(result, name, labels), points))
    return series


def render_stage_attacks(result, labels):
    if 'registration_stages_F' not in result['attacks']:
        return '<p class="notice">旧报告没有逐阶段各方案的攻击统计，需要重新运行。</p>'
    budgets = result['config']['budgets']
    parts = ['<div class="registration-stage-attacks"><h3>各注册阶段的攻击效果（冻结攻击 F）</h3>',
             '<p>横轴为累计注册人数，纵轴为截至该阶段被猜中的用户比例。选择猜测预算后，'
             '比较不同策略在同一批用户上的命中率。全部阶段和方案共用注册前开发数据训练的攻击流；'
             '不使用未来注册用户或未来策略训练，也不在各阶段重新训练自适应攻击器。</p>'
             '<p class="muted">预算按每模型不同有效猜测计数；多个模型命中同一用户只计一次。'
             '攻击未完成时不将未知命中率画成零。所有方案均以本阶段实际具有最终口令的用户数为攻击分母；'
             '若存在待处理用户，须先核对人数再比较。</p>',
             '<style>.registration-stage-attacks > .stage-budget{display:none}'
             '.attack-budget-options{display:flex;flex-wrap:wrap}'
             '.attack-budget-options label{flex-direction:row;align-items:center}</style>',
             '<div class="attack-budget-options" role="radiogroup" aria-label="选择阶段攻击预算">']
    for budget in budgets:
        checked = ' checked' if budget == budgets[-1] else ''
        parts.append(f'<label><input type="radio" name="stage-attack-budget" value="{budget}"{checked}>'
                     f'{budget:,} 次/模型</label>')
    parts.append('</div>')
    for budget in budgets:
        parts.append(f'<style>.registration-stage-attacks:has(> .attack-budget-options input[value="{budget}"]:checked)'
                     f' > .stage-budget-{budget}{{display:block}}</style>')
        parts.append(f'<div class="stage-budget stage-budget-{budget}" data-budget="{budget}">')
        parts.append(plot(stage_attack_series(result, labels, budget), xlabel='累计注册人数 N',
                          ylabel='累计猜中的用户比例', x_format='count', y_format='percent',
                          distinguish=True, y_domain=(0, 1),
                          x_ticks=[row['end_user'] for row in result['cohorts']]))
        parts.append('<details><summary>查看阶段人数、命中率与计算界限</summary>')
        data = []
        for stage in result['attacks']['registration_stages_F']['stages']:
            for name in ('baseline', 'dynamic', *displayed_controls(result)):
                ev = stage['evaluations'][name]
                p = next(p for p in ev['minauto'] if p['budget'] == budget)
                label = '无策略' if name == 'baseline' else strategy_label(result, name, labels)
                rate = pct(p['rate']) if p['rate'] is not None else f"未完成 [{pct(p['lower_bound'])}, {pct(p['upper_bound'])}]"
                data.append([stage['users'], label, p['target_weight'], rate])
        parts.append(table(['累计注册人数', '策略', '攻击分母', '累计命中率'], data))
        parts.append('</details></div>')
    parts.append('</div>')
    return ''.join(parts)


def render_decision_evidence(result):
    if result['config']['controller'].get('sequence') is not None:
        return ('<p>本次按配置中预先指定的逐批网站规则序列执行；此处没有在线选择。'
                '策略在评价前固定，攻击结果没有用于本次序列选择。</p>')
    parts = []
    c = result['config']['controller']
    parts.append('<p>hold 表示沿用当前策略，updated 表示选中新增限制。所有候选用相同开发预览、'
                 '相同用户编号和随机种子模拟修改；历史口令保持不变。</p>')
    parts.append(f"<p>先检查修改成本、合规生成和开发热点风险代理。新增规则的预测累计碰撞概率须比 hold "
                 f"至少下降 {100 * c['min_relative_collision_gain']:.2f}%。"
                 '合格候选按预测碰撞概率、热点风险代理、修改率依次比较；完全相同时优先保持。'
                 '没有可行更新就 hold；若保持本身也不满足条件，则标记 no_feasible_update。</p>')
    for d in result['decisions']:
        parts.append(f'<details class="decision-evidence"><summary>第 {d["after_cohort"]} 批之后：'
                     f'{escape(d["action"])} / {escape(d["status"])}</summary>')
        if 'predicted_batch_users' in d:
            parts.append(f'<p>开发预览 {d["preview_users"]:,} 人，用于预测下一批 {d["predicted_batch_users"]:,} 人；'
                         f'此前已有 {d["history_users"]:,} 人。历史内部、历史与新批次、新批次内部的同口令对分别计算。</p>')
        parts.append(table(['候选动作', '预测同口令数/百万对', '较 hold 的相对下降',
                            '预测修改率', '开发热点风险代理', '判断', '不通过原因'], [
            [row['action'], f"{row['collision'] * 1_000_000:.4f}" if row['collision'] is not None else '—',
             pct(row['relative_collision_gain']), pct(row['modification_rate']), pct(row['head_risk_proxy']),
             '选中' if row['action'] == d['action'] else '可行' if row['feasible'] else '不通过',
             '；'.join(row.get('rejection_reasons', [])) or ('—' if row['feasible'] else '旧报告未保存细分原因')]
            for row in d['candidates']]))
        parts.append('</details>')
    return ''.join(parts)
