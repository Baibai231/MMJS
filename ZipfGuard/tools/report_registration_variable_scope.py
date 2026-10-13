"""Audit the revised single arm; reuse paired comparator evidence, no training."""
from pathlib import Path
from collections import Counter
import argparse, csv, hashlib, json, sys, math
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.registration_joint_data import load_private
from tools.report_registration_dual_full import audit as audit_dual, METHODS as ORIGINAL_METHODS
METHODS = [(a, '本作品调整方案' if a == 'graded' else label, color, marker, style)
           for a, label, color, marker, style in ORIGINAL_METHODS]

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def summarize(arm, label, result, state):
    attempts = state['attempts'];total = result['cost']['initial_users']
    assert len(attempts) == total and math.isclose(sum(attempts)/total, result['cost']['mean_extra_submissions'], rel_tol=0, abs_tol=1e-12)
    assert math.isclose(sum(a > 3 for a in attempts)/total, result['cost']['more_than_three_fraction'], rel_tol=0, abs_tol=1e-12)
    rates = {m: result['actual_attacks'][m]['points'][-1]['rate_per_initial_user']
             for m in ('pcfg', 'omen', 'union_each_model_B', 'union_equal_total_B')}
    ordered = sorted(attempts)
    return {'arm': arm, 'label': label, 'users': total, 'per_model_budget': 1000000,
            **rates, 'extra': result['cost']['mean_extra_submissions'],
            'modification': result['cost']['modification_rate'],
            'more_than_three': result['cost']['more_than_three_fraction'],
            'extra_zero': sum(a == 0 for a in attempts)/total,
            'extra_one': sum(a == 1 for a in attempts)/total,
            'extra_two': sum(a == 2 for a in attempts)/total,
            'extra_three': sum(a == 3 for a in attempts)/total,
            'extra_max': max(attempts), 'extra_p90': ordered[int(.9*total)],
            'extra_p95': ordered[int(.95*total)], 'pending': result['cost']['pending_users'],
            'outside_both': result['final_dual_risk']['outside_model_support_fraction']}

def plot(dest, cfg, results):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    for family in ('Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC'):
        if any(f.name == family for f in font_manager.fontManager.ttflist):
            plt.rcParams['font.sans-serif'] = [family]
            break
    plt.rcParams.update({'font.size': 10, 'axes.unicode_minus': False, 'pdf.fonttype': 42, 'svg.fonttype': 'none'})
    fig, axes = plt.subplots(2, 2, figsize=(10.8, 7.8))
    fig.subplots_adjust(left=.08, right=.96, top=.92, bottom=.24, wspace=.47, hspace=.44)
    fig.suptitle('10万模拟用户：两种攻击模型与注册成本', fontsize=15, y=.985)
    for ax, model, title in zip(axes.flat[:3], ('pcfg', 'omen', 'union_each_model_B'),
                              ('（a）PCFG', '（b）马尔可夫模型（OMEN）', '（c）两模型联合命中')):
        for arm, label, color, marker, style in METHODS:
            points = results[arm]['actual_attacks'][model]['points']
            ax.plot([p['budget'] for p in points], [100*p['rate_per_initial_user'] for p in points],
                    color=color, marker=marker, linestyle=style, label=label, linewidth=1.8, markersize=4.5)
        ax.set(xscale='log', xlabel='每模型实际猜测次数' if model == 'union_each_model_B' else '实际猜测次数',
               ylabel='猜中账户比例（%）', title=title)
        ax.set_xticks(cfg['budgets'], ['1千', '1万', '10万', '100万'])
        ax.set_ylim(bottom=0)
        ax.grid(axis='y', alpha=.2)
    ax = axes[1, 1]
    values = [results[arm]['cost']['mean_extra_submissions'] for arm, *_ in METHODS]
    ax.barh(range(5), values, color=[m[2] for m in METHODS], height=.58)
    ax.set_yticks(range(5), [m[1] for m in METHODS], fontsize=8.5)
    ax.invert_yaxis()
    ax.set(xlabel='人均额外提交次数', title='（d）注册负担（仅统计，无硬上限）')
    ax.set_xlim(0, max(values)*1.24)
    for i, value in enumerate(values):
        ax.text(value+max(values)*.02, i, f'{value:.4f}', va='center', fontsize=9)
    ax.grid(axis='x', alpha=.15)
    for ax in axes.flat:
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.set_axisbelow(True)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='lower center', bbox_to_anchor=(.5, .075), ncol=3, frameon=False, fontsize=9)
    fig.text(.5, .02, '联合曲线每模型各使用横轴所示次数，合计最多两倍；同一账户只计一次。调整方案重跑一次，四个对照组复用相同样本。',
             ha='center', fontsize=9, color='#505B65')
    for suffix in ('pdf', 'png', 'svg'):
        fig.savefig(dest/f'registration_variable_five_policy_comparison.{suffix}', dpi=240, facecolor='white')
    plt.close(fig)

