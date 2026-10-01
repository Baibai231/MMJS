"""Presentation of saved study evidence; never runs experiments."""
from web.presentation import escape
from policy.site_catalog import site_labels
from web.sequence_rankings import load_exhaustive_status


DASHBOARD_STYLE = """
:root{--ink:#17372f;--accent:#19765e;--line:#dce7e1}
html{scroll-behavior:smooth;scroll-padding-top:28px}
body{background:#f3f5f1;color:var(--ink)}
main{max-width:1280px}a{color:var(--accent);text-underline-offset:4px}
.workbench-header{padding-bottom:0}.brand{font-size:13px;letter-spacing:2px;font-weight:750;color:var(--accent)}
.workbench-header h1{font-size:clamp(28px,4vw,42px);letter-spacing:-1px;margin:15px 0}
.workbench-header>p{max-width:850px;line-height:1.8}
.study-nav{display:flex;gap:8px;flex-wrap:wrap;margin:24px 0 12px}
.study-nav a{padding:10px 16px;background:#fff;border:1px solid var(--line);border-radius:30px;text-decoration:none;font-size:14px}
.study-nav a:hover{background:#e3eee7}
section{border-color:var(--line);border-radius:18px;box-shadow:0 3px 14px #193a2d04}
.dynamic-report{padding-top:0}.study-overview{background:linear-gradient(120deg,#153e33,#245e4c);color:#fff;padding:30px}
.study-overview .eyebrow{color:#bfdfc8;font-size:12px;letter-spacing:1px}.study-overview h2{font-size:28px;margin:12px 0}
.study-overview p{color:#d5e6dc;line-height:1.8}.study-overview .metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:24px 0}
.study-overview .metric{background:#ffffff10;border:1px solid #ffffff20;border-radius:12px;padding:18px;font-size:12px;color:#d5e6dc}
.study-overview .metric strong{color:#fff;font-size:30px;font-variant-numeric:tabular-nums}.study-overview .metric small{display:block;margin-top:8px;line-height:1.5}
.cohort-timeline{display:grid;grid-template-columns:repeat(10,minmax(0,1fr));gap:6px;margin:16px 0}
.cohort-timeline div{background:#ffffff12;border-radius:8px;padding:12px 5px;text-align:center;font-size:11px}
.cohort-timeline strong{display:block;font-size:13px;margin-top:8px}.study-overview .decision{border-top:1px solid #ffffff30;padding-top:16px}
#sequence-rankings>h2{font-size:26px}#sequence-rankings svg{max-height:none;min-width:1000px}
.ranking-chart{overflow-x:auto;margin:18px 0 30px;padding:16px;border:1px solid var(--line);border-radius:14px;background:#fcfdfb}
.ranking-chart h3{margin:4px 0 12px}.ranking-chart .actions{margin-bottom:0}
.settings-card>details>summary{font-size:20px;font-weight:650}.settings-card>details>summary small{font-size:13px;font-weight:400;color:#60756b;margin-left:12px}
.workbench-tools{padding-top:0}.query-panel p{line-height:1.7}
.notice{background:#f4f2e7;color:#5d5940;line-height:1.7}section p{line-height:1.75}
#status{display:block;flex-basis:100%;padding-top:6px}button:disabled{opacity:.55;cursor:default}
@media(max-width:760px){main{padding:16px}.study-overview{padding:20px}.study-overview .metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.cohort-timeline{grid-template-columns:repeat(5,minmax(0,1fr))}.study-overview .metric strong{font-size:25px}.study-nav a{padding:9px 12px}}
"""


def render_study_overview(result):
    users = result['dataset']['registration_occurrences']
    cohorts = result['cohorts']
    points = result['attacks']['by_strategy']['dynamic']['A1']['minauto']
    point = next((p for p in points if p['budget'] == 10**6), None)
    modified = sum(c['response']['modified_users'] for c in cohorts)
    names = site_labels()
    policies = [c['policy']['name'] for c in cohorts]
    sequence = result['config']['controller'].get('sequence')
    exact = load_exhaustive_status(result)
    selected = (exact is not None and exact.get('status') == 'complete'
                and policies == exact['best_evaluated']['path'])
    title = '全空间选路后的注册评价' if selected else '当前注册方案的评价'
    parts = ['<section id="study-overview" class="study-overview">',
             '<span class="eyebrow">PCFG / MONTE CARLO / TOP15</span>',
             f'<h2>{title}</h2>',
             f'<p>{users:,} 名用户依次注册，共 {len(cohorts)} 批。'
             '每批单独应用一条网站规则，老用户保留注册时的口令。</p>',
             '<div class="metrics">']
    metrics = [('注册用户', f'{users:,}', f'{len(cohorts)} 批 · 种子 {result["config"]["seed"]}'),
               ('A1 累计猜中比例', f'{point["rate"]:.3%}' if point else '未评价', '10⁶ 次猜测 · 蒙特卡洛估计'),
               ('用户修改比例', f'{modified / users:.3%}', f'{modified:,} 名用户修改原口令'),
               ('选路覆盖', f'{exact["covered_rule_paths"]:,}' if selected else '—',
                '可行规则路径 · 开发集评价' if selected else '当前报告无匹配的全空间结论')]
    for label, value, note in metrics:
        parts.append(f'<div class="metric">{escape(label)}<strong>{escape(value)}</strong><small>{escape(note)}</small></div>')
    parts.append('</div><h3>十批规则时间线</h3><div class="cohort-timeline">')
    for c in cohorts:
        label = names.get(c['policy']['name'], c['policy']['name']).split('（')[0].strip()
        parts.append(f'<div title="{c["start_user"]:,}–{c["end_user"]:,} 位用户">第 {c["cohort_id"]} 批'
                     f'<strong>{escape(label)}</strong></div>')
    parts.append('</div><div class="decision">')
    if selected:
        parts.append(f'<p>15¹⁰ = 576,650,390,625 条形式路径已分类核算；'
                     f'可执行且满足修改成本上限的 {exact["covered_rule_paths"]:,} 条不同规则路径，'
                     f'合并为 {exact["evaluated_groups"]:,} 个严格等效组完成 A1 评价。'
                     '缺少规则或成本不合格的路径没有计作已运行 A1。</p>')
        parts.append('<p><strong>为什么选它：</strong>先要求每批修改率不超过 60%，'
                     '再比较开发集上 PCFG A1 在 10⁶ 次猜测下的命中率，同分时选择碰撞概率更低的路径。'
                     '下方三张图分别列出三个指标最好的十条代表路径。</p>')
    else:
        parts.append('<p>' + ('本次按事先指定的完整路径执行。' if sequence else
                             '本次控制器使用之前批次的分布决定下一批规则。') + '</p>')
    if len(set(policies)) == 1:
        parts.append('<p>这条路径全程没有切换规则；它与对应固定策略的规则相同，'
                     '本次结果不能证明动态切换带来收益。</p>')
    if point:
        parts.append(f'<p>模型未覆盖 {point.get("outside_model_support_weight", 0):,} 名用户，'
                     f'仍保留在 {users:,} 人的分母中。最低模型命中率不等同于现实攻击下最安全。</p>')
    parts.append('</div></section>')
    return ''.join(parts)
