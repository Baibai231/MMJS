"""Compact module overview, replacing the detailed branch flowchart.

Content is restricted to report section 2.1.1. Reference layout: user supplied
codex-clipboard-3699d65f-c624-4b00-b5a0-e88bb85f692f.png.
"""
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle

OUT=Path(__file__).resolve().parent
W,H=1830,750
INK='#303038'; LINE='#777784'
PURPLE='#E1E1FF'; BLUE='#E4EEF9'; PINK='#FFDDDD'; GREEN='#DDFFDD'; GRAY='#F5F5F5'
plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','DejaVu Sans'],
                    'pdf.fonttype':42,'svg.fonttype':'none','axes.unicode_minus':False})
fig,ax=plt.subplots(figsize=(W/110,H/110))
fig.subplots_adjust(0,0,1,1)
ax.set(xlim=(0,W),ylim=(H,0));ax.axis('off')

def text(x,y,s,size=31,bold=False,color=INK,bg=None):
    ax.text(x,y,s,fontsize=size*72/110,fontweight='bold' if bold else 'normal',
            color=color,ha='center',va='center',linespacing=1.3,zorder=5,
            bbox=dict(facecolor=bg,edgecolor='none',pad=3) if bg else None)

def rect(x,y,w,h,fill='white',dashed=False,r=0,lw=1.1):
    ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle=f'round,pad=0,rounding_size={r}',
                 facecolor=fill,edgecolor=LINE,linewidth=lw,
                 linestyle=(0,(5,4)) if dashed else '-',zorder=1))

def arrow(points,dashed=False):
    xs,ys=zip(*points)
    ax.plot(xs,ys,color=LINE,linewidth=1.3,linestyle=(0,(5,4)) if dashed else '-',zorder=2)
    ax.add_patch(FancyArrowPatch(points[-2],points[-1],arrowstyle='-|>',
                 mutation_scale=13,color=LINE,linewidth=1.3,shrinkA=0,shrinkB=0,zorder=3))

def panel(x,w,title,fill):
    rect(x,66,w,530,dashed=True,r=12)
    rect(x+13,80,w-26,63,fill,r=5,lw=0.9)
    text(x+w/2,111,title,34,True)

def node(x,y,w,h,s,fill='white',size=30,bold=False):
    rect(x,y,w,h,fill,r=3)
    text(x+w/2,y+h/2,s,size,bold)

panel(40,380,'① 分布统计与评价',PURPLE)
panel(465,390,'② 构建修改候选',BLUE)
panel(900,440,'③ 模拟比较与选取',PINK)
panel(1385,405,'④ 执行与反馈',GREEN)

# Module 1: the two distances are kept distinct.
node(66,170,328,65,'当前账户与口令',GRAY)
node(66,277,328,65,'统计完整口令频次')
arrow([(230,235),(230,277)])
node(66,384,328,90,'CDF-Zipf拟合\n与理想分布比较 $W_1$',PURPLE,29)
arrow([(230,342),(230,384)])
text(230,540,'实际距离用于初筛\n拟合距离用于评分',29)

# Module 2: g,q,n are visual components, not a paragraph copied into a box.
node(491,170,338,107,'热门口令与结构群体\n兼顾重复次数与随机选人',size=29)
for x,s in [(491,'群体 g'),(609,'规则 q'),(727,'人数 n')]:
    node(x,334,102,67,s,BLUE,29,True)
arrow([(660,277),(660,320)])
text(660,445,'形成候选 a = (g, q, n)',29,True)
node(491,493,338,76,'筛选未通知且不合规账户\n保留基础要求，限制通知人数',GRAY,26)

# Module 3: every simulated password undergoes the same acceptance check.
node(926,170,388,64,'模拟修改与重选')
node(926,264,388,80,'基础要求与本轮规则检查\nPCFG＋OMEN检查新口令',PINK,28)
arrow([(1120,234),(1120,264)])
node(926,374,388,80,'实际距离初筛，多次拟合复核\n评分：平均拟合距离下降',size=27)
arrow([(1120,344),(1120,374)])
node(926,484,388,79,'普通与组合方案按评分选取\n无可接受方案时才前瞻两轮',PINK,27)
arrow([(1120,454),(1120,484)])

# Module 4: only the selected current batch is committed.
node(1411,170,353,80,'确定本轮名单、规则和人数\n生成正式响应，再次检查',size=28)
node(1411,294,353,80,'通过后统一更新账户与频次\n记录通知、修改和编辑成本',GREEN,27)
arrow([(1587,250),(1587,294)])
node(1411,418,353,80,'保存方案与执行反馈\n比较预测和实际距离变化',size=29)
arrow([(1587,374),(1587,418)])
text(1587,550,'进入下一轮或停止',30,True)

# Short inter-module arrows preserve the horizontal overview style.
for a,b in [(420,465),(855,900),(1340,1385)]:arrow([(a,312),(b,312)])
arrow([(1587,596),(1587,657),(230,657),(230,596)])
text(905,657,'按实际更新的状态重新统计、重建群体并选择下一轮方案',30,bg='white')
text(915,719,'停止条件：名额用尽、达到轮数上限、无符合条件的账户或无可接受方案',28,color='#575764')

for ext in ['pdf','svg','png']:
    fig.savefig(OUT/f'existing-dynamic-overview-2-1-1.{ext}',dpi=240,facecolor='white')
plt.close(fig)
print('Created compact module overview PDF, SVG and PNG.')