def main():
    parser = argparse.ArgumentParser();parser.add_argument('root', type=Path)
    root = parser.parse_args().root.resolve()
    cfg = json.loads((root/'manifest.json').read_text('utf-8'))['config']
    assert cfg['arms'] == ['graded'] and cfg['user_cost_caps_disabled']
    assert all(cfg[k] is None for k in ('max_modification', 'max_extra', 'max_more_than_three', 'target_retry_cap', 'patience'))
    old = Path(cfg['comparison_source'])
    assert digest(root/'private/partition.pkl.gz') == digest(old/'private/partition.pkl.gz') == cfg['paired_partition_sha256']
    new_results, new_records = audit_dual(root, cfg)
    for audit_path in sorted((root/'runs/11/graded/joint').glob('candidate_audit_*.json')):
        a = json.loads(audit_path.read_text('utf-8'))
        prohibited = {'modification_rate', 'mean_extra_submissions', 'more_than_three_fraction'}
        assert all(not prohibited.intersection(row.get('failures', [])) for row in a['candidates'])
        if a.get('confirmation'):
            assert not prohibited.intersection(a['confirmation']['failures'])
    state_new = load_private(root/'runs/11/graded/joint/state.pkl.gz')
    assert all(c['terminal_state'] == 'continue_until_accepted_no_retry_cap' for c in state_new['costs'])
    old_cfg = json.loads((old/'manifest.json').read_text('utf-8'))['config']
    old_results, old_records = audit_dual(old, old_cfg)
    results = {a: new_results['graded'] if a == 'graded' else old_results[a] for a, *_ in METHODS}
    rows = []
    for arm, label, *_ in METHODS:
        path = (root if arm == 'graded' else old)/'runs/11'/arm
        state = load_private(path/('joint/state.pkl.gz' if arm == 'graded' else 'state.pkl.gz'))
        rows.append(summarize(arm, label, results[arm], state))
    old_state = load_private(old/'runs/11/graded/joint/state.pkl.gz')
    rows.append(summarize('graded_previous', '本作品此前限成本方案', old_results['graded'], old_state))
    dest = root/'deliverables';dest.mkdir(exist_ok=True)
    with (dest/'million_attempt_summary.csv').open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    plot(dest, cfg, results)
    new, prior = rows[0], rows[-1]
    lines = ['# 可调阻断范围、无修改上限方案：完整运行对照', '',
             '本次只运行调整方案：同一批100,000条模拟用户出现记录，10批注册，seed 11。四个对照组复用2026-10-11完整运行的结果，开发训练、预览角色和目标出现记录完全相同。', '',
             '本次根据上一轮结果调整，并使用相同用户重新运行，适合比较本轮调整前后的表现；尚不能说明换一批用户仍有相同提升。修改比例、平均额外提交次数和超过三次的比例不再作为硬约束，单用户重选继续至接受，负担全部报告。其他选择规则、再次确认和风险检查逻辑未调整。', '',
             '| 方法 | PCFG猜中率 | OMEN猜中率 | 联合猜中率 | 人均额外提交 | 修改比例 | 超过3次比例 |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in rows:
        lines.append(f'| {r["label"]} | {r["pcfg"]:.3%} | {r["omen"]:.3%} | {r["union_each_model_B"]:.3%} | {r["extra"]:.5f} | {r["modification"]:.3%} | {r["more_than_three"]:.3%} |')
    lines += ['', 'PCFG与OMEN各实际猜测1,000,000次，联合最多2,000,000次；重复猜测计入预算，联合命中同一账户只计一次。', '',
              f'相较此前限成本方案，联合猜中率变化为{100*(new["union_each_model_B"]-prior["union_each_model_B"]):+.3f}个百分点，人均额外提交变化为{new["extra"]-prior["extra"]:+.5f}次。覆盖范围与成本限制同时改变，不能单独归因。', '',
              '| 方法 | 0次 | 1次 | 2次 | 3次 | 超过3次 | 最大次数 |', '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in rows:
        lines.append(f'| {r["label"]} | {r["extra_zero"]:.3%} | {r["extra_one"]:.3%} | {r["extra_two"]:.3%} | {r["extra_three"]:.3%} | {r["more_than_three"]:.3%} | {r["extra_max"]} |')
    timeline = new_results['graded']['timeline']
    lines += ['', '| 注册批次 | 完整口令阻断数 | 整类结构阻断数 | 选择性结构数 | 选择性覆盖范围（每模型） |',
              '| --- | ---: | ---: | ---: | ---: |']
    for i, r in enumerate(timeline, 1):
        scope = r.get('risk_membership_budget_per_model', 0) if r.get('soft_shape_size', 0) else '不适用'
        lines.append(f'| {i} | {r["exact_size"]} | {r["shape_size"]} | {r.get("soft_shape_size",0)} | {scope} |')
    lines += ['', '候选池保留完整口令和整类结构阻断，同时加入每模型前5万、10万、100万次猜测覆盖范围的结构内选择性阻断。上表报告实际启用的规则；候选提供某种规则不代表最终选中了该规则。', '']
    lines += ['', 'Segreti等的真人研究发现，一些结构策略平均增加0.58—1.58次创建尝试，多数条件下没有显著增加创建时间或负面感受，最极端结构限制条件存在例外。文献支持继续观察修改负担，不能证明本次模拟策略没有沮丧或退出风险。定位：论文印刷页2，Introduction末尾。', '',
              '[Segreti等，Diversify to Survive，SOUPS 2017，原文](https://www.usenix.org/system/files/conference/soups2017/soups2017-segreti.pdf)', '',
              '只有一次模拟，未测真人情绪、记忆或退出行为；对照为共同模拟条件下的机制适配，GitHub只复现公开核心规则。', '',
              '## 写作与核验工具', '',
              '本对照文档的写作与证据核验采用 scientific-writing 技能。流程工具来源：Kassis, T., Agarwal, V., He, Y., Patel, D., and Brueckner, A. M. (2026). Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents. [arXiv:2609.00065](https://doi.org/10.48550/arXiv.2609.00065)。该来源用于说明工具使用，不作为口令策略有效性的证据。', '']
    text = '\n'.join(lines)
    assert text.count('```') % 2 == 0 and text.count('$$') % 2 == 0
    (dest/'完整运行对照.md').write_text(text, encoding='utf-8')
    provenance = {'status': 'passed', 'new_evidence': new_records, 'reused_evidence': old_records,
                  'same_target_and_development_partition': True, 'partition_sha256': cfg['paired_partition_sha256'],
                  'rerun_arms': ['graded'], 'reused_arms': [a for a, *_ in METHODS if a != 'graded'],
                  'analysis_type': 'post-hoc paired descriptive revision', 'user_cost_caps': None,
                  'human_verification': 'pending', 'source_audits': {str(p): digest(p) for p in [root/'audit.json', old/'audit.json']},
                  'report_code_sha256': digest(Path(__file__)),
                  'outputs': {p.name: digest(p) for p in dest.iterdir() if p.suffix in ('.csv', '.pdf', '.png', '.svg', '.md')}}
    (dest/'comparison_evidence.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'audit': 'passed', 'summary': rows, 'artifacts': str(dest)}, ensure_ascii=False))

if __name__ == '__main__':
    main()
