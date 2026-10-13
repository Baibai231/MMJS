"""Read-only recount of new/historical evidence; aggregate comparisons only."""
from pathlib import Path
from collections import Counter
import argparse
import csv
import hashlib
import json
import math
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from experiments.registration_joint_data import load_private
from experiments.registration_joint_pipeline import write_json, summed_cost
from core.corpus import counts_hash


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_arm(root,arm,label):
    path=root/'runs/11'/arm
    result_path=path/'result.json'
    result=json.loads(result_path.read_text('utf-8'))
    state_path=path/'state.pkl.gz'
    if not state_path.exists():
        state_path=path/'joint/state.pkl.gz'
    state=load_private(state_path)
    if result['status']!='complete' or result['attack_failures']:
        raise ValueError('Incomplete arm evidence')
    n=result['cost']['initial_users']
    assert n==100000 and sum(state['history'].values())==n
    assert len(state['attempts'])==n and result['cost']['pending_users']==0
    assert math.isclose(sum(state['attempts'])/n,result['cost']['mean_extra_submissions'],abs_tol=1e-12)
    assert math.isclose(sum(a>3 for a in state['attempts'])/n,result['cost']['more_than_three_fraction'],abs_tol=1e-12)
    assert sum(state.get('final_batches',state.get('batches')),Counter())==state['history']
    # No cache constructor is called: missing evidence must fail, never retrain.
    cache=root/'private/attacks/11'/arm/'prefixes'
    prefix_paths=[cache/f"{result['A1_training_sha256']}-{model}-1000000.pkl.gz" for model in ('pcfg','omen')]
    prefixes={model:load_private(p) for model,p in zip(('pcfg','omen'),prefix_paths)}
    for p in prefixes.values():
        assert p['complete'] and p['raw_emissions']==1000000
    for name,curve in result['actual_attacks'].items():
        for point in curve['points']:
            budget=point['budget']
            if name in prefixes:
                hits=sum(c for w,c in state['history'].items() if prefixes[name]['ranks'].get(w,budget+1)<=budget)
            else:
                per=budget if name=='union_each_model_B' else budget//2
                hits=sum(c for w,c in state['history'].items() if any(p['ranks'].get(w,per+1)<=per for p in prefixes.values()))
                assert point['per_model_budget']==per and point['max_total_attempts']==per*2
            assert point['hits']==hits and math.isclose(point['rate_per_initial_user'],hits/n,abs_tol=1e-12)
    # Some historical aggregators substituted 0 for unmeasured None. Inspect
    # original batch summaries instead of interpreting that 0 as zero edit effort.
    measured=all(s.get('mean_edit_distance') is not None for s in state['costs'])
    edits=result['cost']['mean_edit_distance'] if measured else None
    modification=result['cost']['modification_rate']
    ordered=sorted(state['attempts'])
    row={'arm':arm,'label':label,'users':n,'per_model_budget':1000000,'max_total_budget':2000000,
         **{m:result['actual_attacks'][m]['points'][-1]['rate_per_initial_user']
            for m in ('pcfg','omen','union_each_model_B','union_equal_total_B')},
         'extra':result['cost']['mean_extra_submissions'],'modification':modification,
         'mean_edit_all_users':edits,'mean_edit_modified_users':edits/modification if edits is not None and modification else None,
         'more_than_three':result['cost']['more_than_three_fraction'],
         'extra_p90':ordered[int(.9*n)],'extra_p95':ordered[int(.95*n)],'extra_max':max(ordered),
         'outside_both':result['final_dual_risk']['outside_model_support_fraction'],'pending':0}
    evidence={'label':label,'root':str(root),'result_sha256':digest(result_path),
              'state_sha256':digest(state_path),'partition_sha256':digest(root/'private/partition.pkl.gz'),
              'prefix_sha256':{m:digest(p) for m,p in zip(('pcfg','omen'),prefix_paths)},
              'recount':'passed','edit_distance_measured':measured,'human_verification':'pending'}
    if arm=='bidirectional_exchange':
        assert summed_cost(state['costs'])==result['cost']
        assert counts_hash(state['dev_history'])==result['A1_training_sha256']
        assert sum(state['dev_history'].values())==result['A1_training_occurrences']==60000
        assert len(state['timeline'])==10 and len(state['decisions'])==9
        assert len(list(path.glob('candidate_audit_*.json')))==9
    return row,result,evidence,state


