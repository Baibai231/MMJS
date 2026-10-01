"""PCFG estimate plots and the complete fifteen-site evidence inventory."""
from web.presentation import plot, table, escape
from web.dynamic_comparison import attack_entries, attack_groups, STRATEGY_LABELS
from web.policy_rankings import render_policy_rankings


def render_mc_attacks(result):
    attacks = result['attacks']['by_strategy']
    budgets = result['config']['budgets']
    people = result['dataset']['registration_occurrences']
    sample_count = result['config']['monte_carlo']['samples']
    parts = ['<section id="attack-success"><h2>PCFG 攻击曲线 · 蒙特卡洛估计</h2>',
        f'<p><strong>{people:,} 名用户 · {sample_count:,} 次预采样 · {len(result["controls"])} 条固定网站规则</strong></p>',
        '<p>横轴为累计攻击次数，按 10 的次方排列；纵轴为累计猜出口令的用户比例，分母始终为该方案的全部原始用户。重复口令按使用人数计权，未覆盖和待处理用户也保留在分母中。</p>',
        '<p>F：原始训练集模型。A0：同一模型，仅统计符合该用户注册规则的猜测。A1：用策略修改后的开发训练集重新训练。全部使用 PCFG，不再合并其他攻击器。</p>',
        '<p class="notice">曲线是前置采样得到的估计，不是逐条枚举的实测结果。抽样误差较大或前驱样本不足时，单条查询会显示标记；模型概率为零表示未覆盖，不能解释为安全。</p>']
    selected = [k for k in (10**3, 10**6, 10**12) if k in budgets]
    if not selected:
        selected = [budgets[-1]]
    rows = []
    for name, levels in attacks.items():
        a1 = {p['budget']: p for p in levels['A1']['minauto']}
        control = result['controls'].get(name)
        modified = (sum(row['response']['modified_users'] for row in result['cohorts'])
                    if name == 'dynamic' else control['modified_users'] if control else 0)
        rows.append([STRATEGY_LABELS.get(name, name),
                     f'{100 * modified / people:.2f}%',
                     *[f'{100 * a1[k]["rate"]:.2f}%' for k in selected],
                     a1[budgets[-1]]['outside_model_support_weight']])
    parts.append('<h3>策略对照 · A1</h3>')
    parts.append(table(['策略', '修改用户比例', *[f'估计命中 @ 10^{len(str(k))-1}' for k in selected],
                        '模型未覆盖人数'], rows))
    policy_names = list(dict.fromkeys(row['policy']['name'] for row in result['cohorts']))
    if len(policy_names) == 1 and result['config']['controller'].get('sequence') is None:
        parts.append(f'<p class="notice">本次动态控制器在 {len(result["cohorts"])} 批中始终沿用 '
                     f'{escape(policy_names[0])}，没有选中后续策略更新。动态曲线与对应固定策略重合；'
                     '这次结果不能证明动态调整优于固定策略。</p>')
    # One panel per knowledge level keeps the website comparisons readable.
    for level in ('F', 'A0', 'A1'):
        series = [(name, [(p['budget'], p['rate']) for p in levels[level]['minauto']])
                  for name, levels in attack_groups(result) if level in levels]
        parts.append('<h3>' + level + '</h3>')
        ceiling = max((y for _, points in series for _, y in points if y is not None), default=0)
        y_top = min(1, max(.01, ceiling * 1.12))
        parts.append(plot(series, xlabel='累计攻击次数（估计猜测预算）',
                          ylabel='累计猜出的口令比例（全部用户）', log=True,
                          distinguish=True, x_format='power10', y_format='percent', y_domain=(0, y_top)))
    parts.append('<p class="actions">下载图像：'
                 '<a class="figure-link" href="attack_F.svg">F</a> · '
                 '<a class="figure-link" href="attack_A0.svg">A0</a> · '
                 '<a class="figure-link" href="attack_A1.svg">A1</a> · '
                 '<a class="figure-link" href="final_rank_distribution.svg">口令分布</a> · '
                 '<a class="figure-link" href="cumulative_collision.svg">分布变化</a>'
                 '</p>')
    parts.append('<details><summary>各预算估计值、样本不足与模型覆盖</summary>')
    parts.append(table(['策略 / 攻击', '攻击次数', '累计比例（估计）', '总用户数', '排名采样不足人数', '模型未覆盖人数'],
        [[name, p['budget'], f"{100*p['rate']:.2f}%", p['target_weight'],
          p.get('low_sample_support_weight', 0), p.get('outside_model_support_weight', 0)]
         for name, ev in attack_entries(result) for p in ev['minauto']]))
    parts.append('</details></section>')
    parts.append(render_policy_rankings(result))
    parts.append('<section id="site-catalog"><h2>Top15 网站资料与实验范围</h2>')
    labels = {'partial_hard': '已知强制规则子集', 'recommended_scenario': '建议场景（默认不参与）',
              'unknown': '规则不明，待补证', 'alias': '共享认证，不重复计算',
              'not_applicable': '不适用传统口令实验'}
    enabled = result['controls']
    hard_sites = [row['site'] for row in result['site_catalog']['sites']
                  if row['basis'] == 'partial_hard' and 'site_' + row['id'] in enabled]
    parts.append('<p>名单采用队友提供的文件。文件未提供榜单来源、原始规则链接和测量日期；本实验不宣称已完整复刻 15 个网站。'
                 + escape('、'.join(hard_sites)) + ' 的已知强制规则子集参与本次对照；不明规则没有补造。'
                 'YouTube 继承 Google。官方建议可在配置中作为单独标注的“建议采纳场景”启用。</p>')
    parts.append(table(['序号', '网站', '证据范围', '本次参与', '缺失与解释'],
        [[r['list_position'], r['site'], labels[r['basis']],
          '是' if 'site_'+r['id'] in enabled else '否', r['limitations']]
         for r in result['site_catalog']['sites']]))
    parts.append('</section><section><h2>逐条口令查询</h2><p>本次实验已保存原始 PCFG 与动态策略 A1 的采样索引。'
                 '在实验页的查询框输入口令，即可复用索引得到猜测次数估计、采样误差和该用户口令集中的实际热门排名。</p>'
                 '<p>热门排名按出现人数降序，同频口令并列；F/A0 对应初始用户集，A1 对应动态策略最终用户集。查询不参与训练。</p></section>')
    return ''.join(parts)
