"""Shared open-experiment presentation; only aggregate evidence is rendered."""
import json
from web.presentation import escape, table, plot, STYLE

LABELS={'frequency':'训练频次','dictionary-rules':'字典变形','character-ngram':'字符 n-gram','pcfg':'PCFG'}
STATUS={'test_constraints_not_met':'测试未达到所设需求','selected':'已按验证集选择','no_feasible_policy':'没有满足约束的策略',
        'no_supported_improvement':'未证实优于常见规则','supported_improvement':'本次配对比较支持改进',
        'incomplete_test_budget':'测试预算未完成','no_feasible_common_comparator':'同约束下无常见规则可比较'}
KNOWLEDGE={'F':'冻结','A0':'仅知策略','A1':'自适应'}
FEATURE_LABELS={'year_suffix':'年份后缀','keyboard_walk':'键盘相邻模式','repeated':'重复模式','sequential_digits':'连续数字','word_plus_digits':'单词加数字'}


def pct(x):return '—' if x is None else f'{100*x:.2f}%'
def mdcell(value):return str(value).replace('\\','\\\\').replace('|','\\|').replace('\n',' ').replace('\r',' ')
def num(x):return '—' if x is None else f'{x:.3f}'
def pvalue(p):
    if p['rate'] is not None:return pct(p['rate'])
    if p['lower_bound'] is None:return '无有效目标/训练'
    return f"未完成 [{pct(p['lower_bound'])}, {pct(p['upper_bound'])}]"

def curve(evaluation):
    # Never bridge an incomplete budget as if it were an observed point.
    return [(p['budget'],p['rate']) for p in evaluation['minauto']]

def selected_point(ev,k):return next(p for p in ev['minauto'] if p['budget']==k)

