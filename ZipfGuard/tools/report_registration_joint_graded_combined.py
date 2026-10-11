"""Combined report tables and figures, from this round's completed data only."""
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
    literature=cfg['arms']+extension['literature_arms'];website=extension['website_arms']
    allarms=literature+website
    dest=root / 'combined_deliverables';dest.mkdir(exist_ok=True)
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
            if arm in extension['literature_arms']+website:
                checkpoint=path / ('joint/state.pkl.gz' if arm=='github_graded' else 'state.pkl.gz')
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
                audit.append({'seed':seed,'arm':arm,'union_recount_matches':True,
                              'result_sha256':hashlib.sha256(file.read_bytes()).hexdigest(),
                              'prefix_hashes':{m:hashlib.sha256(p.read_bytes()).hexdigest() for m,p in prefix_files.items()}})
                target_batches=state['final_batches'] if arm=='github_graded' else state['batches']
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
    csv_write(dest / 'per_seed_results.csv',rows);csv_write(dest / 'mean_results.csv',means)
    csv_write(dest / 'paired_comparisons.csv',pairs);csv_write(dest / 'additional_batch_records.csv',batch_rows)
    write_json(root / 'combined_audit.json',{'status':'partial' if missing else 'passed',
                                           'expected_trajectories':50,'available_trajectories':len(rows)//len(cfg['budgets']),
                                           'missing':missing,'additional_recounts':audit})
    text=['# 分批注册动态策略：文献与网站统一对照','',
          f"状态：{'部分结果，不能作为最终结论' if missing else '全部50组完成'}。各组10万模拟用户，10批，5个配对种子。所有组复用这轮相同的新目标样本、训练样本与重选序列；不将旧实验结果当成本轮结果。",'',
          '本轮主指标为总计10万次实际猜测，100万次为补充指标；PCFG和OMEN各承担联合预算的一半，重复输出计费。单模型各自预算与联合总预算分别记录。攻击器用各方法作用后的6万个开发训练出现记录重新训练，未用目标口令训练。','',
          '原始联合方案与新方案保持共同基础（至少8位及训练前1000公共黑名单）及共同绝对成本上限。Schechter三档阈值、Malone概率接受和Segreti结构机制使用相同基础；网站实验另用GitHub公开长度与字符规则，共同成本按相对GitHub＋公共黑名单的增量约束计量。不能混用两类成本合格口径。','',
          '## 复现范围','',
          '- Schechter：精确计数流行度接受机制，不复现隐私计数结构。',
          '- Malone–Maher（2012，第6节）：从已见不同字符串均匀选参考，统计所有提议，按频次概率接受，参考在该用户重试中固定。共享首批注册作启动。',
          '- Segreti 等（2017，第3.3、3.4节）：适配 StructMNoHint 的预计算结构黑名单，取10万个符合共同基础的PCFG不同字符串，禁止它们对应的全部字符类别结构；实际结构数由本轮开发模型确定，不能照抄原论文的2141。黑名单固定，不替换为简单近期top-j。共同基础、训练集与攻击器统一到本项目，不宣称完整重现其3c12真人研究或反馈效果。',
          '- GitHub：至少15个字符，或至少8个字符且含小写字母和数字。仅复现公开核心规则，完整弱口令服务端检查未复现；共同训练黑名单作为独立基础控制，不能冒充GitHub真实黑名单。','',
          '新增范围在原25组启动后按用户要求补入，不改变原方案、原样本或既定预算，不根据已出现的本轮结果调整新增机制。','']
    for title,arms in (('文献机制与原方案对照',literature),('网站策略对照',website)):
        text+=['## '+title,'','| 方案 | 完成次数 | 联合10万次均值±标准差 | 联合100万次均值±标准差 | 平均额外提交 | 平均增量提交 | 成本合格次数 |','|---|---:|---:|---:|---:|---:|---:|']
        for arm in arms:
            p=next((r for r in means if r['arm']==arm and r['budget']==cfg['primary_observed_budget']),None)
            s=next((r for r in means if r['arm']==arm and r['budget']==cfg['secondary_observed_budget']),None)
            if not p:text.append(f'| {NAMES[arm]} | 0/5 | 待完成 | 待完成 | 待完成 | 待完成 | 待完成 |');continue
            text.append(f"| {NAMES[arm]} | {p['repeats']}/5 | {100*p['union_rate_mean']:.3f}% ± {100*p['union_rate_sd']:.3f}% | {100*s['union_rate_mean']:.3f}% ± {100*s['union_rate_sd']:.3f}% | {p['extra_mean']:.4f} | {p['incremental_extra_mean']:.4f} | {p['eligible_repeats']}/{p['repeats']} |")
        text+=['']
    text+=['## 结论边界','',
          '相同成本上限不等于相同实际成本；必须同时报告猜中比例、成本、五次改善方向及规则使用情况。成本不合格、负收益和未完成攻击不能删掉或当作通过。网站比较以新动态方案对GitHub＋共同黑名单为直接增益证据，同时列GitHub公开核心规则。','',
          'Segreti 是论文机制在统一模拟环境中的适配复现，模拟重选成本不能替代真人时间、记忆和满意度；也不复现原论文所有配置。新方案同时添加风险排序和结构内限制，本轮不能将全部改善只归因于结构内限制。','',
          '逐批风险采用最终攻击器回顾评价，没有为每批重新训练攻击器；逐批成本与规则来自注册记录。均值与标准差描述五次同源模拟重复，不把种子当作跨网站泛化证据。','',
          '## 来源','',
          '- Malone–Maher：https://www.maths.tcd.ie/~dwmalone/p/www2012.pdf',
          '- Segreti：https://www.usenix.org/system/files/conference/soups2017/soups2017-segreti.pdf',
          '- GitHub：https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-strong-password','']
    (dest / '文献与网站对照结果.md').write_text('\n'.join(text),'utf-8')
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
            axes[0].errorbar([r['budget'] for r in mm],[100*r['union_rate_mean'] for r in mm],
                             yerr=[100*r['union_rate_sd'] for r in mm],label=NAMES[arm],color=colors[arm],marker='o',capsize=2)
            p=next(r for r in mm if r['budget']==cfg['primary_observed_budget'])
            axes[1].errorbar(p['extra_mean'],100*p['union_rate_mean'],xerr=p['extra_sd'],yerr=100*p['union_rate_sd'],
                             label=NAMES[arm],color=colors[arm],marker='o',capsize=3)
        axes[0].set(xscale='log',xlabel='两种攻击器总计实际猜测次数',ylabel='联合猜中比例（%）',title='实际攻击预算曲线')
        axes[1].set(xlabel='人均额外提交次数',ylabel='联合10万次猜中比例（%）',title='实际成本与风险')
        for ax in axes:ax.legend(fontsize=8)
        fig.suptitle(groupname+('（部分结果）' if missing else '：10万用户 × 5次'))
        fig.tight_layout();fig.savefig(dest / (groupname+'.png'),dpi=180);fig.savefig(dest / (groupname+'.pdf'));plt.close(fig)
    print(f'Combined outputs: {len(rows)//len(cfg["budgets"])}/50 trajectories; missing {len(missing)}',flush=True)


if __name__=='__main__':main()
