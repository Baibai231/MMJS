"""Two vector module diagrams traced to report sections 2.1.2 and 2.1.3."""
from pathlib import Path
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch,FancyArrowPatch
OUT=Path(__file__).resolve().parent
W,H=1830,790
INK='#303038';LINE='#777784';PURPLE='#E1E1FF';BLUE='#E4EEF9';PINK='#FFDDDD';GREEN='#DDFFDD';GRAY='#F5F5F5'
plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','DejaVu Sans'],'pdf.fonttype':42,'svg.fonttype':'none','axes.unicode_minus':False})
TEXT=[]
def canvas():
 fig,ax=plt.subplots(figsize=(W/110,H/110));fig.subplots_adjust(0,0,1,1);ax.set(xlim=(0,W),ylim=(H,0));ax.axis('off');return fig,ax

def text(ax,x,y,s,size=29,bold=False,color=INK,bg=None):
 TEXT.append(s)
 ax.text(x,y,s,fontsize=size*72/110,fontweight='bold' if bold else 'normal',color=color,ha='center',va='center',linespacing=1.35,zorder=5,bbox=dict(facecolor=bg,edgecolor='none',pad=3) if bg else None)

def rect(ax,x,y,w,h,fill='white',dashed=False,r=4):
 ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle=f'round,pad=0,rounding_size={r}',facecolor=fill,edgecolor=LINE,linewidth=1.1,linestyle=(0,(5,4)) if dashed else '-',zorder=1))

def arrow(ax,points):
 xs,ys=zip(*points);ax.plot(xs,ys,color=LINE,linewidth=1.3,zorder=2)
 ax.add_patch(FancyArrowPatch(points[-2],points[-1],arrowstyle='-|>',mutation_scale=13,color=LINE,linewidth=1.3,shrinkA=0,shrinkB=0,zorder=3))

def panel(ax,x,title,fill):
 rect(ax,x,62,400,565,dashed=True,r=12);rect(ax,x+13,77,374,63,fill,r=5);text(ax,x+200,108,title,32,True)

def node(ax,x,y,s,fill='white',h=82,size=28):
 rect(ax,x+22,y,356,h,fill);text(ax,x+200,y+h/2,s,size)

def sequence(ax,x,ys):
 for y,h,next_y in ys:arrow(ax,[(x+200,y+h),(x+200,next_y)])

def finish(fig,ax,name,feedback,footer):
 for x in (40,490,940):arrow(ax,[(x+400,325),(x+450,325)])
 arrow(ax,[(1590,627),(1590,690),(240,690),(240,627)])
 text(ax,915,690,feedback,28,bg='white')
 if footer:
  text(ax,915,757,footer,26,color='#575764')
 else:
  ax.set_ylim(730,0);fig.set_size_inches(W/110,730/110)
 for ext in ('pdf','svg','png'):fig.savefig(OUT/f'{name}.{ext}',dpi=240,facecolor='white')
 plt.close(fig)

fig,ax=canvas();xs=[40,490,940,1390]
for x,title,fill in zip(xs,['① 历史统计与候选','② 重选与风险评价','③ 独立确认与决策','④ 执行注册与反馈'],[PURPLE,BLUE,PINK,GREEN]):panel(ax,x,title,fill)
node(ax,40,166,'原始 Segreti 名单 $S_0$\n当前名单 $S_t$',GRAY)
node(ax,40,284,'最近两批的结构人数\n发现名单外的热门结构')
node(ax,40,402,'追加前 5 或 20 种结构\n原始、当前名单始终保留',PURPLE,h=96)
sequence(ax,40,[(166,82,284),(284,82,402)]);text(ax,240,565,'基础要求始终生效\n候选包含原始名单',27)
node(ax,490,166,'同一用户采用相同提议序列\n模拟被拒绝后的重选',h=96)
node(ax,490,298,'以开发用户重选口令\n重新训练 PCFG 与 OMEN',BLUE,h=96)
node(ax,490,430,'评价历史与新增账户的\n累计联合命中率',h=96)
sequence(ax,490,[(166,96,298),(298,96,430)]);text(ax,690,578,'降低风险后进入独立确认',27,True)
node(ax,940,166,'另一组预览用户\n重新比较候选与两种基准',h=96)
node(ax,940,298,'再次降低累计风险\n通过固定模型与注册检查',PINK,h=96)
node(ax,940,430,'确认通过：追加限制\n否则：保持或恢复原始名单',h=108,size=26)
sequence(ax,940,[(166,96,298),(298,96,430)]);text(ax,1140,583,'同分时优先保持当前名单',26)
node(ax,1390,166,'下一批按选定名单注册\n批内规则保持不变',h=96)
node(ax,1390,298,'完成重选并累计口令频次\n保存名单与确认记录',GREEN,h=96)
node(ax,1390,430,'更新近期结构与用户偏好\n下一批开始前重新选择',h=96)
sequence(ax,1390,[(166,96,298),(298,96,430)]);text(ax,1590,580,'全部批次结束后评价攻击效果',25)
finish(fig,ax,'registration-dynamic-overview-2-1-2','依据已完成批次的实际分布，重建候选并重新进行选择与确认','')

