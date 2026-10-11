"""Two deliverables from recorded validation and untouched final-test results."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import statistics
import time

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


LABELS = {'base': '基础策略', 'fixed': '固定策略', 'joint': '动态联合策略'}
COLORS = {'base': '#727b87', 'fixed': '#2879ad', 'joint': '#d66b32'}


def read(path):
    return json.loads(Path(path).read_text('utf-8'))


def save_markdown(path, lines):
    text = '\n'.join(lines)+'\n'
    assert text.count('```') % 2 == 0
    assert '\\(' not in text and '\\[' not in text
    assert sum(line.strip() == '$$' for line in text.splitlines()) % 2 == 0
    assert all(line.count('$') % 2 == 0 for line in text.splitlines() if line.strip() != '$$')
    Path(path).write_text(text, encoding='utf-8')


def main(root):
    root = Path(root).resolve()
    manifest = read(root/'manifest.json')
    cfg = manifest['config']
    seeds, arms = cfg['seeds'], cfg['arms']
    assert arms == ['base', 'fixed', 'joint'] and cfg['scenarios'] == ['raw']
    assert read(root/'suite_status.json')['state'] == 'complete'
    amendment = read(root/'scope_amendment.json')
    original = read(Path(amendment['source_study'])/'manifest.json')
    assert original['implementation_sha256'] == manifest['implementation_sha256']
    assert original['dependencies'] == manifest['dependencies']
    jobs = {phase: [read(root/f'runs/main/{phase}/raw/{seed}/summary.json') for seed in seeds]
            for phase in ['validation', 'test']}
    results = {(phase, arm): [j['arms'][arm] for j in jj]
               for phase, jj in jobs.items() for arm in arms}
    for records in results.values():
        for r in records:
            assert r['status'] == 'complete' and not r['attack_failures']
            assert r['cost']['initial_users'] == cfg['users']
            for model in ['pcfg', 'omen']:
                assert all(p['prefix_reached_requested_budget'] for p in r['actual_attacks'][model]['points'])
    out = root/'deliverables'
    figures = out/'figures'
    figures.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({'font.family': 'sans-serif',
                         'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'DejaVu Sans'],
                         'axes.unicode_minus': False, 'font.size': 10,
                         'axes.spines.top': False, 'axes.spines.right': False,
                         'pdf.fonttype': 42, 'svg.fonttype': 'none'})
    metrics = {}
    budget = cfg['objective_budget']
    bi = cfg['budgets'].index(budget)
    for phase in jobs:
        for arm in arms:
            rr = results[phase, arm]
            metrics[phase, arm] = {
                'A1_estimated': [100*r['A1_MC']['rates'][str(budget)] for r in rr],
                'PCFG_actual': [100*r['actual_attacks']['pcfg']['points'][bi]['rate_per_initial_user'] for r in rr],
                'OMEN_actual': [100*r['actual_attacks']['omen']['points'][bi]['rate_per_initial_user'] for r in rr],
                'union_equal_total': [100*r['actual_attacks']['union_equal_total_B']['points'][bi]['rate_per_initial_user'] for r in rr],
                'modification': [100*r['cost']['modification_rate'] for r in rr],
                'extra': [r['cost']['mean_extra_submissions'] for r in rr],
                'more_than_three': [100*r['cost']['more_than_three_fraction'] for r in rr],
                'outside': [100*r['A1_MC']['outside_model_support_fraction'] for r in rr],
                'updates': [r['cost']['policy_updates'] for r in rr],
            }
    def values(phase, arm, key):
        return metrics[phase, arm][key]
    def average(phase, arm, key):
        return statistics.mean(values(phase, arm, key))
    def formatted(phase, arm, key, digits=3):
        vv = values(phase, arm, key)
        return f'{statistics.mean(vv):.{digits}f} ± {statistics.stdev(vv):.{digits}f}'
    def difference(phase, reference, key):
        return [a-b for a, b in zip(values(phase, reference, key), values(phase, 'joint', key))]
    def save_figure(fig, name):
        fig.savefig(figures/f'{name}.png', dpi=220, bbox_inches='tight')
        fig.savefig(figures/f'{name}.pdf', bbox_inches='tight')
        fig.savefig(figures/f'{name}.svg', bbox_inches='tight')
        plt.close(fig)

    # Model-specific real attempts and equal-total two-model attempts are separate.
    fig, axes = plt.subplots(1, 3, figsize=(13.2, 4.0), constrained_layout=True)
    for ax, model, title in zip(axes, ['pcfg', 'omen', 'union_equal_total_B'],
                                ['PCFG 实际攻击', 'OMEN 实际攻击', '两模型联合（总预算相同）']):
        for arm in arms:
            matrix = np.array([[100*p['rate_per_initial_user'] for p in r['actual_attacks'][model]['points']]
                               for r in results['test', arm]])
            ax.errorbar(cfg['budgets'], matrix.mean(axis=0), yerr=matrix.std(axis=0, ddof=1),
                        color=COLORS[arm], marker='o', capsize=3, label=LABELS[arm])
        ax.set(xscale='log', xlabel='实际猜测次数（重复猜测计费）', ylabel='命中率（%）', title=title)
        ax.grid(alpha=.2)
    axes[0].legend(fontsize=9)
    save_figure(fig, '01_actual_attack_curves')

    fig, axes = plt.subplots(1, 2, figsize=(10.3, 4.2), constrained_layout=True)
    for ax, phase, title in zip(axes, ['validation', 'test'], ['验证阶段', '独立终测']):
        for i, key in enumerate(['A1_estimated', 'PCFG_actual', 'OMEN_actual', 'union_equal_total']):
            ds = difference(phase, 'fixed', key)
            ax.scatter(np.arange(len(seeds))+(i-1.5)*.13, ds, s=28,
                       label=['PCFG 估计', 'PCFG 实测', 'OMEN 实测', '联合实测'][i])
        ax.axhline(0, color='#555555', linewidth=.8)
        ax.axhline(100*cfg['min_gain'], color='#888888', linestyle='--', linewidth=.8, label='0.5 pp 选择门槛')
        ax.set(xticks=range(len(seeds)), xticklabels=seeds, xlabel='随机种子',
               ylabel='固定策略 − 动态策略（百分点）', title=title)
        ax.grid(axis='y', alpha=.2)
    axes[1].legend(fontsize=8, loc='upper left', bbox_to_anchor=(1.02, 1))
    save_figure(fig, '02_paired_improvement')

    fig, axes = plt.subplots(1, 3, figsize=(11.4, 4), constrained_layout=True)
    for ax, key, title, limit in zip(axes, ['modification', 'extra', 'more_than_three'],
                                    ['需修改口令的用户（%）', '人均额外提交次数', '额外提交超过 3 次（%）'],
                                    [100*cfg['max_modification'], cfg['max_extra'], 100*cfg['max_more_than_three']]):
        vv = [average('test', arm, key) for arm in arms]
        sd = [statistics.stdev(values('test', arm, key)) for arm in arms]
        ax.bar(range(3), vv, yerr=sd, capsize=3, color=[COLORS[a] for a in arms], width=.62)
        ax.axhline(limit, linestyle='--', color='#9e3646', linewidth=1, label='成本上限')
        ax.set(xticks=range(3), xticklabels=[LABELS[a] for a in arms], title=title,
               ylim=(0, max(limit*1.15, max(vv)*1.15)))
        ax.tick_params(axis='x', labelsize=9)
        for i, v in enumerate(vv):
            ax.annotate(f'{v:.3f}', (i, v), xytext=(0, 7), textcoords='offset points', ha='center', fontsize=9)
        ax.legend(fontsize=8)
    save_figure(fig, '03_user_costs')

    fig, axes = plt.subplots(2, 1, figsize=(9.0, 5.8), sharex=True, constrained_layout=True)
    for seed, r in zip(seeds, results['test', 'joint']):
        xx = np.arange(1, cfg['batches']+1)
        axes[0].step(xx, [t['exact_size'] for t in r['timeline']], where='post', label=f'seed {seed}')
        axes[1].step(xx, [t['shape_size'] for t in r['timeline']], where='post', label=f'seed {seed}')
    axes[0].set(ylabel='实际完整口令拦截数', title='独立终测中的策略调整轨迹')
    axes[1].set(ylabel='实际字符结构拦截数', xlabel='注册批次', xticks=np.arange(1, cfg['batches']+1))
    if all(t['shape_size'] == 0 for r in results['test', 'joint'] for t in r['timeline']):
        axes[1].set(ylim=(-.5, .5), yticks=[0])
        axes[1].text(.5, .78, '所有批次均选择 j = 0', transform=axes[1].transAxes,
                     ha='center', color='#555555')
    axes[0].legend(ncol=5, fontsize=8)
    for ax in axes:
        ax.grid(alpha=.2)
    save_figure(fig, '04_policy_timeline')

    csv_rows = []
    for phase in jobs:
        for arm in arms:
            for i, seed in enumerate(seeds):
                csv_rows.append({'phase': phase, 'seed': seed, 'arm': arm, 'initial_users': cfg['users'],
                                 **{key: vv[i] for key, vv in metrics[phase, arm].items()},
                                 'pending_users': results[phase, arm][i]['cost']['pending_users'],
                                 'eligible': not results[phase, arm][i]['eligibility_failures']})
    with (out/'per_seed_results.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(csv_rows[0]))
        writer.writeheader()
        writer.writerows(csv_rows)
    comparison = {}
    for phase in jobs:
        for reference in ['base', 'fixed']:
            for key in ['A1_estimated', 'PCFG_actual', 'OMEN_actual', 'union_equal_total']:
                ds = difference(phase, reference, key)
                comparison[f'{phase}/{reference}/{key}'] = {'paired_difference_pp': ds,
                    'mean_pp': statistics.mean(ds), 'sample_sd_pp': statistics.stdev(ds),
                    'positive_seeds': sum(d > 0 for d in ds), 'seeds': len(ds)}
    recommendation = read(root/'frozen_recommendations/raw.json')
    parameters = read(root/'frozen_parameters/raw.json')
    eligible = all(not r['eligibility_failures'] for r in results['test', 'joint'])
    primary = comparison['test/fixed/A1_estimated']
    gate = primary['mean_pp'] >= 100*cfg['min_gain'] and primary['positive_seeds'] >= 4 and eligible
    actual_consistent = all(comparison[f'test/fixed/{k}']['mean_pp'] > 0
                            for k in ['PCFG_actual', 'OMEN_actual', 'union_equal_total'])
    evidence_files = [root/'manifest.json', root/'scope_amendment.json', root/'data_manifest.json',
                      root/'audit.json', root/'frozen_parameters/raw.json', root/'frozen_recommendations/raw.json']
    for phase in jobs:
        evidence_files.extend(root/f'runs/main/{phase}/raw/{seed}/{arm}/result.json'
                              for seed in seeds for arm in arms)
    evidence = {'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                'numeric_verification': 'automated local consistency checks; human manuscript verification pending',
                'sources': [{'id': f'E{i+1:03}', 'path': str(p.relative_to(root)),
                             'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for i, p in enumerate(evidence_files)],
                'metrics': {f'{phase}/{arm}': m for (phase, arm), m in metrics.items()},
                'comparisons': comparison, 'selection': recommendation,
                'final_primary_gate_passed': gate, 'actual_direction_consistent': actual_consistent,
                'cost_eligible_all_test_seeds': eligible}
    (out/'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')

    all_shapes_zero = all(t['j'] == 0 for rr in results.values() for r in rr for t in r['timeline'])
    method = [
        '# 注册阶段动态策略：最终选取与方法', '',
        f'本次验证封存的推荐为 **{LABELS[recommendation["recommended_arm"]]}**。最终测试前完成封存，终测不参与参数或方法选择。', '',
        '## 核心机制', '',
        '按批次观察已完成注册账户的口令热点，联合选择完整口令拦截数 $k$ 和 ASCII 字符结构拦截数 $j$；候选必须满足注册成本与安全保护约束，并经独立预览确认后才能更新。', '',
        '每批 10,000 个用户，共 10 批。第一批统一采用最小长度 8 加训练集频数前 1,000 的公共拦截表。后续从最近两批已注册口令生成完整口令与字符结构热点；完整口令至少出现 2 次，结构至少出现 20 次。', '',
        r'候选取 $k\in\{0,10,100,1000\}$、$j\in\{0,5,20\}$ 的组合，并加入保持当前列表不变的候选，去除重复规则。字符结构使用连续字符类别及长度编码（大小写字母、数字、符号），不依赖现有 Top15 策略。', '',
        '## 风险目标与约束', '',
        '候选目标是历史账户与预测新批次的累计 PCFG 自适应攻击命中率：', '',
        '$$',
        r'\widehat R_t(c)=\frac{N_{t-1}\widehat r(H_{t-1};M_c)+n_t\widehat r(P_t^c;M_c)}{N_{t-1}+n_t}.',
        '$$', '',
        '其中 $H_{t-1}$ 为历史已注册口令分布，$P_t^c$ 为候选作用后的预览分布，$M_c$ 使用独立训练角色的模拟响应拟合。近期用户信息只通过训练字符串的结构权重进入训练，不将目标用户的明文口令复制到训练角色。', '',
        '选择与确认分别使用两个独立预览角色，每次各 3,000 个用户；初始提议按独立开发分布与过去批次偏好各 50% 混合，后续未到达批次不可见。', '',
        '各方法与候选共享不依赖策略的用户提议序列，随机性由用户出现位置和种子决定；最小长度填充使用确定性公共规则。候选不会通过额外随机生成高熵口令获得虚假的安全改善。', '',
        '| 约束或门槛 | 设置 |',
        '| --- | --- |',
        '| 修改口令比例 | 不超过 60% |',
        '| 人均额外提交 | 不超过 1 次 |',
        '| 额外提交超过 3 次的比例 | 不超过 5% |',
        '| 未完成注册 | 0 |',
        '| 训练集固定 PCFG 的 10 万次实际攻击命中率增量 | 相对保持当前规则不超过 0.5 个百分点 |',
        '| 累计模型外比例增量 | 相对保持当前规则不超过 2 个百分点 |',
        '| 最低累计预测改善 | 0.5 个百分点 |', '',
        '在可行候选中先最小化风险，再以预测碰撞、人均额外提交、列表规模和保持原规则偏好打破平局。候选改善不足 0.5 个百分点时保持规则；达到门槛时只确认单个赢家，确认失败不继续尝试其他候选；全部不可行时回退公共基础规则。', '',
        '选择阶段使用 10,000 次 Monte Carlo 抽样，确认与终测使用 100,000 次。Monte Carlo 预算指估计的不同字符串概率排名，不能与实际攻击次数直接混为同一指标。', '',
        '## 最终方法选取', '',
        f'固定策略的参数已在既有校准阶段封存为 $(k,j)=({parameters["fixed_pair"][0]},{parameters["fixed_pair"][1]})$；它在第二批建立热点列表，此后保持该列表。动态策略每批重新评估并允许保持原列表。', '',
        f'五次验证中，动态策略相对固定策略的平均主指标改善为 **{comparison["validation/fixed/A1_estimated"]["mean_pp"]:.4f} 个百分点**，方向一致 **{comparison["validation/fixed/A1_estimated"]["positive_seeds"]}/5**，满足预设的平均改善至少 0.5 个百分点且至少 4/5 同向的选择条件。随后封存该方法，再运行独立终测。', '',
        '最终选取的是完整的在线选择规则，不是选择表现最好的一次随机种子，也不是将某一条验证时间线直接套入终测。', '',
        ('当前验证和终测均选择 $j=0$。实际收益来自完整口令列表的动态调整，现有证据没有证明结构拦截的额外收益；联合搜索空间保留了自动选择零结构拦截的能力。' if all_shapes_zero else
         '实际选中的完整口令与结构数量见图 4 和逐种子记录；候选空间包含结构拦截并不意味着每批一定采用结构拦截。'), '',
        '## 本次范围', '',
        '因用户要求缩短运行时间，在终测打开前将实验缩减为原始分布场景、基础/固定/动态三组和五个种子；沿用校准与验证记录，仅补充独立终测。敏感性、消融、漂移场景和两项拓展未纳入本次结论。该范围修改在已看到部分验证结果后作出，属于明示的范围修订，不能表述为完整原计划全部完成。', '',
        '用户响应采用 60% 修补、25% 拼接、15% 独立重选的模拟假设。五次种子来自同源数据划分，可能共享用户出现位置，不能当作五个独立人群，也不能声称进行了真人注册实验。', '',
        '证据：scope_amendment.json、frozen_parameters/raw.json、frozen_recommendations/raw.json、逐种子 result.json；哈希与自动核验记录见 evidence.json。',
    ]
    method += ['', '## 终测中实际选取的规则', '',
               '| 种子 | 最终 $k$ | 最终 $j$ | 实际完整口令拦截数 | 实际结构拦截数 | 策略更新次数 |',
               '| --- | --- | --- | --- | --- | --- |']
    for seed, r in zip(seeds, results['test', 'joint']):
        final = r['timeline'][-1]
        method.append(f'| {seed} | {final["k"]} | {final["j"]} | {final["exact_size"]} | {final["shape_size"]} | {r["cost"]["policy_updates"]} |')
    method += ['', '此表记录在线方法在各次终测中实际选出的终态，不是预先固定所有批次均使用该终态。完整演化过程见验证报告图 4。']
    save_markdown(out/'01_最终方法.md', method)

    report = ['# 注册阶段动态策略：验证结果与图表', '',
              f'实验已完成：一个原始分布场景，基础/固定/动态三组，验证与独立终测各五次种子；每次每组 100,000 个初始用户，分 10 批注册。', '',
              '所有数值来自实际保存的模拟和攻击记录；百分数按初始用户数计算。下表为五种子的均值 ± 样本标准差，描述同源模拟的波动，不是独立人群置信区间。', '',
              '全局出现位置划分为训练 60,000、保留调参 20,000、选择预览 10,000、确认预览 10,000、验证储备 200,000、终测储备 200,000；保留调参角色未用于当前控制器。角色之间出现位置不重叠，同一字符串允许在不同角色出现。每个种子从对应验证或终测储备中无放回抽取 100,000 个出现位置，各种子之间可能重叠。终测前封存了对照参数与推荐方法。', '',
              '## 独立终测主结果', '',
              '| 方法 | PCFG 估计 @100万（%） | PCFG 实测 @100万（%） | OMEN 实测 @100万（%） | 联合实测总100万（%） |',
              '| --- | --- | --- | --- | --- |']
    for arm in arms:
        report.append('| '+LABELS[arm]+' | '+' | '.join(formatted('test', arm, k) for k in
                      ['A1_estimated', 'PCFG_actual', 'OMEN_actual', 'union_equal_total'])+' |')
    report += ['', 'PCFG 和 OMEN 两列各自使用 100 万次实际尝试，重复猜测消耗预算；联合列总预算为 100 万，两模型各 50 万。联合列不是“两模型各猜 100 万”的结果。', '',
               '| 动态相对固定 | 验证平均改善（百分点） | 终测平均改善（百分点） | 终测同向种子 |',
               '| --- | --- | --- | --- |']
    for key, label in [('A1_estimated', 'PCFG 估计'), ('PCFG_actual', 'PCFG 实测'),
                       ('OMEN_actual', 'OMEN 实测'), ('union_equal_total', '联合实测')]:
        v, t = comparison[f'validation/fixed/{key}'], comparison[f'test/fixed/{key}']
        report.append(f'| {label} | {v["mean_pp"]:.4f} | {t["mean_pp"]:.4f} | {t["positive_seeds"]}/5 |')
    report += ['', '改善定义为“固定策略命中率 − 动态策略命中率”；正值表示动态策略被猜中的比例更低。', '',
               '## 注册成本与覆盖', '',
               '| 方法 | 修改比例（%） | 人均额外提交 | 超过3次额外提交（%） | PCFG 模型外用户（%） |',
               '| --- | --- | --- | --- | --- |']
    for arm in arms:
        report.append('| '+LABELS[arm]+' | '+' | '.join(formatted('test', arm, k) for k in
                      ['modification', 'extra', 'more_than_three', 'outside'])+' |')
    report += ['', f'动态方法终测五个种子的成本约束状态：**{"全部通过" if eligible else "存在不通过记录"}**。未完成注册人数与逐项约束状态见 per_seed_results.csv。', '',
               '模型外口令没有被解释为已证实安全。PCFG 估计与实际攻击前缀的降低，只说明在本次两种攻击模型和有限预算下被命中的比例降低，不能推出无限预算或所有攻击器下的安全性。', '',
               '## 图表', '',
               '图 1：独立终测的 PCFG、OMEN 和等总预算联合攻击曲线；误差条为五种子的样本标准差。', '',
               '![图1 实际攻击曲线](figures/01_actual_attack_curves.png)', '',
               '图 2：各随机种子相对固定策略的配对改善；0.5 个百分点虚线仅对应方法选择的 PCFG 估计门槛，其他实测指标用于交叉核验。', '',
               '![图2 配对改善](figures/02_paired_improvement.png)', '',
               '图 3：注册成本与约束上限。', '',
               '![图3 用户成本](figures/03_user_costs.png)', '',
               '图 4：终测中实际生效的完整口令和结构拦截数；候选参数 $k$ 是数量上限，实际可用热点数可能小于 $k$。', '',
               '![图4 策略时间线](figures/04_policy_timeline.png)', '',
               '## 可采用的结论边界', '',
               (f'独立终测通过原主指标的改善与方向门槛：动态相对固定的平均 PCFG 估计命中率降低 {primary["mean_pp"]:.4f} 个百分点，{primary["positive_seeds"]}/5 个种子同向，且五次均满足注册成本约束。' if gate else
                f'独立终测未同时满足原主指标的改善、方向和成本门槛：平均 PCFG 估计改善 {primary["mean_pp"]:.4f} 个百分点，同向 {primary["positive_seeds"]}/5；应报告该结果，不能将方法宣称为已通过门槛。'), '',
               ('PCFG、OMEN 与等总预算联合攻击的平均实测方向均与主指标一致，为本次有限预算下的效果提供交叉支持。' if actual_consistent else
                '实测攻击的平均改善方向未全部一致；主指标与实测差异必须保留，不能只选择支持方法的指标。'), '',
               ('结构拦截在本次均未被选中，现有实验支持完整口令列表动态调整的效果，不能证明联合结构拦截优于仅完整口令调整。' if all_shapes_zero else
                '本次未执行结构消融对照，因此不能单独归因结构拦截的收益。'), '',
               '结论仅覆盖该同源数据、原始分布、给定用户响应假设和攻击预算。当前未完成漂移泛化、跨数据集、消融、敏感性与真人可用性验证。', '',
               '## 数据与核验', '',
               '来源和数值映射：evidence.json；逐次结果：per_seed_results.csv；执行及范围修订：上级目录的 manifest.json、scope_amendment.json；完整性与分母审计：上级目录 audit.json。全部图同时提供 PNG、PDF、SVG。', '',
               '本报告已进行本地自动核验，作为论文素材使用前仍需作者核对来源与最终表述。',
              ]
    save_markdown(out/'02_验证结果与图表.md', report)
    status = {'state': 'complete', 'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
              'method': str(out/'01_最终方法.md'), 'validation': str(out/'02_验证结果与图表.md'),
              'figures': 4, 'final_primary_gate_passed': gate, 'actual_direction_consistent': actual_consistent}
    (root/'deliverable_status.json').write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(status, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root')
    args = parser.parse_args()
    main(args.root)