def render_open_html(result,document=True):
    d=result['dataset'];cfg=result['reproducibility']['config'];k=cfg['search']['risk_budget'];primary=cfg['response']['primary']
    m=result['metadata'];parts=['<main class="open-report">']
    parts.append('<section><span class="muted">OPEN CANDIDATES · MIN_AUTO</span><h1>策略与用户成本的取舍</h1><div class="metrics">')
    for label,value in [('数据源',d.get('dataset_id','local')),('抽样出现次数',f"{d['sample_occurrences']:,}"),
                        ('主要预算',f'{k:,} / 模型'),('响应情景',primary),('运行时间',str(m['runtime_seconds'])+' s')]:
        parts.append(f'<div class="metric">{escape(label)}<strong>{escape(value)}</strong></div>')
    parts.append('</div><p>真实初始口令 + 显式用户响应。各攻击器独立生成，逐口令取最早命中；公开结果不包含口令内容。</p></section>')
    parts.append('<section><h2>需求对应的推荐</h2><p class="muted">先在验证集选择，再报告测试表现。已有规则也可能是合适选择；未证实改进时如实保留该结论。</p>')
    rows=[]
    for rec in result['recommendations']:
        req=rec['requirement'];test=rec.get('test',{});response=test.get('response',{})
        rows.append([req['name'],'成本上限' if req['mode']=='cost_cap' else '风险要求',
                     pct(req['max_cost']),pct(req['max_risk']),rec.get('policy_name') or '—',
                     pct(rec.get('validation_risk')),pct(test.get('risk')),pct(response.get('cost')),
                     STATUS[rec['status']]])
    parts.append(table(['需求','选择方式','成本上限','风险上限','选中策略','验证风险','测试风险','测试成本','证据状态'],rows))
    selected_names={r['policy_name'] for r in result['recommendations'] if r['policy_name']}
    policies={r['policy']['name']:r['policy'] for r in result['validation_candidates']}
    if selected_names:
        rows=[]
        for name in sorted(selected_names):
            rule=policies[name];descriptions=[]
            if rule['min_length']:descriptions.append(f"至少 {rule['min_length']} 位")
            if rule['required_classes']:descriptions.append(f"至少 {rule['required_classes']} 类字符")
            if rule['deny_features']:descriptions.append('禁止'+ '、'.join(FEATURE_LABELS.get(x,x) for x in rule['deny_features']))
            if rule['blocklist_size']:descriptions.append(f"阻断训练集中前 {rule['blocklist_size']} 个高频口令")
            rows.append([name,'；'.join(descriptions) or '无额外限制'])
        parts.append(table(['选中策略','具体规则'],rows))
    parts.append('<details><summary>完成率和额外尝试约束</summary>'+table(['需求','最低完成率','每用户额外尝试上限'],
        [(r['name'],pct(r['min_completion']),num(r['max_extra_attempts'])) for r in cfg['search']['requirements']])+'</details>')
    for rec in result['recommendations']:
        if 'comparison' not in rec:continue
        rows=[]
        for key,label in [('risk','风险下降'),('cost','拒绝比例下降'),('completion','对照完成率 − 推荐完成率'),('attempts','额外尝试减少')]:
            diff=rec['comparison']['differences'][key];fmt=num if key=='attempts' else pct
            rows.append([label,fmt(diff['estimate']),f"[{fmt(diff['lower'])}, {fmt(diff['upper'])}]",diff['valid_repetitions']])
        parts.append('<details><summary>'+escape(rec['requirement']['name']+'：与 '+rec['comparator_name']+' 的配对比较')+'</summary>'+table(['指标','对照 − 推荐','95% 逐点区间','有效重采样次数'],rows)+'<p class="muted">固定攻击器和选择结果下的初始队列配对重采样；区间未作多重比较校正。</p></details>')
    parts.append('</section><section><h2>验证集风险—成本前沿</h2>')
    valid=[r for r in result['validation_candidates'] if r['scenario']==primary]
    groups=[]
    for origin,label in [('common','常见规则'),('discovered','分布引导规则')]:
        groups.append((label,[(r['cost'],r['risk'],r['policy']['name']) for r in valid if r['evaluable'] and r['policy']['origin']==origin]))
    groups.append(('Pareto 前沿',[(r['cost'],r['risk'],r['policy']['name']) for r in valid if r['policy']['name'] in result['pareto_front']]))
    parts.append(plot(groups,xlabel='初始拒绝比例（成本代理）',ylabel=f'自适应 Min_auto@{k}',scatter=True))
    incomplete=sum(not r['evaluable'] for r in valid)
    parts.append(f'<p>候选 {len(valid)} 项；未完成评价或响应不可行 {incomplete} 项。推荐还约束完成率、额外尝试及规则数量。</p>')
    parts.append('<details><summary>所有验证候选与约束量</summary>'+table(['策略','来源','风险','成本','完成率','额外尝试','可完整评价'],
        [(r['policy']['name'],r['policy']['origin'],pct(r['risk']),pct(r['cost']),pct(r['completion']),num(r['attempts']),'是' if r['evaluable'] else '否') for r in valid])+'</details></section>')
    parts.append('<section><h2>自适应风险与预算</h2>')
    finals=[r for r in result['test_policies'] if r['scenario']==primary]
    parts.append(plot([(r['policy']['name'],curve(r['evaluations']['A1'])) for r in finals],xlabel='每模型校验预算 K',ylabel='测试 Min_auto 命中率',log=True))
    parts.append('<p class="muted">不完整预算点不连线；K 不表示跨模型总猜测次数。未命中仅表示本次预算内未命中。</p>')
    for row in finals:
        parts.append('<details'+(' open' if row['policy']['name']=='baseline' else '')+'><summary>'+escape(row['policy']['name'])+' · 单模型与 Min_auto</summary>')
        ev=row['evaluations']['A1'];models=ev['models']
        rows=[[LABELS.get(r['run']['model'],r['run']['model'])]+[pvalue(p) for p in r['points']] for r in models]
        rows.append(['Min_auto']+[pvalue(p) for p in ev['minauto']])
        parts.append(table(['攻击器']+[f'@{b}' for b in cfg['budgets']],rows))
        parts.append(table(['模型','计费次数','原始输出','策略过滤','重复','停止原因','计算秒数'],
            [(LABELS.get(x['run']['model'],x['run']['model']),x['run']['charged_count'],x['run']['raw_generated_count'],
              x['run']['policy_filtered_count'],x['run']['duplicate_count'],x['run']['stop_reason'],
              num(x['run']['elapsed_seconds']+x['run']['preparation_seconds'])) for x in models]))
        parts.append('</details>')
    parts.append('</section><section><h2>用户响应与攻击者适应</h2>')
    rows=[]
    for row in result['test_policies']:
        e=row['evaluations'];s=e['A1']['response']
        rows.append([row['policy']['name'],row['scenario'],pct(s['initial_accept_rate']),pct(s['completion_rate']),
                     num(s['mean_extra_attempts']),num(s['mean_edit_distance'])]+[pvalue(selected_point(e[h],k)) for h in ('F','A0','A1')])
    parts.append(table(['策略','响应','初始接受','完成率','额外尝试/初始用户','编辑距离/初始用户','冻结风险','仅知策略风险','自适应风险'],rows))
    parts.append('<p class="muted">R0 为独立重新选择的解析条件分布；R1 为固定修补，R2 为修补、训练池重选和放弃的混合。失败用户保留在成本分母中，攻击风险按完成账户计算。</p></section>')
    parts.append('<section><h2>分布怎样参与规则发现</h2>')
    disc=result['discovery']
    parts.append('<p>训练集频次头部 → 加权特征区分度与方向 → 候选规则 → 自适应风险与成本检验。频次排名不是猜测排名。</p>')
    parts.append(table(['特征','训练覆盖','信息增益比','出现时头部比例','未出现时头部比例','候选依据'],
        [(FEATURE_LABELS.get(s['feature'],s['feature']),pct(s['support']),num(s['information_gain_ratio']),pct(s['head_rate_present']),pct(s['head_rate_absent']),
          '入选' if s['feature'] in disc['selected_features'] else '未选') for s in disc['features']]))
    a=result.get('analysis')
    if a:
        parts.append('<details><summary>训练频次分布拟合与残差</summary>')
        parts.append(table(['模型','内部留出 KS','BIC'],[(m['id'],num(m['validation_ks']),num(m['bic'])) for m in a['models']]))
        parts.append(plot([('训练经验 CDF',[(r['rank'],r['empirical_cdf']) for r in a['curves']])]+
                          [(m['id'],[(r['rank'],r[m['id']+'_cdf']) for r in a['curves']]) for m in a['models']],
                          xlabel='频次排名',ylabel='累计出现质量',log=True))
        parts.append(plot([(m['id'],[(r['rank'],r[m['id']+'_cdf']-r['empirical_cdf']) for r in a['curves']]) for m in a['models']],
                          xlabel='频次排名',ylabel='拟合残差',log=True))
        parts.append('</details>')
    parts.append('</section><section><h2>数据、预算与复现记录</h2>')
    parts.append(table(['属性','记录'],[('输入格式',d.get('source_format','fixture')),('源文件出现次数',d.get('source_occurrences','—')),
        ('排除出现质量',d.get('excluded_frequency','—')),('计数未知的排除行',d.get('unknown_frequency_rows',0)),
        ('抽样方式',d.get('sampling','fixture')),('抽样不同口令数',d['sample_unique']),('无效行',d.get('invalid_rows',0)),
        ('训练 / 调参 / 验证 / 测试',' / '.join(str(d['split_sizes'][s]) for s in ('train','tuning','validation','test'))),
        ('频次解释',d.get('frequency_interpretation','测试夹具')),('模型集合','、'.join(LABELS.get(x,x) for x in m['participating_attackers']))]))
    for failure in m['attacker_failures']:parts.append('<div class="notice">'+escape(failure['reason'])+'；已从整次比较排除。</div>')
    parts.append('<p>PassLLM / RFGuess 尚未接入本协议；A2 机制已知攻击留待单独扩展。</p>')
    for limitation in m['limitations']:parts.append('<p class="muted">'+escape(limitation)+'</p>')
    parts.append('<details><summary>配置、数据划分、模型与代码版本</summary><pre>'+escape(json.dumps(result['reproducibility'],ensure_ascii=False,indent=2))+'</pre></details></section></main>')
    body=''.join(parts)
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>'+STYLE+'</style><body>'+body+'</body></html>' if document else body


