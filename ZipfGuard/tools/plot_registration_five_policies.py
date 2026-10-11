"""Render the requested five-policy comparison from completed first-round results.

This is a descriptive figure export, with no model fitting or new experiments.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

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


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    scope = json.loads((root / 'single_scope.json').read_text('utf-8'))
    cfg = json.loads((root / 'manifest.json').read_text('utf-8'))['config']
    audit = json.loads((root / 'single_audit.json').read_text('utf-8'))
    assert audit['status'] == 'passed' and len(audit['additional_recounts']) == 10
    seed, users = scope['retained_seed'], cfg['users']
    assert users == 100000
    dest = root / 'single_deliverables/five_policy_comparison'
    dest.mkdir(parents=True, exist_ok=True)
    source_records, results, rows = [], {}, []
    for index, (arm, label, color, marker, style) in enumerate(METHODS, 1):
        path = root / 'runs' / str(seed) / arm / 'result.json'
        result = json.loads(path.read_text('utf-8'))
        assert result['status'] == 'complete' and not result['attack_failures']
        assert result['cost']['completed_users'] == users and not result['cost']['pending_users']
        points = result['actual_attacks']['union_equal_total_B']['points']
        assert [p['budget'] for p in points] == cfg['budgets']
        for point in points:
            assert point['initial_users'] == users
            assert point['per_model_budget'] * 2 == point['budget']
            assert abs(point['hits'] / users - point['rate_per_initial_user']) < 1e-12
            rows.append({'arm': arm, 'method': label, 'seed': seed, 'users': users,
                         'total_joint_budget': point['budget'], 'budget_per_model': point['per_model_budget'],
                         'joint_hits': point['hits'], 'joint_hit_pct': 100 * point['rate_per_initial_user'],
                         'mean_extra_submissions': result['cost']['mean_extra_submissions'],
                         'modification_pct': 100 * result['cost']['modification_rate'],
                         'more_than_three_retries_pct': 100 * result['cost']['more_than_three_fraction']})
        results[arm] = result
        source_records.append({'evidence_id': f'E{index:03d}', 'type': 'local_simulation_result',
                               'path': str(path), 'sha256': digest(path),
                               'locators': ['actual_attacks.union_equal_total_B.points', 'cost'],
                               'automated_recount': 'passed in single_audit.json',
                               'human_verification': 'pending'})
    with (dest / '五方案作图数据.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)

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
    plt.close(fig)

    def risk(arm, budget):
        return next(p['rate_per_initial_user'] * 100 for p in
                    results[arm]['actual_attacks']['union_equal_total_B']['points'] if p['budget']==budget)

    primary, secondary = cfg['primary_observed_budget'], cfg['secondary_observed_budget']
    ours, strict, malone, segreti, github = [risk(m[0], primary) for m in METHODS]
    caption = ('图 X　全量10万模拟用户下五种策略的攻击效果与注册成本。'
               '（a）PCFG与OMEN的联合猜中比例，横轴为两模型合计实际猜测次数，各模型分配一半预算；'
               '猜中同一账户只计一次，重复猜测仍计入预算。'
               '（b）用户第一次提交之后，人均额外提交次数。Schechter采用本轮严格档；'
               'Segreti为统一模拟条件下的结构黑名单机制适配；GitHub仅使用公开长度与字符要求。'
               '每种策略分10批注册，仅展示第一轮结果，没有跨轮次误差条。')
    paragraphs = [
        '图X比较了我们的改进方案、Schechter严格档、Malone–Maher、Segreti结构黑名单以及GitHub公开核心规则。'
        '各方案均使用同一批10万模拟用户，分10批注册。攻击评价采用PCFG与OMEN联合猜测，'
        '两模型各分配一半总预算；用户成本采用首次提交后的人均额外提交次数。',
        f'在合计10万次猜测下，我们方案的猜中比例为{ours:.3f}%，低于Schechter的{strict:.3f}%、'
        f'Malone–Maher的{malone:.3f}%和GitHub公开核心规则的{github:.3f}%，'
        f'分别下降{strict-ours:.3f}、{malone-ours:.3f}和{github-ours:.3f}个百分点。'
        '在合计100万次猜测下，我们的方案仍低于上述三个对照。'
        f'我们方案的人均额外提交次数为{values[0]:.4f}次，与Schechter的{values[1]:.4f}次接近，'
        '说明本轮相较Schechter的安全效果改善只增加了较少的提交负担。',
        f'Segreti的猜中比例更低：合计10万次猜测下为{segreti:.3f}%，'
        f'我们的方案比它高{ours-segreti:.3f}个百分点；合计100万次猜测下，二者分别为'
        f'{risk("graded",secondary):.3f}%和{risk("segreti_struct_m_nohint",secondary):.3f}%，'
        f'相差{risk("graded",secondary)-risk("segreti_struct_m_nohint",secondary):.3f}个百分点。'
        f'但Segreti的人均额外提交次数达到{values[3]:.4f}次，我们的方案降低了{reduction:.1f}%。'
        '因此，本轮结果支持的优势是：与Schechter、Malone–Maher和网站公开规则相比，我们的方案在主要攻击预算下有更低的猜中比例；'
        '与Segreti相比，则以一定的安全效果差距换取大幅降低的注册负担，提供了另一种安全性与使用成本之间的选择。'
        '这些结论来自一次模拟，不表示安全效果与Segreti等同，也不代表原论文真人实验的全部结果。',
    ]
    prose = '# 五方案对照：图注与结果论述\n\n## 图注\n\n' + caption + '\n\n## 结果论述\n\n' + '\n\n'.join(paragraphs) + '\n'
    assert prose.count('```') % 2 == 0 and prose.count('$$') % 2 == 0
    (dest / '五方案对照论述.md').write_text(prose, encoding='utf-8')
    alt_text = ('左图展示五种策略在1千至100万次两模型联合猜测下的猜中比例。'
                '10万与100万次预算下，改进方案低于Schechter、Malone–Maher和GitHub，高于Segreti。'
                '右图展示人均额外提交次数，Segreti最高，改进方案与Schechter接近。')
    explanation = ('# 五方案图表说明\n\n'
                   '本图直接读取已完成第一轮的最终结果，没有重新训练攻击模型或增加实验。'
                   '按用户要求展示五个指定配置；Schechter保留此前严格档，不按本图重新调参。\n\n'
                   '## 统计口径\n\n'
                   '- 每种方案均为同一批10万模拟用户，第一轮 seed 11，分10批注册。\n'
                   '- 曲线使用两个攻击器各占一半预算的联合结果，并按账户去重，未使用单模型结果替代。\n'
                   '- 成本为总人均额外提交次数，包含基础要求造成的重选；没有减去不同方案的基础成本。\n'
                   '- GitHub公开核心规则与文献组基础长度及黑名单要求不同，图中比较的是这五个完整配置。\n'
                   '- 图中保留全部已设四档联合预算，并保留Segreti较低的猜中比例。\n'
                   '- 只展示一次模拟，不绘制重复实验误差条、不做显著性或等效性声明。\n\n'
                   '## 替代文本\n\n' + alt_text + '\n\n'
                   '## 数据来源\n\n'
                   '证据E001—E005对应五份最终 result.json，路径、文件哈希和字段位置记录在 figure_manifest.json。'
                   '原 single_audit.json 已对全部10种配置重新核对联合猜中比例并通过。'
                   '本图的数据导出见五方案作图数据.csv；作者人工核验状态仍为待核验。\n\n'
                   '## 写作工具来源\n\n'
                   '本次图注与结果论述参考 scientific-writing skill 的证据追溯与数字一致性要求。'
                   '工具来源：Kassis, T., Agarwal, V., He, Y., Patel, D., & Brueckner, A. M. (2026). '
                   'Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents. '
                   '[arXiv:2609.00065](https://doi.org/10.48550/arXiv.2609.00065)。'
                   '当前文献记录为2026-09-02修订版，元数据已读取；该引用说明写作工具来源，不支撑本项目实验结论。\n')
    (dest / '五方案图表说明.md').write_text(explanation, encoding='utf-8')
    manifest = {'display_id': 'registration-five-policies-first-round-v1',
                'generated_at': datetime.now(timezone(timedelta(hours=8))).isoformat(),
                'source_evidence': source_records,
                'audit_path': str(root / 'single_audit.json'), 'audit_sha256': digest(root / 'single_audit.json'),
                'code_path': str(Path(__file__).resolve()), 'code_sha256': digest(Path(__file__).resolve()),
                'population': {'seed': seed, 'users_per_method': users, 'batches': cfg['batches'], 'repeats': 1},
                'analysis': 'descriptive user-requested display after completed experiments',
                'primary_budget': primary, 'secondary_budget': secondary,
                'denominator': 'all 100000 initial users; both attackers half joint budget',
                'cost': 'mean submissions after first submission, including base-rule burden',
                'claims': [{'claim_id': f'C{i:03d}', 'text_sha256': hashlib.sha256(p.encode()).hexdigest(),
                            'evidence_ids': [r['evidence_id'] for r in source_records],
                            'human_verification': 'pending'} for i,p in enumerate(paragraphs,1)],
                'creator': 'Codex automated export', 'human_reviewer': 'pending',
                'alt_text': alt_text, 'image_processing': 'matplotlib rendering of recorded values; no raster edits',
                'uncertainty': 'one simulation round; no across-round estimates',
                'external_visual_material': 'none',
                'outputs': {p.name: digest(p) for p in dest.iterdir() if p.is_file() and p.name != 'figure_manifest.json'}}
    (dest / 'figure_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(dest), 'primary_gap_to_segreti_pp': ours-segreti,
                      'extra_submission_reduction_pct': reduction}, ensure_ascii=False))


if __name__ == '__main__':
    main()
