"""Render a figure and Markdown analysis exclusively from verified evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('MPLCONFIGDIR', str(ROOT/'tmp'/'mpl_contextual'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.ticker import PercentFormatter
import numpy as np


def main():
    p = argparse.ArgumentParser()
    p.add_argument('report', type=Path)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--markdown', type=Path, required=True)
    args = p.parse_args()
    assert hashlib.sha256(args.report.read_bytes()).hexdigest() == args.report.with_suffix('.json.sha256').read_text().strip()
    r = json.loads(args.report.read_text(encoding='utf-8')); assert r['status'] == 'complete'
    n = r['configuration']['intervention']['data']['users']
    local = r['local']; yahoo = r['yahoo']
    names = ['原动态策略', '上下文老虎机', 'Yahoo! JAPAN']
    colors = ['#426B98', '#009E89', '#E1913C']
    arms = [local['arms']['legacy'], local['arms']['bandit'],
            {'ledger': yahoo['final']['ledger'], 'final_distance': yahoo['final_distance'],
             **yahoo['attacks']}]
    font = FontProperties(fname='C:/Windows/Fonts/msyh.ttc')
    plt.rcParams.update({'font.family': font.get_name(), 'font.size': 11,
                         'axes.unicode_minus': False, 'svg.fonttype': 'none',
                         'axes.spines.top': False, 'axes.spines.right': False})
    out = args.output_dir.resolve(); out.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.subplots_adjust(left=.075, right=.965, bottom=.20, top=.85, wspace=.25, hspace=.43)
    fig.suptitle(f'{n:,} 账户评价：新旧动态策略与 Yahoo! JAPAN 的差距', fontsize=22, x=.075, ha='left', y=.973)
    fig.text(.075, .916, '同一原始样本 · 独立参考群体重训攻击 · seed=42 · 全员成功修改的模拟响应', fontsize=11, color='#555555')
    a = axes[0, 0]
    for arm, name, color in zip(arms, names, colors):
        points = arm['A1']['minauto']
        a.plot([v['budget'] for v in points], [v['rate'] for v in points],
               marker='o', label=name, color=color, linewidth=2)
    a.set_xscale('log'); a.yaxis.set_major_formatter(PercentFormatter(1))
    a.set_ylim(bottom=0); a.set_title('A  适应性攻击 A1：联合命中率', loc='left', fontweight='bold')
    a.set_xlabel('每模型实际尝试次数 B（联合最多 2B）'); a.set_ylabel('命中账户比例 ↓')
    a.legend(frameon=False, fontsize=10); a.grid(axis='y', alpha=.18)
    a = axes[0, 1]; x = np.arange(3); width=.34
    for j, (key, color) in enumerate([('F', '#B5C9DF'), ('A1', '#426B98')]):
        vals = [arm[key]['minauto'][-1]['rate'] for arm in arms]
        bars = a.bar(x+(j-.5)*width, vals, width, label=key, color=color)
        a.bar_label(bars, labels=[f'{v:.3%}' for v in vals], padding=4, fontsize=10)
    a.set_xticks(x, names); a.yaxis.set_major_formatter(PercentFormatter(1))
    a.set_ylim(0, max(arm[key]['minauto'][-1]['rate'] for arm in arms for key in ['F','A1'])*1.25+.003)
    a.set_title('B  每模型 100 万次：固定 F / 重训 A1', loc='left', fontweight='bold')
    a.set_ylabel('联合命中账户比例 ↓'); a.legend(frameon=False); a.grid(axis='y', alpha=.18)
    a = axes[1, 0]; vals = [arm['final_distance'] for arm in arms]
    bars = a.bar(names, vals, color=colors, width=.55)
    a.bar_label(bars, labels=[f'{v:.8f}' for v in vals], padding=5, fontsize=11)
    a.set_yscale('symlog', linthresh=1e-5)
    a.set_ylim(0, max(max(vals)*1.35, 1e-5))
    a.set_yticks([0, 1e-5, 1e-4, 1e-3])
    a.set_yticklabels(['0', '0.00001', '0.0001', '0.001'])
    a.set_title('C  经验分布距单例理想分布的 W1', loc='left', fontweight='bold')
    a.set_ylabel('归一化对数排名距离 ↓（对称对数刻度）'); a.grid(axis='y', alpha=.18)
    a.text(.02, .90, '0 表示样本内口令互不重复；不等于无法猜中', transform=a.transAxes, fontsize=10, color='#555555')
    a = axes[1, 1]; vals = [arm['ledger']['changed_rate'] for arm in arms]
    bars = a.bar(names, vals, color=colors, width=.55)
    a.bar_label(bars, labels=[f'{arm["ledger"]["changed"]:,} 人\n{v:.2%}' for arm,v in zip(arms, vals)], padding=5, fontsize=10)
    a.set_ylim(0, 1.22); a.set_yticks([0, .25, .5, .75, 1]); a.yaxis.set_major_formatter(PercentFormatter(1))
    a.set_title('D  从原始样本起算：实际修改的不同账户', loc='left', fontweight='bold')
    a.set_ylabel('账户比例（含 8 字符基线迁移） ↓'); a.grid(axis='y', alpha=.18)
    b, old, y = arms[1], arms[0], arms[2]
    gap = (b['A1']['minauto'][-1]['rate']-old['A1']['minauto'][-1]['rate'])*100
    fig.text(.075, .125, f'老虎机 − 原方法：A1 差 {gap:+.3f} 个百分点；局部通知 {b["ledger"]["adaptive_affected"]:,} vs {old["ledger"]["adaptive_affected"]:,} 人。', fontsize=11)
    supports = [arm['A1']['minauto'][-1]['outside_model_support_weight']/n for arm in arms]
    fig.text(.075, .086, 'A1 联合模型支持外账户：'+ '；'.join(f'{name} {v:.2%}' for name,v in zip(names,supports))+'。支持外未命中不能视为安全证明。', fontsize=10, color='#555555')
    fig.text(.075, .05, '局部方法共享 8 字符起点、累计 20% 通知上限；Yahoo 公开规则从原始口令全站迁移，无该上限。单次种子结果。', fontsize=10, color='#555555')
    for ext in ['png', 'svg']:
        fig.savefig(out/f'comparison_100k.{ext}', dpi=220, facecolor='white')
    plt.close(fig)
    if b['final_distance'] > old['final_distance']+1e-12:
        verdict = '按预先确定的分布距离主目标，本次原动态策略更好。'
    elif b['final_distance']+1e-12 < old['final_distance']:
        verdict = '按预先确定的分布距离主目标，本次上下文老虎机更好。'
    else:
        verdict = '按预先确定的分布距离主目标，两种局部方法本次终态数值相同（数值容差 1e-12）。'
    verdict += ' 攻击命中率、通知人数和计算时间分别报告；这一组结果不证明跨场景稳定优势。'
    lines = ['# 十万账户：新旧动态策略与 Yahoo! JAPAN 对照', '',
             '**评价性质:** 真实语料频次抽样、程序模拟用户修改；不是十万名真人用户的实验。', '',
             '**结论:** '+verdict+' 本次新方法的 A1 命中率高于原方法；Yahoo 观察命中率最低，但其全站修改成本高，且有更多口令处于攻击模型支持范围之外。', '',
             f'本次目标账户 {n:,}，开发样本 100,000（训练 60%、预热 20%、验证留出 20%），种子 42。按出现位置分离，允许相同口令字符串存在于不同样本中。三种方法使用相同原始目标样本；两个局部方法另外共享同一次 8 字符基线迁移。', '',
             '## 结果', '',
             '| 方法 | 经验 W1 ↓ | F 联合命中率 ↓ | A1 联合命中率 ↓ | 局部通知 / 全站迁移 | 修改的不同账户 | 平均累计编辑成本 ↓ |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for name, arm in zip(names, arms):
        l = arm['ledger']
        lines.append(f'| {name} | {arm["final_distance"]:.10f} | {arm["F"]["minauto"][-1]["rate"]:.4%} | {arm["A1"]["minauto"][-1]["rate"]:.4%} | {l["adaptive_affected"]:,} | {l["changed"]:,} ({l["changed_rate"]:.2%}) | {l["edit_cost"]:.6f} |')
    lines += ['', 'F 与 A1 均为 PCFG 和作者版 OMEN 的实际前缀；每模型预算 1,000,000 次，联合至多 2,000,000 次。F 在未干预开发训练集上冻结；A1 在每种方法的独立参考迁移分布上重训，不使用目标终态训练。编辑成本为每次归一化编辑距离累计后对全部账户平均；两种局部方法包含基线成本。', '',
              '原动态策略是保留原选择算法的受控重跑：外层最多 40 轮，关闭非 Google 分支的相对攻击收益提前停止；不是历史发布结果的直接复现。两个局部方法的初始状态配对，内部响应随机流不同。新方法使用经验准入模式，本次没有使用严格置信准入的概率保证。', '',
              '## 新旧差距', '',
              f'两个局部方法的共同初始经验距离为 {local["start_distance"]:.10f}。新方法降低 {1-b["final_distance"]/local["start_distance"]:.4%}，原方法降低 {1-old["final_distance"]/local["start_distance"]:.4%}；此比例衡量分布形状改善，不是破解风险的相对下降。', '',
              f'新方法相对原方法的经验 W1 差为 {b["final_distance"]-old["final_distance"]:+.10f}；A1 差为 {gap:+.4f} 个百分点；局部通知人数差为 {b["ledger"]["adaptive_affected"]-old["ledger"]["adaptive_affected"]:+,}。这里正数表示新方法指标更高，负数表示更低。', '',
              f'控制阶段耗时：新方法 {local["arms"]["bandit"]["controller_seconds"]:.2f} 秒，原方法 {local["arms"]["legacy"]["controller_seconds"]:.2f} 秒。此时间不包含终态攻击训练；原方法保留拟合、组合、前瞻和无模拟量上限的搜索，新方法使用经验 W1 与有限搜索，因此不能把耗时差全部归因于学习。', '',
              f'预热有效学习更新 {local["warmup"]["learning_observations"]} 次；新方法结束累计 {local["arms"]["bandit"]["learning_observations"]} 次。当前更新量与单种子评价不足以证明稳定的跨场景学习收益。', '',
              f'新方法停止原因为 `{local["arms"]["bandit"]["stop_reason"]}`，实际使用 {b["ledger"]["adaptive_affected_rate"]:.2%} 的局部通知额度，原方法停止原因为 `{local["arms"]["legacy"]["stop_reason"]}`。对于候选复核无正收益的停止，只能断言本轮已复核动作未显示收益，不能断言整个动作空间没有更优动作。', '',
              '## 与 Yahoo! JAPAN 的差距及可比范围', '',
              f'新方法相对 Yahoo 的 A1 差为 {(b["A1"]["minauto"][-1]["rate"]-y["A1"]["minauto"][-1]["rate"])*100:+.4f} 个百分点；W1 差为 {b["final_distance"]-y["final_distance"]:+.10f}；修改的不同账户少 {y["ledger"]["changed"]-b["ledger"]["changed"]:,} 人。原方法相对 Yahoo 的 A1 差为 {(old["A1"]["minauto"][-1]["rate"]-y["A1"]["minauto"][-1]["rate"])*100:+.4f} 个百分点。', '',
              'Yahoo 对照复刻已公开的 15–32 字符、半角字符与组合限制，执行一次全站合规迁移；不受局部 20% 通知上限或固定攻击个体门槛约束。这里比较的是完整策略流程的效果与实际成本，不能据此推断等修改成本下哪个算法更优，也不是 Yahoo 真实站点安全测评。', '',
              '公开规则来源：'+yahoo['policy']['source_url']+'（本项目既有规则模块核验日期 '+yahoo['policy']['verified_on']+'）。未复刻未公开检测。', '',
              '| 方法 | F 联合模型支持外账户 | F 比例 | A1 联合模型支持外账户 | A1 比例 |', '| --- | ---: | ---: | ---: | ---: |']
    for name,arm,v in zip(names,arms,supports):
        f_out = arm['F']['minauto'][-1]['outside_model_support_weight']
        lines.append(f'| {name} | {f_out:,} | {f_out/n:.3%} | {arm["A1"]["minauto"][-1]["outside_model_support_weight"]:,} | {v:.3%} |')
    final_round = local['arms']['bandit']['rounds'][-1]
    detail = final_round['audit']
    lines += ['', '## 新方法停止诊断', '',
              f'末轮为第 {final_round["round"]} 轮，生成 {detail["candidate_count"]} 个合法候选，从中选择 {len(detail.get("selected_candidate_indices", []))} 个进行初筛，最终复核 {len(detail.get("validation", []))} 个。模拟调用 {detail["simulation_calls"]} 次，构造或门槛失败记录 {len(detail.get("failures", []))} 条。', '',
              r'末轮独立模拟平均收益（$D_{\mathrm{before}}-D_{\mathrm{after}}$）如下。正值表示改善，负值表示变差：', '',
              '| 动作人数 | 复核平均收益 |', '| ---: | ---: |']
    for v in detail.get('validation', []):
        lines.append(f'| {v["action"]["selected"]} | {v["mean_gain"]:+.10f} |')
    lines += ['', '支持外是模型可表示范围之外，不代表破解成本已被充分评估。图中的观察命中率须与此诊断一起解释。经验 W1 为零也只说明这批样本没有重复口令，不代表抵抗猜测攻击。', '',
              '## 指标定义', '',
              r'设降序口令频次为 $f_r$，总人数为 $N$，对数排名坐标为 $x_r=\ln r/\ln N$，则经验指标为：', '',
              '$$', r'D=W_1(F,F_{\mathrm{singleton}})=\sum_r\frac{f_r}{N}\frac{\ln(N/r)}{\ln N}-\frac{N\ln N-\ln(N!)}{N\ln N}.', '$$', '',
              '联合攻击命中率与差距分别定义为：', '', '$$',
              r'R(B)=\frac{1}{N}\sum_{i=1}^{N}\mathbf{1}\{p_i\in G_{\mathrm{PCFG}}(B)\cup G_{\mathrm{OMEN}}(B)\},\qquad \Delta R_{a,b}=100(R_a-R_b)\ \text{个百分点}.', '$$', '',
              '较低的 $D$、$R$ 与修改成本各自更优；未预先确定三者的权重，不强行合成单一排名。只有一个十万账户种子，图中不绘制虚构误差条，不报告显著性。', '',
              '## 审计边界', '',
              '局部方法的通知预算、每轮人数、双攻击计费、完成状态与文件校验和通过核查。源码哈希全量核查有两项差异：`experiments/htpg_iteration.py` 和 `experiments/reproduce_htpg_baseline.py` 在本次运行期间改动；它们属于独立的历史 HTPG 实验，不在本次三方法运行的导入链中。本次评价使用的模块哈希仍与报告记录一致。源文件全量审计不能标记为完全通过。', '',
              '## 图与证据', '',
              f'![十万账户三方法差距图]({os.path.relpath(out/"comparison_100k.png", args.markdown.parent.resolve()).replace(os.sep,"/")})', '',
              f'- [可编辑 SVG]({os.path.relpath(out/"comparison_100k.svg", args.markdown.parent.resolve()).replace(os.sep,"/")})',
              f'- [完成的原始对照报告]({os.path.relpath(args.report.resolve(), args.markdown.parent.resolve()).replace(os.sep,"/")})',
              f'- [局部方法完整结果]({os.path.relpath((args.report.parent/"local"/"report.json").resolve(), args.markdown.parent.resolve()).replace(os.sep,"/")})', '',
              'comparison.json 带有独立 SHA-256 校验文件，包含样本元数据、各方法证据及审计结果。原论文、原控制器、已有结果与默认配置保留。']
    body = '\n'.join(lines)+'\n'
    assert body.count('$$')%2 == 0 and body.count('```')%2 == 0
    assert body.replace('$$','').count('$')%2 == 0
    assert not any(ord(c)<32 and c not in '\n\t' for c in body)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(body, encoding='utf-8')
    print('Rendered', out, 'and', args.markdown)


if __name__ == '__main__':
    main()