def render_open_markdown(result):
    c=result['reproducibility']['config'];d=result['dataset'];k=c['search']['risk_budget']
    lines=['# ZipfGuard 开放候选与 Min_auto 实验报告','',
           f"数据：{d.get('dataset_id','local')}；抽样出现次数：{d['sample_occurrences']}；协议：{result['protocol_id']}。",'',
           f'主要终点：{c["response"]["primary"]} 响应下，自适应 Min_auto@{k}。K 为每模型不同校验候选数。','',
           '| 需求 | 策略 | 测试风险 | 测试成本 | 状态 |','|---|---|---:|---:|---|']
    for r in result['recommendations']:
        t=r.get('test',{});lines.append(f"| {mdcell(r['requirement']['name'])} | {r.get('policy_name') or '—'} | {pct(t.get('risk'))} | {pct(t.get('response',{}).get('cost'))} | {STATUS[r['status']]} |")
    lines+=['','## 测试风险与响应','', '| 策略 | 情景 | Min_auto 风险 | 完成率 | 拒绝率 | 额外尝试 |','|---|---|---:|---:|---:|---:|']
    for r in result['test_policies']:
        ev=r['evaluations']['A1'];s=ev['response']
        lines.append(f"| {r['policy']['name']} | {r['scenario']} | {pvalue(selected_point(ev,k))} | {pct(s['completion_rate'])} | {pct(s['cost'])} | {num(s['mean_extra_attempts'])} |")
    lines+=['','## 解释边界','']+[f'- {x}' for x in result['metadata']['limitations']]
    lines+=['','- 完整候选预算状态、配对区间、模型参数和哈希见同名 JSON / HTML。',
            '- 不完整预算不能视为零风险；无可行策略时不自动放松约束。',
            '- 本报告的成本来自指定响应模型，不能直接解释为真实用户体验。','']
    return '\n'.join(lines)
