"""Single first-round results; reuse frozen measurements and recount all arms."""
from pathlib import Path
import argparse, csv, hashlib, json, statistics, sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from experiments.registration_joint_data import load_private
from experiments.registration_joint_pipeline import write_json
from experiments.registration_comparison import score_counts

NAMES={'joint':'原始联合方案','graded':'结构内限制新方案','schechter_strong':'Schechter 严格',
       'schechter_middle':'Schechter 中等','schechter_light':'Schechter 宽松',
       'malone':'Malone–Maher 概率接受','segreti_struct_m_nohint':'Segreti 结构黑名单（适配）',
       'github_core':'GitHub 公开核心规则','github_base':'GitHub＋共同黑名单','github_graded':'GitHub＋新动态方案'}
COLORS=['#879DAF','#27648C','#BA7635','#CFA56A','#E2C7A3','#8A709A','#698B72','#879DAF','#BA7635','#27648C']


def csv_write(path,rows):
    if not rows:return
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('root');parser.add_argument('--partial',action='store_true');args=parser.parse_args()
    root=Path(args.root).resolve()
    cfg=json.loads((root / 'manifest.json').read_text('utf-8'))['config']
    extension=json.loads((root / 'additions_manifest.json').read_text('utf-8'))['settings']
    scope=json.loads((root / 'single_scope.json').read_text('utf-8'))
    cfg={**cfg,'seeds':[scope['retained_seed']]}
    literature=cfg['arms']+extension['literature_arms'];website=extension['website_arms']
    allarms=literature+website
    dest=root / 'single_deliverables';dest.mkdir(exist_ok=True)
    rows=[];batch_rows=[];audit=[];missing=[]
    for seed in cfg['seeds']:
        for arm in allarms:
            path=root / 'runs' / str(seed) / arm
            file=path / 'result.json'
            if not file.exists():missing.append(f'{seed}/{arm}');continue
            result=json.loads(file.read_text('utf-8'));assert result['status']=='complete'
            assert not result['attack_failures']
            points=result['actual_attacks']['union_equal_total_B']['points']
            for point in points:
                rows.append({'experiment':'website' if arm in website else 'literature','arm':arm,'seed':seed,
                             'budget':point['budget'],'hits':point['hits'],'initial_users':cfg['users'],
                             'union_rate':point['rate_per_initial_user'],
                             'pcfg_rate':next(p['rate_per_initial_user'] for p in result['actual_attacks']['pcfg']['points'] if p['budget']==point['budget']),
                             'omen_rate':next(p['rate_per_initial_user'] for p in result['actual_attacks']['omen']['points'] if p['budget']==point['budget']),
                             'extra':result['cost']['mean_extra_submissions'],
                             'modification':result['cost']['modification_rate'],
                             'more_than_three':result['cost']['more_than_three_fraction'],
                             'incremental_extra':result['cost']['incremental_mean_extra_submissions'],
                             'incremental_modification':result['cost']['incremental_modification_rate'],
                             'pending':result['cost']['pending_users'],'eligible':not result['eligibility_failures'],
                             'whole_shape_batches':sum(t['shape_size']>0 for t in result['timeline']),
                             'soft_shape_batches':sum(t.get('soft_shape_size',0)>0 for t in result['timeline'])})
            if arm in allarms:
                checkpoint=path / ('joint/state.pkl.gz' if arm in ('joint','graded','github_graded') else 'state.pkl.gz')
                state=load_private(checkpoint);key=result['A1_training_sha256']
                prefixes={};prefix_files={}
                for m in ('pcfg','omen'):
                    p=root / 'private/attacks' / str(seed) / arm / 'prefixes' / f'{key}-{m}-{max(cfg["budgets"])}.pkl.gz'
                    if not p.exists():
                        old=ROOT / 'reports/registration_comparison_final_20261011/private/attacks' / ('website' if arm in website else 'literature') / str(seed) / 'prefixes'
                        choices=[p for p in old.glob(f'{key}-{m}-*.pkl.gz') if int(p.name.split('-')[-1].split('.')[0])>=max(cfg['budgets'])]
                        assert choices;p=sorted(choices)[0]
                    prefix_files[m]=p;prefixes[m]=load_private(p)
                    assert prefixes[m]['complete'] and prefixes[m]['raw_emissions']>=max(cfg['budgets'])
                recounted=score_counts(state['history'],prefixes,cfg['budgets'],cfg['users'])
                assert all(abs(r-p['rate_per_initial_user'])<1e-12 for r,p in zip(recounted,points))
                assert sum(state['history'].values())==result['cost']['completed_users']
                assert result['A1_training_occurrences']==60000
                from core.corpus import counts_hash
                training=state['dev_history'] if arm in ('joint','graded','github_graded') else load_private(path / 'developer_counts.pkl.gz')
                assert counts_hash(training)==key and sum(training.values())==60000
                audit.append({'seed':seed,'arm':arm,'union_recount_matches':True,
                              'result_sha256':hashlib.sha256(file.read_bytes()).hexdigest(),
                              'prefix_hashes':{m:hashlib.sha256(p.read_bytes()).hexdigest() for m,p in prefix_files.items()}})
                target_batches=state['final_batches'] if arm in ('joint','graded','github_graded') else state['batches']
                from collections import Counter
                cumulative=Counter()
                for t,counts in enumerate(target_batches):
                    cumulative.update(counts);cost=state['costs'][t];rule=state['timeline'][t]
                    risk=score_counts(cumulative,prefixes,[cfg['primary_observed_budget'],cfg['secondary_observed_budget']],(t+1)*(cfg['users']//cfg['batches']))
                    batch_rows.append({'seed':seed,'arm':arm,'batch':t+1,'retrospective_union_100k':risk[0],
                                       'retrospective_union_1m':risk[1],'extra':cost['mean_extra_submissions'],
                                       'modification':cost['modification_rate'],'exact_size':rule['exact_size'],
                                       'whole_shapes':rule['shape_size'],'soft_shapes':rule.get('soft_shape_size',0)})
    if missing and not args.partial:raise RuntimeError(f'{len(missing)} trajectories missing')
    means=[]
    for arm in allarms:
        for b in cfg['budgets']:
            rr=[r for r in rows if r['arm']==arm and r['budget']==b]
            if not rr:continue
            mean={'experiment':'website' if arm in website else 'literature','arm':arm,'budget':b,
                  'repeats':len(rr),'eligible_repeats':sum(r['eligible'] for r in rr),'pending_users':sum(r['pending'] for r in rr)}
            for metric in ('union_rate','pcfg_rate','omen_rate','extra','modification','incremental_extra','more_than_three'):
                vals=[r[metric] for r in rr]
                mean[metric+'_mean']=statistics.mean(vals);mean[metric+'_sd']=statistics.stdev(vals) if len(vals)>1 else 0
            means.append(mean)
    pairs=[]
    for experiment,new,comparators in (('literature','graded',[a for a in literature if a!='graded']),
                                        ('website','github_graded',['github_core','github_base'])):
        for old in comparators:
            for budget in (cfg['primary_observed_budget'],cfg['secondary_observed_budget']):
                for seed in cfg['seeds']:
                    nr=[r for r in rows if r['arm']==new and r['seed']==seed and r['budget']==budget]
                    cr=[r for r in rows if r['arm']==old and r['seed']==seed and r['budget']==budget]
                    if nr and cr:
                        n,c=nr[0],cr[0]
                        pairs.append({'experiment':experiment,'new':new,'comparator':old,'seed':seed,'budget':budget,
                                      'risk_reduction_pp':100*(c['union_rate']-n['union_rate']),
                                      'extra_increase':n['extra']-c['extra'],
                                      'new_eligible':n['eligible'],'comparator_eligible':c['eligible']})
    csv_write(dest / 'per_seed_results.csv',rows);csv_write(dest / 'summary_results.csv',means)
    csv_write(dest / 'paired_comparisons.csv',pairs);csv_write(dest / 'batch_records.csv',batch_rows)
    write_json(root / 'single_audit.json',{'status':'partial' if missing else 'passed',
                                           'expected_trajectories':len(allarms),'available_trajectories':len(rows)//len(cfg['budgets']),
                                           'missing':missing,'additional_recounts':audit})
    text=['# 分批注册策略：第一轮实验结果','',
          f"状态：{'部分结果，尚未全部完成' if missing else '全部10组完成'}。每种方案10万模拟用户，分10批注册，只保留最初指定的第1轮（seed={scope['retained_seed']}）。用户要求停止其余轮次；本次只减少重复次数，不改变策略参数、样本和攻击预算，也不根据效果挑选种子。",'',
          '## 数据与指标','',
          '所有方法使用相同目标出现记录、训练数据及模拟重选过程。主指标为PCFG与OMEN合计10万次实际猜测下的猜中比例，合计100万次为补充指标；两种攻击器各分配一半联合预算，重复输出也计入次数。目标数据未参与攻击器训练。','',
          '文献对照采用共同的至少8位、训练前1000常见口令黑名单及绝对成本上限；网站对照采用GitHub公开长度与字符规则，动态增量成本相对于GitHub＋共同黑名单计算。相同成本上限不代表实际成本相同。','',
          '## 文献复现与网站策略范围','',
          '- Schechter：精确计数的流行度接受机制，保留三个事先指定的阈值，不复现隐私计数结构。',
          '- Malone–Maher：按已见口令频次进行概率接受，参考字符串在用户重试过程中固定，记录所有提交。',
          '- Segreti：在统一基础策略和开发数据下适配StructMNoHint机制，用10万个符合基础要求的PCFG不同字符串构造固定结构黑名单。实际结构数来自本轮模型；不宣称复现原论文完整真人实验。',
          '- GitHub：至少15位，或至少8位且包含小写字母与数字。公开字符规则、该规则加共同黑名单、再加动态方案分别列出；未复现GitHub完整弱口令检查服务。','']
    for title,arms in (('文献与原方案对照',literature),('网站策略对照',website)):
        text+=['## '+title,'',
               '| 方案 | 联合10万次猜中比例 | 联合100万次猜中比例 | 人均额外提交 | 相对基础的增量提交 | 成本是否合格 |',
               '|---|---:|---:|---:|---:|---|']
        for arm in arms:
            p=next((r for r in means if r['arm']==arm and r['budget']==cfg['primary_observed_budget']),None)
            s=next((r for r in means if r['arm']==arm and r['budget']==cfg['secondary_observed_budget']),None)
            if not p:
                text.append(f'| {NAMES[arm]} | 待完成 | 待完成 | 待完成 | 待完成 | 待完成 |');continue
            eligible='是' if p['eligible_repeats']==1 else '否'
            text.append(f"| {NAMES[arm]} | {100*p['union_rate_mean']:.3f}% | {100*s['union_rate_mean']:.3f}% | {p['extra_mean']:.4f} | {p['incremental_extra_mean']:.4f} | {eligible} |")
        text+=['']
    text+=['## 本轮直接比较','',
           '| 改进方案 | 对照 | 联合预算 | 猜中比例下降（百分点） | 人均额外提交增加 |',
           '|---|---|---:|---:|---:|']
    for p in pairs:
        text.append(f"| {NAMES[p['new']]} | {NAMES[p['comparator']]} | {p['budget']} | {p['risk_reduction_pp']:.3f} | {p['extra_increase']:.4f} |")
    text+=['','正的风险下降表示改进方案在本轮猜中比例更低；负值表示更高。成本不合格的结果仍保留，不能作为满足成本要求的胜出证据。','',
           '## 结果说明','',
           '本轮只有一次模拟，图表不给出跨轮次误差条，不声称五次稳定获胜或统计显著。已完成的其他轮次保留在原目录，未混入本次表格或用于选择结果。','',
           '改进方案同时增加风险排序和结构内限制，不能把全部收益只归因于其中一项。逐批风险由最终攻击器回顾计算；模拟额外提交次数不能替代真人的记忆、耗时或满意度。','',
           '## 文献来源','',
           '- Malone–Maher：https://www.maths.tcd.ie/~dwmalone/p/www2012.pdf',
           '- Segreti：https://www.usenix.org/system/files/conference/soups2017/soups2017-segreti.pdf',
           '- GitHub：https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-strong-password','']
    (dest / '第一轮实验结果.md').write_text('\n'.join(text),'utf-8')
    if not means:return
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    for family in ('Microsoft YaHei','SimHei','Noto Sans CJK SC'):
        if any(f.name==family for f in font_manager.fontManager.ttflist):plt.rcParams['font.sans-serif']=[family];break
    plt.rcParams['axes.unicode_minus']=False
    colors={a:COLORS[i] for i,a in enumerate(allarms)}
    for groupname,arms in (('文献对照',literature),('网站对照',website)):
        fig,axes=plt.subplots(1,2,figsize=(13,5.2))
        for arm in arms:
            mm=[r for r in means if r['arm']==arm]
            if not mm:continue
            axes[0].plot([r['budget'] for r in mm],[100*r['union_rate_mean'] for r in mm],label=NAMES[arm],color=colors[arm],marker='o')
            p=next(r for r in mm if r['budget']==cfg['primary_observed_budget'])
            axes[1].plot(p['extra_mean'],100*p['union_rate_mean'],label=NAMES[arm],color=colors[arm],marker='o',linestyle='none')
        axes[0].set(xscale='log',xlabel='两种攻击器总计实际猜测次数',ylabel='联合猜中比例（%）',title='实际攻击预算曲线')
        axes[1].set(xlabel='人均额外提交次数',ylabel='联合10万次猜中比例（%）',title='实际成本与风险')
        for ax in axes:ax.legend(fontsize=8)
        fig.suptitle(groupname+('（部分结果）' if missing else '：10万用户 × 1次'))
        fig.tight_layout();fig.savefig(dest / (groupname+'.png'),dpi=180);fig.savefig(dest / (groupname+'.pdf'));plt.close(fig)
    print(f'Single-round outputs: {len(rows)//len(cfg["budgets"])}/{len(allarms)} trajectories; missing {len(missing)}',flush=True)


if __name__=='__main__':main()