def plot(dest,rows,results):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    for family in ('Microsoft YaHei','SimHei','Noto Sans CJK SC'):
        if any(f.name==family for f in font_manager.fontManager.ttflist):
            plt.rcParams['font.sans-serif']=[family];break
    plt.rcParams.update({'font.size':10,'axes.unicode_minus':False,'pdf.fonttype':42,'svg.fonttype':'none'})
    colors=['#006D77','#7E9C75','#E29578','#8B6BA4','#699ECA','#AB7546','#CBA258','#9C9FA4','#B18A72']
    fig,axes=plt.subplots(1,2,figsize=(12.6,6.3))
    fig.subplots_adjust(left=.07,right=.96,top=.86,bottom=.30,wspace=.28)
    fig.suptitle('单组10万用户重测：编辑量感知双向调整 v2',fontsize=16,y=.98)
    for i,(row,result) in enumerate(zip(rows,results)):
        points=result['actual_attacks']['union_each_model_B']['points']
        axes[0].plot([p['budget'] for p in points],[100*p['rate_per_initial_user'] for p in points],
                     color=colors[i],label=row['label'],marker='o' if i==0 else '.',
                     linewidth=2.5 if i==0 else 1.4,linestyle='-' if i==0 else '--',zorder=10 if i==0 else 3)
        axes[1].scatter(row['extra'],100*row['union_each_model_B'],color=colors[i],
                        s=130 if i==0 else 55,marker='*' if i==0 else 'o',zorder=10 if i==0 else 4)
        axes[1].annotate(str(i+1),(row['extra'],100*row['union_each_model_B']),
                         xytext=(-15,8) if i==0 else (5,-13) if i==1 else (5,5),
                         textcoords='offset points',fontsize=10,color=colors[i])
    axes[0].set(xscale='log',xlabel='每模型实际猜测次数',ylabel='联合猜中比例（%）',title='（a）联合攻击曲线')
    axes[0].set_xticks([1000,10000,100000,1000000],['1千','1万','10万','100万'])
    axes[0].set_ylim(bottom=0)
    axes[1].set(xlabel='人均额外提交次数',ylabel='联合猜中比例（%）',title='（b）安全与负担：左下方更优')
    axes[1].set_xlim(left=0);axes[1].set_ylim(bottom=0)
    for ax in axes:
        ax.grid(alpha=.2);ax.spines['top'].set_visible(False);ax.spines['right'].set_visible(False)
    handles,labels=axes[0].get_legend_handles_labels()
    labels=[f'{i+1}. {label}' for i,label in enumerate(labels)]
    fig.legend(handles,labels,loc='lower center',bbox_to_anchor=(.5,.065),ncol=3,frameon=False,fontsize=9)
    fig.text(.5,.02,'主终点每模型100万次，合计最多200万次。仅新方案重跑；旧对照复用同一用户流。一次探索性比较。',ha='center',fontsize=9,color='#59636C')
    for suffix in ('png','pdf','svg'):
        fig.savefig(dest/f'双向调整与历史方案对照.{suffix}',dpi=200,facecolor='white')
    plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('root',type=Path)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    root=args.root.resolve();dest=args.output.resolve();dest.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((root/'manifest.json').read_text('utf-8'))
    cfg=manifest['config']
    for file,expected in manifest['source_hashes'].items():
        assert digest(ROOT/file)==expected,'Frozen execution source changed: '+file
    old=Path(cfg['comparison_source'])
    historical=Path('C:/Users/jlx51/.codex/visualizations/2026/10/11/01a12a85-b74d-7740-89ee-d9101382a8d9')
    specs=[(root,'bidirectional_exchange','优化双向调整 v2'),
           (old,'segreti_struct_m_nohint','固定 Segreti'),
           (historical/'sp','segreti_dynamic','此前单向新增'),
           (historical/'registration_variable_scope_20261012','graded','此前可调范围'),
           (old,'graded','此前限成本方案'),(old,'schechter_strong','Schechter 严格档'),
           (old,'malone','Malone–Maher'),(old,'github_core','GitHub 核心规则'),
           (Path(cfg['previous_exchange_root']),'bidirectional_exchange','此前双向调整 v1')]
    rows=[];results=[];evidence=[]
    for source,arm,label in specs:
        row,result,record,state=read_arm(source,arm,label)
        assert record['partition_sha256']==cfg['paired_partition_sha256']
        rows.append(row);results.append(result);evidence.append(record)
    new,fixed=rows[:2]
    with (dest/'comparison_summary.csv').open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(new));writer.writeheader();writer.writerows(rows)
    write_json(dest/'comparison_evidence.json',{'status':'passed','new_complete_trajectories':1,
        'historical_retraining':False,'historical_result_mutations':False,'same_partition_all_arms':True,
        'records':evidence,'analysis_type':'paired post-hoc single-seed exploration','human_verification':'pending'})
    lines=['# 编辑量感知双向调整 v2：单组十万用户重测结果','',
           '只新增运行双向调整方案；八组历史结果只读复用。共同用户划分、seed 11、十批注册、假设响应序列相同。',
           '', 'PCFG 与 OMEN 各实际猜测一百万次，合计最多二百万次；重复猜测计费，联合命中同一账户只计一次。',
           '', '| 方法 | 联合猜中率 | 人均额外提交 | 口令改变比例 | 超过3次 | 平均编辑量 |',
           '| --- | ---: | ---: | ---: | ---: | ---: |']
    for row in rows:
        edits=f"{row['mean_edit_all_users']:.5f}" if row['mean_edit_all_users'] is not None else '未测'
        lines.append(f"| {row['label']} | {100*row['union_each_model_B']:.3f}% | {row['extra']:.5f} | {100*row['modification']:.3f}% | {100*row['more_than_three']:.3f}% | {edits} |")
    risk_delta=100*(new['union_each_model_B']-fixed['union_each_model_B'])
    cost_delta=new['extra']-fixed['extra']
    lines+=['',f'相对固定 Segreti，联合猜中率变化 {risk_delta:+.3f} 个百分点，人均额外提交变化 {cost_delta:+.5f} 次。',
            '', '本轮是否同时改善：'+('是，两项均严格降低。' if risk_delta<0 and cost_delta<0 else '否，未同时降低这两项。'),
            '',f"新方案平均编辑量为 {new['mean_edit_all_users']:.5f} 字符／全部用户；修改用户条件均值为 {new['mean_edit_modified_users']:.5f} 字符／修改用户。",
            '固定 Segreti 等部分历史组未测编辑距离，旧汇总中的 0 是缺失值汇总造成的占位，不能解释为无字符修改。',
            '', '## 逐批实际规则', '', '| 批次 | 硬阻断结构 | 选择性结构 | 更新状态 |', '| --- | ---: | ---: | --- |']
    for i,(timeline,decision) in enumerate(zip(results[0]['timeline'],[{'status':'共同原名单起点'}]+results[0]['decisions']),1):
        lines.append(f"| {i} | {timeline['shape_size']} | {timeline.get('soft_shape_size',0)} | {decision['status']} |")
    lines+=['','## 解释边界','',
            '成本约束针对开发选择与确认，不保证目标执行成本逐批满足。公开基础规则保持；候选和最终攻击均由开发训练响应条件化训练。',
            '本方案按旧实验结果提出，并复用旧目标用户流，只能报告一次配对探索的实际效果。没有新独立目标、跨种子稳定性或真人体验结论。',
            '即使最终优于某个历史档位，也不能称已优于该方法全部可调参数下的风险—成本前沿。',
            '', '![双向调整与历史方案对照](双向调整与历史方案对照.png)', '',
            '输入结果、用户状态和攻击前缀哈希见 `comparison_evidence.json`；作图数据见 `comparison_summary.csv`。']
    audits=[json.loads(p.read_text('utf-8')) for p in sorted((root/'runs/11/bidirectional_exchange').glob('candidate_audit_*.json'))]
    screening_diagnosis=[]
    for a in audits:
        assert len(a['candidates'])<=cfg['fit_shortlist']
        assert all(x['cost']['mean_edit_distance'] is not None for x in a['screening'])
        for row in a['candidates']:
            matches=[x for x in a['screening'] if x['rule']==row['rule'] and x['shortlisted']]
            assert len(matches)==1
            # Full uncached shortlist replay must reproduce cached screening.
            assert matches[0]['cost']==row['cost']
            if row['family']!='anchor':
                assert matches[0]['cost_ok'] and not matches[0]['failures']
        screening_diagnosis.append({'status':a['status'],'screened':len(a['screening']),
            'cost_feasible':sum(x['cost_ok'] and x['family']!='anchor' for x in a['screening']),
            'pareto':sum(x['pareto'] for x in a['screening']),
            'shortlisted':len(a['candidates']),'cached_and_direct_replays_match':True})
    blockers=Counter(f for a in audits for r in a['candidates'] if r['family']!='anchor' for f in r.get('failures',[]))
    missed=[]
    for a in audits:
        baseline=next(r for r in a['candidates'] if 'baseline' in r['roles'])
        for r in a['candidates']:
            if (r['family']!='anchor' and r['predicted_A1']<baseline['predicted_A1']
                    and r['cost']['mean_extra_submissions']<baseline['cost']['mean_extra_submissions']
                    and 'mean_edit_distance' in r.get('failures',[])):
                missed.append({'roles':r['roles'],'risk_improvement_pp':100*(baseline['predicted_A1']-r['predicted_A1']),
                               'extra_saving':baseline['cost']['mean_extra_submissions']-r['cost']['mean_extra_submissions'],
                               'edit_increase':r['cost']['mean_edit_distance']-baseline['cost']['mean_edit_distance']})
    write_json(dest/'selection_diagnosis.json',{'candidate_failure_counts':dict(blockers),
        'risk_and_submission_improved_but_edit_increased':missed,
        'edit_aware_screening':screening_diagnosis,
        'actual_updated_batches':sum(x['updated'] for x in results[0]['decisions']),
        'scope':'development previews, not additional target trajectories'})
    lines+=['','## 候选未通过的原因','',
            f"本轮实际更新 {sum(x['updated'] for x in results[0]['decisions'])} 批；共有 {len(missed)} 个开发候选同时降低预测风险与提交数，但平均编辑量增加，未满足完整目标。",
            '候选排除条件可同时出现，计数不可相加解释为不同用户人数。明细见 `selection_diagnosis.json`。']
    previous=rows[-1]
    assert previous['label']=='此前双向调整 v1'
    lines+=['','## 相对 v1 的优化效果','',
        f"相对 v1：联合猜中率变化 {100*(new['union_each_model_B']-previous['union_each_model_B']):+.3f} 个百分点，平均额外提交变化 {new['extra']-previous['extra']:+.5f} 次，平均编辑距离变化 {new['mean_edit_all_users']-previous['mean_edit_all_users']:+.5f} 字符。",
        'v1 与 v2 均完整测量编辑量，分母一致；成本门槛、用户响应与六个重训名额保持，搜索机制变化单独记录。',
        '', '| 批次 | 粗筛候选 | 全成本合格 | Pareto 候选 | 重训短名单 |',
        '| --- | ---: | ---: | ---: | ---: |']
    for t,row in enumerate(screening_diagnosis,2):
        lines.append(f"| {t} | {row['screened']} | {row['cost_feasible']} | {row['pareto']} | {row['shortlisted']} |")
    if (root/'summary_cache_repair.json').exists():
        lines+=['','运行中修复了重复计算大型集合摘要的性能问题，从两批检查点继续。摘要与原实现逐项一致的检查通过，候选算法、用户响应、配置及选择条件未改变；原实现哈希和修复记录保留于运行目录。']
    report='\n'.join(lines)+'\n'
    assert report.count('```')%2==0 and report.count('$$')%2==0
    (dest/'完整结果与比较.md').write_text(report,'utf-8')
    plot(dest,rows,results)
    write_json(root/'audit.json',{'status':'passed','comparison_output':str(dest),
        'records':evidence,'human_verification':'pending'})
    print(json.dumps({'new':new,'fixed_segreti':fixed,'risk_delta_pp':risk_delta,'extra_delta':cost_delta},ensure_ascii=False))


if __name__=='__main__':
    main()