fig,ax=canvas()
for x,title,fill in zip(xs,['① 用户私密输入','② 三方秘密统计','③ 受保护发布与选规','④ 私密检查与更新'],[PURPLE,BLUE,PINK,GREEN]):panel(ax,x,title,fill)
node(ax,40,166,'可信客户端输入口令\n编码并填充至固定长度',GRAY,h=96)
node(ax,40,298,'每位使用新的随机数\n生成三份秘密份额',PURPLE,h=96)
sequence(ax,40,[(166,96,298)])
for offset,label in [(22,'计算方 1'),(144,'计算方 2'),(266,'计算方 3')]:
 rect(ax,40+offset,465,112,63,GRAY);text(ax,40+offset+56,496,label,24)
arrow(ax,[(240,394),(240,431),(118,431),(118,465)])
arrow(ax,[(240,431),(240,465)])
arrow(ax,[(240,431),(362,431),(362,465)])
text(ax,240,586,'每方仅接收自己的份额',27,True)
node(ax,490,166,'检查分享关系、位值与填充\n绑定同一秘密口令输入',h=96)
node(ax,490,298,'秘密推导 L/U/D/S 结构\n统计完整口令的相同字符串',BLUE,h=96,size=26)
node(ax,490,430,'结构人数＋结构内热门频次\n秘密保存匿名位置对应关系',h=96,size=26)
sequence(ax,490,[(166,96,298),(298,96,430)]);text(ax,690,583,'真实字符串保留在秘密计算内',25)
node(ax,940,166,'三方噪声在秘密计算中相加\n加噪后修正并发布统计',PINK,h=96,size=26)
node(ax,940,298,'管理员查看加噪人数\n与结构内匿名热门频次',h=96)
node(ax,940,430,'先选结构，再补结构外热点\n以快照和匿名位置指定限制',PINK,h=96,size=26)
sequence(ax,940,[(166,96,298),(298,96,430)]);text(ax,1140,583,'新发布扣减预算；同快照读缓存',25)
node(ax,1390,166,'注册／改密时私密检查\n基础要求、结构与完整口令',h=96)
node(ax,1390,298,'仅返回合并的通过／拒绝\n检查失败保留原统计',GREEN,h=96)
node(ax,1390,430,'成功后增加新记录\n改密同时撤销旧记录',h=96)
sequence(ax,1390,[(166,96,298),(298,96,430)]);text(ax,1590,583,'更新秘密状态后按预算重新发布',25)
# Update goes back to secret state, not to initial client sharing.
for x in (40,490,940):arrow(ax,[(x+400,325),(x+450,325)])
arrow(ax,[(1590,627),(1590,690),(690,690),(690,627)])
text(ax,1140,690,'成功更新秘密状态，下一次发布反映新分布',28,bg='white')
text(ax,915,757,'安全条件：客户端可信，三方独立管理、按协议执行，至多一方被动腐化',26,color='#575764')
for ext in ('pdf','svg','png'):fig.savefig(OUT/f'privacy-protection-overview-2-1-3.{ext}',dpi=240,facecolor='white')
plt.close(fig)
(OUT/'figure_text.json').write_text(json.dumps(TEXT,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('Created two module overviews in PDF, SVG and PNG.')
