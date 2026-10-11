"""Tables and figures from completed full trajectories; no extra experiments."""
from pathlib import Path
from collections import defaultdict
import csv, hashlib, json, statistics, sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from experiments.registration_joint_data import load_private
from experiments.registration_comparison import score_counts
from experiments.registration_joint_pipeline import write_json
from core.corpus import counts_hash

NAMES={'joint':'原始联合方案','graded':'结构内限制新方案',
       'schechter_strong':'Schechter 严格','schechter_middle':'Schechter 中等',
       'schechter_light':'Schechter 宽松'}
COLORS={'joint':'#8FA6BC','graded':'#356A93','schechter_strong':'#C98542',
        'schechter_middle':'#D6A66B','schechter_light':'#E5C8A2'}


def csv_write(path,rows):
    if not rows:return
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


def main():
    root=Path(sys.argv[1]).resolve()
    cfg=json.loads((root / 'manifest.json').read_text('utf-8'))['config']
    dest=root / 'deliverables';dest.mkdir(exist_ok=True)
    rows=[];batches=[];audits=[];results={}
    for seed in cfg['seeds']:
        for arm in cfg['arms']:
            path=root / 'runs' / str(seed) / arm
            result=json.loads((path / 'result.json').read_text('utf-8'))
            assert result['status']=='complete' and not result.get('attack_failures')
            assert result['cost']['completed_users']==cfg['users'] and not result['cost']['pending_users']
            results[seed,arm]=result
            state=load_private(path / ('joint/state.pkl.gz' if arm in ('joint','graded') else 'state.pkl.gz'))
            training=state['dev_history'] if arm in ('joint','graded') else load_private(path / 'developer_counts.pkl.gz')
            key=counts_hash(training)
            assert key==result['A1_training_sha256'] and sum(training.values())==60000
            prefix_paths={m:root / 'private/attacks' / str(seed) / arm / 'prefixes' / f'{key}-{m}-{max(cfg["budgets"])}.pkl.gz'
                          for m in ('pcfg','omen')}
            # A matching previous prefix may have been read-only reused. Resolve
            # it from metadata, never substitute an unrelated attack model.
            prefixes={}
            for m,p in prefix_paths.items():
                if p.exists(): prefixes[m]=load_private(p)
                else:
                    old=ROOT / 'reports/registration_comparison_final_20261011/private/attacks/literature' / str(seed) / 'prefixes'
                    candidates=sorted(old.glob(f'{key}-{m}-*.pkl.gz'))
                    candidates=[p for p in candidates if int(p.name.split('-')[-1].split('.')[0])>=max(cfg['budgets'])]
                    assert candidates
                    p=candidates[0];raw=load_private(p)
                    prefixes[m]={**raw,'ranks':{w:r for w,r in raw['ranks'].items() if r<=max(cfg['budgets'])}}
                    prefix_paths[m]=p
                assert prefixes[m]['complete'] and prefixes[m]['raw_emissions']>=max(cfg['budgets'])
            rates=score_counts(state['history'],prefixes,cfg['budgets'],cfg['users'])
            points=result['actual_attacks']['union_equal_total_B']['points']
            assert all(abs(a-b['rate_per_initial_user'])<1e-12 for a,b in zip(rates,points))
            audits.append({'seed':seed,'arm':arm,'counts_hash':key,'recount_matches':True,
                           'prefix_hashes':{m:hashlib.sha256(p.read_bytes()).hexdigest() for m,p in prefix_paths.items()}})
            soft=sum(t.get('soft_shape_size',0)>0 for t in result['timeline'])
            whole=sum(t['shape_size']>0 for t in result['timeline'])
            for b,r in zip(cfg['budgets'],rates):
                rows.append({'seed':seed,'arm':arm,'budget':b,'users':cfg['users'],'hits':round(r*cfg['users']),
                             'union_rate':r,'pcfg_rate':next(p['rate_per_initial_user'] for p in result['actual_attacks']['pcfg']['points'] if p['budget']==b),
                             'omen_rate':next(p['rate_per_initial_user'] for p in result['actual_attacks']['omen']['points'] if p['budget']==b),
                             'modification':result['cost']['modification_rate'],
                             'extra':result['cost']['mean_extra_submissions'],
                             'more_than_three':result['cost']['more_than_three_fraction'],
                             'eligible':not result['eligibility_failures'],'soft_batches':soft,'whole_batches':whole})
            target_batches=state['final_batches'] if arm in ('joint','graded') else state['batches']
            cumulative=__import__('collections').Counter()
            for t,counts in enumerate(target_batches):
                cumulative.update(counts)
                cost=state['costs'][t];timeline=result['timeline'][t]
                retrospective=score_counts(cumulative,prefixes,[cfg['primary_observed_budget'],cfg['secondary_observed_budget']],(t+1)*(cfg['users']//cfg['batches']))
                batches.append({'seed':seed,'arm':arm,'batch':t+1,'cumulative_users':(t+1)*(cfg['users']//cfg['batches']),
                                'retrospective_union_100k':retrospective[0],'retrospective_union_1m':retrospective[1],
                                'batch_extra':cost['mean_extra_submissions'],'batch_modification':cost['modification_rate'],
                                'exact_size':timeline['exact_size'],'whole_shapes':timeline['shape_size'],
                                'soft_shapes':timeline.get('soft_shape_size',0)})
    means=[]
    for arm in cfg['arms']:
        for b in cfg['budgets']:
            group=[r for r in rows if r['arm']==arm and r['budget']==b]
            mean={'arm':arm,'budget':b,'repeats':len(group),'eligible_repeats':sum(r['eligible'] for r in group)}
            for metric in ('union_rate','pcfg_rate','omen_rate','extra','modification','more_than_three'):
                values=[r[metric] for r in group]
                mean[metric+'_mean']=statistics.mean(values);mean[metric+'_sd']=statistics.stdev(values)
            means.append(mean)
    comparisons=[]
    for comparator in cfg['arms']:
        if comparator=='graded':continue
        for b in (cfg['primary_observed_budget'],cfg['secondary_observed_budget']):
            diffs=[];costs=[]
            for seed in cfg['seeds']:
                new=next(r for r in rows if r['seed']==seed and r['arm']=='graded' and r['budget']==b)
                old=next(r for r in rows if r['seed']==seed and r['arm']==comparator and r['budget']==b)
                diffs.append(old['union_rate']-new['union_rate']);costs.append(new['extra']-old['extra'])
            comparisons.append({'new':'graded','comparator':comparator,'budget':b,
                                'mean_risk_reduction_pp':100*statistics.mean(diffs),
                                'risk_reduction_sd_pp':100*statistics.stdev(diffs),
                                'improved_seeds':sum(d>0 for d in diffs),'repeats':len(diffs),
                                'mean_extra_increase':statistics.mean(costs)})
    csv_write(dest / 'per_seed_results.csv',rows);csv_write(dest / 'mean_results.csv',means)
    csv_write(dest / 'paired_comparisons.csv',comparisons);csv_write(dest / 'batch_records.csv',batches)
    write_json(root / 'audit.json',{'status':'passed','trajectories':len(audits),'all_actual_prefixes_complete':True,
                                   'all_recounts_match':True,'all_users_complete':True,'audits':audits})
    primary=cfg['primary_observed_budget'];secondary=cfg['secondary_observed_budget']
    text=['# 分批注册动态策略：全量实验结果','',
          '每组10万用户，分10批注册，5个配对种子。五次目标出现记录互不重叠，排除此前全部实验与小实验的角色记录；相同字符串仍可出现。开发训练、预览与旧校准参数复用，不能将出现记录当作经过核验的独立真人。','',
          '比较原始联合控制器、加入结构内限制与风险排序的新方案，以及Schechter精确计数接受机制的三个阈值（1e-5、3e-5、1e-4）。阈值与方案在访问本轮新目标数据之前冻结，不根据终测结果改选。Schechter的隐私计数结构未复现。','',
          '两种动态方案使用相同的原始候选网格、3000人选择预览及3000人确认预览、1万/10万蒙特卡洛样本、累计PCFG排名风险目标。新方案额外加入按训练模型风险排序的候选和结构内弱口令限制，保留原候选。风险限制集合由基础要求作用后的开发训练数据生成：PCFG和OMEN各前5万次实际猜测。','',
          '共同成本上限为修改比例60%、人均额外提交1次、超过3次额外提交的人数比例5%，无待处理用户。共享上限不是完全相同的实际成本；三档Schechter用于展示安全与成本取舍，不将插值当作实际执行的新方法。','',
          '选策风险目标保持小实验的10万PCFG不同字符串概率排名；这是估计值。最终攻击器分别在各方案作用后的6万个独立开发训练出现记录上重新训练，实际输出各100万次尝试，重复输出计费。联合总预算B各分B/2次；单模型列各使用B次，不能把它们相加当作同预算联合结果。10万是事先指定主指标，100万是补充指标，不能在结果出现后挑较好的预算作为唯一结论。','',
          '| 方案 | 联合10万次均值±标准差 | 联合100万次均值±标准差 | 人均额外提交 | 修改比例 | 成本合格次数 |',
          '|---|---:|---:|---:|---:|---:|']
    for arm in cfg['arms']:
        p=next(m for m in means if m['arm']==arm and m['budget']==primary)
        s=next(m for m in means if m['arm']==arm and m['budget']==secondary)
        text.append(f"| {NAMES[arm]} | {100*p['union_rate_mean']:.3f}% ± {100*p['union_rate_sd']:.3f}% | {100*s['union_rate_mean']:.3f}% ± {100*s['union_rate_sd']:.3f}% | {p['extra_mean']:.4f} | {100*p['modification_mean']:.2f}% | {p['eligible_repeats']}/5 |")
    text+=['','## 配对比较','', '| 比较对象 | 预算 | 新方案平均降低百分点 | 改善次数 | 平均额外提交增量 |', '|---|---:|---:|---:|---:|']
    for c in comparisons:
        text.append(f"| {NAMES[c['comparator']]} | {c['budget']} | {c['mean_risk_reduction_pp']:.3f} | {c['improved_seeds']}/5 | {c['mean_extra_increase']:+.4f} |")
    text+=['','## 结构规则与结论边界','']
    for arm in ('joint','graded'):
        rr=[r for r in rows if r['arm']==arm and r['budget']==primary]
        text.append(f"{NAMES[arm]}：整体结构限制总计采用{sum(r['whole_batches'] for r in rr)}批，结构内限制总计采用{sum(r['soft_batches'] for r in rr)}批。")
        text.append('')
    text+=['优劣需要同时结合猜中比例、实际重试成本和五次改善方向。负值、较差结果与成本不合格结果均保留。新方案同时增加风险排序和结构内限制，本轮不单独归因结构模块，也不证明文献新颖性。','',
           '逐批风险曲线使用最终攻击器对各阶段累计账户回顾评价，未在每批重训攻击器，也不是控制器当时看到的风险。逐批注册成本来自实际模拟记录。','',
           '交付文件包括逐种子结果、均值与标准差、配对差异、逐批记录、猜测预算曲线、成本与风险图、规则及重试变化图。','']
    (dest / '全量实验结果.md').write_text('\n'.join(text),'utf-8')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    for family in ('Microsoft YaHei','SimHei','Noto Sans CJK SC'):
        if any(f.name==family for f in font_manager.fontManager.ttflist):plt.rcParams['font.sans-serif']=[family];break
    plt.rcParams['axes.unicode_minus']=False
    def save(fig,name):
        fig.tight_layout();fig.savefig(dest / (name+'.png'),dpi=180);fig.savefig(dest / (name+'.pdf'));plt.close(fig)
    fig,ax=plt.subplots(figsize=(8.5,4.8))
    for arm in cfg['arms']:
        group=[m for m in means if m['arm']==arm]
        ax.errorbar([m['budget'] for m in group],[100*m['union_rate_mean'] for m in group],
                    yerr=[100*m['union_rate_sd'] for m in group],label=NAMES[arm],color=COLORS[arm],marker='o',capsize=3)
    ax.set(xscale='log',xlabel='两种攻击器总计猜测次数',ylabel='联合猜中比例（%）',title='10万用户 × 5次：实际猜测结果')
    ax.legend(fontsize=9);save(fig,'01_实际猜测预算曲线')
    fig,axes=plt.subplots(1,2,figsize=(11,4.6))
    for ax,budget in zip(axes,(primary,secondary)):
        for arm in cfg['arms']:
            m=next(m for m in means if m['arm']==arm and m['budget']==budget)
            ax.errorbar(m['extra_mean'],100*m['union_rate_mean'],xerr=m['extra_sd'],yerr=100*m['union_rate_sd'],
                        color=COLORS[arm],marker='o',label=NAMES[arm],capsize=3)
        ax.set(xlabel='人均额外提交次数',ylabel='联合猜中比例（%）',title=f'总计{budget:,}次猜测')
        ax.legend(fontsize=8)
    save(fig,'02_注册成本与猜测风险')
    fig,axes=plt.subplots(1,2,figsize=(11,4.6))
    for arm in cfg['arms']:
        xs=list(range(1,cfg['batches']+1))
        for ax,metric in ((axes[0],'retrospective_union_100k'),(axes[1],'batch_extra')):
            ys=[statistics.mean(r[metric] for r in batches if r['arm']==arm and r['batch']==t) for t in xs]
            ax.plot(xs,[y*100 for y in ys] if metric.startswith('retrospective') else ys,
                    label=NAMES[arm],color=COLORS[arm],marker='o',markersize=3)
    axes[0].set(xlabel='注册批次',ylabel='最终攻击器回顾猜中比例（%）',title='累计账户：总计10万次猜测')
    axes[1].set(xlabel='注册批次',ylabel='该批人均额外提交',title='实际模拟重试成本')
    for ax in axes:ax.legend(fontsize=8)
    save(fig,'03_分批风险与重试变化')
    fig,axes=plt.subplots(1,2,figsize=(11,4.4))
    for arm in ('joint','graded'):
        for ax,metric in ((axes[0],'whole_shapes'),(axes[1],'soft_shapes')):
            ys=[statistics.mean(r[metric] for r in batches if r['arm']==arm and r['batch']==t) for t in range(1,cfg['batches']+1)]
            ax.plot(range(1,cfg['batches']+1),ys,label=NAMES[arm],marker='o',color=COLORS[arm])
    axes[0].set(title='整体结构限制数量',xlabel='注册批次',ylabel='5次平均限制结构数')
    axes[1].set(title='结构内限制数量',xlabel='注册批次',ylabel='5次平均限制结构数')
    for ax in axes:ax.legend(fontsize=9)
    save(fig,'04_结构规则变化')
    print('Report, audit and four figure pairs saved',flush=True)


if __name__=='__main__':main()
