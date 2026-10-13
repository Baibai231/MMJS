"""Rebuild five-method figures using aggregate results only."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

METHODS = [
    ('bidirectional_exchange','我们的双向调整 v2','#27648C','o','-'),
    ('schechter_strong','Schechter（严格）','#BA7635','s','--'),
    ('malone','Malone–Maher','#8A709A','^','-.'),
    ('segreti_struct_m_nohint','Segreti（结构黑名单）','#698B72','D',':'),
    ('github_core','GitHub 公开核心规则','#879DAF','v',(0,(5,2,1,2))),
]


def render(dest):
    dest=Path(dest)
    results=json.loads((dest/'data/final_results.json').read_text('utf-8'))
    for family in ('Microsoft YaHei','SimHei','Noto Sans CJK SC'):
        if any(font.name==family for font in font_manager.fontManager.ttflist):
            plt.rcParams['font.sans-serif']=[family];break
    plt.rcParams.update({'axes.unicode_minus':False,'font.size':11,'pdf.fonttype':42,'ps.fonttype':42,'svg.fonttype':'none'})
    def finish(fig,name):
        for suffix in ('png','pdf','svg'):
            fig.savefig(dest/(name+'.'+suffix),dpi=240,facecolor='white')
        plt.close(fig)
    def clean(axis):
        axis.spines['top'].set_visible(False);axis.spines['right'].set_visible(False)
        axis.set_axisbelow(True)
    budgets=[1000,10000,100000,1000000]
    fig,(left,right)=plt.subplots(1,2,figsize=(14.5,6.4),gridspec_kw={'width_ratios':[1.18,1]})
    fig.subplots_adjust(left=.065,right=.965,bottom=.19,top=.80,wspace=.43)
    fig.suptitle('五种口令策略的攻击效果与用户成本对比',fontsize=18,y=.96)
    fig.text(.5,.89,'新版算法 · 同一批新抽取的 100,000 名模拟用户 · 10 批注册',ha='center',color='#505B65')
    for arm,label,color,marker,style in METHODS:
        points=results[arm]['actual_attacks']['union_equal_total_B']['points']
        left.plot([p['budget'] for p in points],[100*p['rate_per_initial_user'] for p in points],
                  label=label,color=color,marker=marker,linestyle=style,linewidth=2.6 if arm==METHODS[0][0] else 1.9,markersize=6.5)
    left.set(xscale='log',ylim=(0,max(30,max(p['rate_per_initial_user']*100 for r in results.values() for p in r['actual_attacks']['union_equal_total_B']['points'])*1.12)),xlabel='PCFG 与 OMEN 合计实际猜测次数',ylabel='联合猜中比例（%）')
    left.set_title('（a）两模型联合攻击：越低越好',fontsize=12,pad=12)
    left.set_xticks(budgets,['1 千','1 万','10 万','100 万'])
    left.grid(axis='y',color='#E6EBEF',linewidth=.8)
    left.legend(loc='upper left',fontsize=9.5,framealpha=.95)
    values=[results[arm]['cost']['mean_extra_submissions'] for arm,*_ in METHODS]
    bars=right.barh(range(5),values,height=.55,color=[m[2] for m in METHODS],edgecolor='white')
    for bar,value in zip(bars,values):
        right.text(value+.04,bar.get_y()+bar.get_height()/2,f'{value:.4f}',va='center')
    right.set_yticks(range(5),[m[1] for m in METHODS],fontsize=10.5);right.invert_yaxis()
    right.set(xlim=(0,max(values)*1.2),xlabel='人均额外提交次数（次）')
    right.set_title('（b）注册成本：越低越好',fontsize=12,pad=12)
    right.grid(axis='x',color='#E6EBEF',linewidth=.8)
    for axis in (left,right):clean(axis)
    reduction=100*(1-values[0]/values[3])
    fig.text(.735,.072,f'相较 Segreti，人均额外提交次数降低 {reduction:.1f}%',ha='center',color='#27648C')
    fig.text(.5,.025,'联合总预算由两模型各分配一半；成本包含各方案基础规则引起的额外提交。',ha='center',color='#505B65',fontsize=10)
    finish(fig,'五方案攻击与用户成本')

    fig,axes=plt.subplots(1,3,figsize=(16,6.0))
    fig.subplots_adjust(left=.16,right=.98,bottom=.13,top=.80,wspace=.40)
    fig.suptitle('五种策略的口令修改与重试负担',fontsize=18,y=.95)
    fields=[('mean_edit_distance',1,'平均编辑距离（字符）'),('modification_rate',100,'修改用户比例（%）'),('more_than_three_fraction',100,'额外提交超过 3 次的用户（%）')]
    for axis,(field,scale,label) in zip(axes,fields):
        v=[results[m[0]]['cost'][field]*scale for m in METHODS]
        axis.barh(range(5),v,height=.55,color=[m[2] for m in METHODS])
        axis.invert_yaxis();axis.set_yticks(range(5),[m[1] for m in METHODS] if axis is axes[0] else [])
        axis.set(xlim=(0,max(v)*1.22),xlabel=label)
        axis.grid(axis='x',color='#E6EBEF');clean(axis)
        for i,x in enumerate(v):axis.text(x+max(v)*.018,i,f'{x:.3f}' if scale==1 else f'{x:.2f}',va='center',fontsize=10)
    fig.text(.5,.04,'编辑距离相对于用户最初口令；分母为各方案全部 100,000 名模拟用户，未修改者计 0。',ha='center',color='#505B65',fontsize=10)
    finish(fig,'五方案修改负担')

    fig,axes=plt.subplots(1,2,figsize=(13.5,6))
    fig.subplots_adjust(left=.07,right=.97,bottom=.15,top=.81,wspace=.22)
    fig.suptitle('策略条件化攻击器：分别查看 PCFG 与 OMEN',fontsize=17,y=.96)
    for axis,model in zip(axes,('pcfg','omen')):
        for arm,label,color,marker,style in METHODS:
            points=results[arm]['actual_attacks'][model]['points']
            axis.plot([p['budget'] for p in points],[100*p['rate_per_initial_user'] for p in points],label=label,color=color,marker=marker,linestyle=style,linewidth=2)
        axis.set(xscale='log',xlabel='该模型实际猜测次数',ylabel='猜中比例（%）',title=model.upper(),ylim=(0,None))
        axis.set_xticks(budgets,['1 千','1 万','10 万','100 万']);axis.grid(axis='y',color='#E6EBEF');clean(axis)
    axes[0].legend(fontsize=9,loc='upper left')
    fig.text(.5,.035,'本图为各单模型预算；主图为两模型各占一半的相同总预算，两者口径不同。',ha='center',color='#505B65',fontsize=10)
    finish(fig,'五方案单模型攻击')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('destination',type=Path,nargs='?',default=Path(__file__).resolve().parent)
    render(parser.parse_args().destination)
