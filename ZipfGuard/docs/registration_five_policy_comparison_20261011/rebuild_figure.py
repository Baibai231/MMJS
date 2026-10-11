"""Rebuild this published figure from aggregate final results; no new experiments."""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import MultipleLocator

METHODS = [
    ('graded', '我们的改进方案', '#27648C', 'o', '-'),
    ('schechter_strong', 'Schechter（严格）', '#BA7635', 's', '--'),
    ('malone', 'Malone–Maher', '#8A709A', '^', '-.'),
    ('segreti_struct_m_nohint', 'Segreti（结构黑名单）', '#698B72', 'D', ':'),
    ('github_core', 'GitHub 公开核心规则', '#879DAF', 'v', (0, (5, 2, 1, 2))),
]


def main():
    dest = Path(__file__).resolve().parent
    results = json.loads((dest / 'data/final_results.json').read_text('utf-8'))
    cfg = {'budgets': [1000,10000,100000,1000000], 'primary_observed_budget':100000}
    for family in ('Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC'):
        if any(font.name == family for font in font_manager.fontManager.ttflist):
            plt.rcParams['font.sans-serif'] = [family]
            break
    plt.rcParams.update({'axes.unicode_minus': False, 'font.size': 11,
                         'pdf.fonttype': 42, 'ps.fonttype': 42, 'svg.fonttype': 'none'})
    fig, (attack_ax, cost_ax) = plt.subplots(1, 2, figsize=(14.5, 6.4),
                                            gridspec_kw={'width_ratios': [1.18, 1]})
    fig.subplots_adjust(left=.065, right=.965, bottom=.19, top=.80, wspace=.43)
    fig.suptitle('五种口令策略的攻击效果与用户成本对比', fontsize=18, y=.96)
    fig.text(.5, .89, '全量 100,000 名模拟用户 · 10 批注册 · 第一轮结果',
             ha='center', color='#505B65', fontsize=11)
    for arm, label, color, marker, style in METHODS:
        points = results[arm]['actual_attacks']['union_equal_total_B']['points']
        attack_ax.plot([p['budget'] for p in points], [100*p['rate_per_initial_user'] for p in points],
                       label=label, color=color, marker=marker, linestyle=style,
                       linewidth=2.6 if arm == 'graded' else 1.9, markersize=6.5)
    attack_ax.set(xscale='log', ylim=(0, 30),
                  xlabel='PCFG 与 OMEN 合计实际猜测次数', ylabel='联合猜中比例（%）')
    attack_ax.set_title('（a）两模型联合攻击：越低越好', fontsize=12, pad=12)
    attack_ax.set_xticks(cfg['budgets'], ['1 千', '1 万', '10 万', '100 万'])
    attack_ax.yaxis.set_major_locator(MultipleLocator(5))
    attack_ax.grid(axis='y', color='#E6EBEF', linewidth=.8)
    attack_ax.set_axisbelow(True)
    attack_ax.axvline(cfg['primary_observed_budget'], color='#A6AFB7', linewidth=.8, linestyle='--', zorder=0)
    attack_ax.legend(loc='upper left', fontsize=9.5, framealpha=.95)
    values = [results[arm]['cost']['mean_extra_submissions'] for arm, *_ in METHODS]
    bars = cost_ax.barh(range(len(METHODS)), values, height=.55,
                        color=[m[2] for m in METHODS], edgecolor='white', linewidth=.8)
    for bar, value in zip(bars, values):
        cost_ax.text(value + .04, bar.get_y()+bar.get_height()/2, f'{value:.4f}', va='center', fontsize=11)
    cost_ax.set_yticks(range(len(METHODS)), [m[1] for m in METHODS], fontsize=10.5)
    cost_ax.invert_yaxis()
    cost_ax.set(xlim=(0, 2.75), xlabel='人均额外提交次数（次）')
    cost_ax.set_title('（b）注册成本：越低越好', fontsize=12, pad=12)
    cost_ax.xaxis.set_major_locator(MultipleLocator(.5))
    cost_ax.grid(axis='x', color='#E6EBEF', linewidth=.8)
    cost_ax.set_axisbelow(True)
    for axis in (attack_ax, cost_ax):
        axis.spines['top'].set_visible(False)
        axis.spines['right'].set_visible(False)
    reduction = 100*(1-values[0]/values[3])
    fig.text(.735, .072, f'相较 Segreti，改进方案的人均额外提交次数降低 {reduction:.1f}%',
             ha='center', color='#27648C', fontsize=11)
    fig.text(.5, .025, '联合预算由 PCFG 与 OMEN 各分配一半；用户成本包含各方案基础规则引起的额外提交。',
             ha='center', color='#505B65', fontsize=10)
    base = dest / '五方案攻击与用户成本'
    for suffix in ('png', 'pdf', 'svg'):
        fig.savefig(base.with_suffix('.'+suffix), dpi=300, facecolor='white')
    svg_path = base.with_suffix('.svg')
    svg_path.write_text('\n'.join(line.rstrip() for line in svg_path.read_text('utf-8').splitlines())+'\n', encoding='utf-8')
    plt.close(fig)
    print('PNG, PDF and SVG rebuilt from the published aggregate results')


if __name__ == '__main__':
    main()
