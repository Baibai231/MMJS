from pathlib import Path
import csv, json, sys
import numpy as np
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt, font_manager
out=Path(sys.argv[1]);summary=json.loads((out/'结果汇总.json').read_text('utf-8'));table={(s['epsilon'],s['method']):s for s in summary['summary']}
font_path=Path('C:/Windows/Fonts/simsun.ttc')
if font_path.exists():
 font_manager.fontManager.addfont(str(font_path))
 family=font_manager.FontProperties(fname=str(font_path)).get_name()
else:family='FandolSong'
plt.rcParams.update({'font.family':[family,'DejaVu Serif'],'axes.unicode_minus':False,'font.size':12,'pdf.fonttype':42,'svg.fonttype':'none','axes.linewidth':.8})
with (out/'逐次决策结果.csv').open(encoding='utf-8-sig',newline='') as h:raw=list(csv.DictReader(h))
fig,ax=plt.subplots(figsize=(7.5,4.8)); plot_data=[]
styles={'marginal':('边际匿名统计','#d97532','s','-'),'TriGuard':('TriGuard','#2875b9','o','-'),'exact_reference':('精确统计参考','#666666','^','--')}
for method in ('exact_reference','TriGuard','marginal'):
 label,color,marker,linestyle=styles[method];means=[];lower=[];upper=[]
 for epsilon in summary['settings']['epsilons']:
  subset=[r for r in raw if float(r['epsilon'])==epsilon and r['method']==method]
  values=[np.mean([float(r['extra_users']) for r in subset if int(r['repetition'])==rep]) for rep in range(20)]
  mean=float(np.mean(values));p10=float(np.quantile(values,.1));p90=float(np.quantile(values,.9))
  np.testing.assert_allclose(mean,table[(epsilon,method)]['mean_extra_users'])
  means.append(mean);lower.append(max(0,mean-p10));upper.append(max(0,p90-mean))
  plot_data.append(dict(epsilon=epsilon,method=method,mean_extra_users=mean,p10_extra_users=p10,p90_extra_users=p90))
 ax.errorbar(summary['settings']['epsilons'],means,yerr=[lower,upper],label=label,color=color,marker=marker,linestyle=linestyle,linewidth=1.8,markersize=6,capsize=4,elinewidth=1,clip_on=False)
ax.set_xlabel(r'隐私预算 $\varepsilon$',labelpad=9);ax.set_ylabel('平均额外覆盖人数 / 人',labelpad=9);ax.set_xticks([.5,1,2],['0.5','1','2']);ax.set_ylim(0,16);ax.set_xlim(.4,2.1);ax.set_yticks([0,4,8,12,16]);ax.grid(axis='y',alpha=.2);ax.spines[['top','right']].set_visible(False);ax.legend(loc='lower center',bbox_to_anchor=(.5,1.02),ncol=3,frameon=False,fontsize=11);fig.tight_layout(pad=1.3)
for ext in ('png','pdf','svg'):fig.savefig(out/f'受保护统计决策效用.{ext}',dpi=240,bbox_inches='tight')
plt.close(fig)
with (out/'绘图数据.csv').open('w',newline='',encoding='utf-8-sig') as h:
 w=csv.DictWriter(h,fieldnames=list(plot_data[0]));w.writeheader();w.writerows(plot_data)
base=table[(2,'marginal')];tri=table[(2,'TriGuard')];ref=table[(2,'exact_reference')];gain=tri['mean_coverage_pct']-base['mean_coverage_pct']
prose=f'''### 3.4.2 受保护统计的决策效用

管理员同时限制热门结构和完整口令时，两类限制可能覆盖相同账户，使部分完整口令限制无法增加覆盖人数。本作品保留结构内匿名热门频次，在选择完整口令限制时排除已被结构要求覆盖的部分，并通过测试评价受保护统计对联合限制选择的作用。

本作品依据软件实际使用的统计与候选生成方式开展以下测试：

1. 从已有RockYou数据划分中取10,000条历史记录，按至少8字符、阻断训练样本前1,000个常见口令的基础要求模拟完成注册，形成固定账户窗口。采用软件中的8类固定结构和OTHER桶，每桶保留前3个匿名热门频次。
2. 分别设置1或3个完整口令限制名额、1或2个结构限制名额，共4组组合。比较边际匿名统计与TriGuard：前者依据全局匿名频次选择完整口令，后者从未被结构限制覆盖的结构中选择。两者使用相同的加噪结构人数、结构限制集合和名额上限；另用精确统计计算相同结构限制下的最佳额外覆盖，作为参考。
3. 隐私预算取0.5、1和2，每档进行20次独立噪声重复。按软件配置将预算的80%用于核心统计，模拟三方离散噪声贡献的合成与后处理，并直接调用管理员网站使用的联合候选生成函数。测试程序以真实分布核对实际覆盖人数和人数预测误差，决策函数只读取匿名数值统计。

本次测试在离线固定窗口中评价统计发布后的候选选择，不执行用户重选或策略自动启用。各指标先对4组名额组合取平均，再汇总20次噪声重复。完整口令限制带来的平均额外覆盖人数见图；误差线表示噪声重复结果的10%至90%分位范围。隐私预算为2时，测试结果如下表所示。

| 方法 | 联合覆盖率 / % | 平均额外覆盖人数 / 人 | 人数预测误差 / 百分点 |
| --- | ---: | ---: | ---: |
| 边际匿名统计 | {base['mean_coverage_pct']:.3f} | {base['mean_extra_users']:.2f} | {base['mean_absolute_error_pp']:.3f} |
| TriGuard | {tri['mean_coverage_pct']:.3f} | {tri['mean_extra_users']:.2f} | {tri['mean_absolute_error_pp']:.3f} |
| 精确统计参考 | {ref['mean_coverage_pct']:.3f} | {ref['mean_extra_users']:.2f} | {ref['mean_absolute_error_pp']:.3f} |

结果表明，在本次固定窗口和名额配置下，TriGuard选取的完整口令限制均未与已选结构限制重复覆盖；隐私预算为2时，联合覆盖率由{base['mean_coverage_pct']:.3f}%提高到{tri['mean_coverage_pct']:.3f}%，增加{gain:.3f}个百分点，平均额外覆盖{tri['mean_extra_users']:.2f}个账户。受保护的结构内频次能够帮助管理员补充未被结构要求覆盖的热门口令，但本次总体覆盖增幅有限，尚未达到精确统计参考的额外覆盖水平。
'''
(out/'受保护统计的决策效用_报告正文.md').write_text(prose,encoding='utf-8')
print(prose)
