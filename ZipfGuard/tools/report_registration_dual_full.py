"""Recount private evidence before exporting public aggregate results."""
from pathlib import Path
from collections import Counter
import argparse
import csv
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.registration_joint_data import load_private
from experiments.registration_dual_pipeline import DualAttackCache, risk, summed_cost, write_json
from core.corpus import counts_hash

METHODS = [
    ('graded', '我们的改进方案', '#27648C', 'o', '-'),
    ('schechter_strong', 'Schechter（严格）', '#BA7635', 's', '--'),
    ('malone', 'Malone–Maher', '#8A709A', '^', '-.'),
    ('segreti_struct_m_nohint', 'Segreti（整类结构）', '#698B72', 'D', ':'),
    ('github_core', 'GitHub 公开核心规则', '#879DAF', 'v', (0, (5, 2, 1, 2))),
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(root, cfg, partial=False):
    manifest = json.loads((root/'manifest.json').read_text('utf-8'))
    for relative, expected in manifest['source_hashes'].items():
        assert digest(ROOT/relative) == expected, f'Frozen implementation changed: {relative}'
    records, results = [], {}
    for arm in cfg['arms']:
        path = root/'runs'/str(cfg['seeds'][0])/arm
        source = path/'result.json'
        if not source.exists() and partial:
            continue
        r = json.loads(source.read_text('utf-8'))
        assert r['status'] == 'complete' and not r['attack_failures']
        state = load_private(path/('joint/state.pkl.gz' if arm == 'graded' else 'state.pkl.gz'))
        training = load_private(path/'developer_counts.pkl.gz')
        assert counts_hash(training) == r['A1_training_sha256']
        assert sum(training.values()) == r['A1_training_occurrences'] == 60000
        assert sum(state['history'].values()) == r['cost']['completed_users'] == cfg['users']
        assert r['cost']['pending_users'] == 0
        assert summed_cost(state['costs']) == r['cost']
        assert sum(state.get('final_batches', state.get('batches')), Counter()) == state['history']
        cache = DualAttackCache(root/'private/attacks'/str(cfg['seeds'][0])/arm, cfg, lambda m: None)
        # Fail if the report would need to create any missing attack evidence.
        prefix_paths = {m: cache.root/'prefixes'/f"{r['A1_training_sha256']}-{m}-{max(cfg['budgets'])}.pkl.gz"
                        for m in ('pcfg', 'omen')}
        assert all(p.exists() for p in prefix_paths.values())
        assert (cache.root/'grammars'/f"{r['A1_training_sha256']}.pkl.gz").exists()
        index = cache.index(training, purpose='final')
        assert risk(index, state['history'], cfg['budgets'], cfg['users']) == r['final_dual_risk']
        for model, curve in r['actual_attacks'].items():
            assert [p['budget'] for p in curve['points']] == cfg['budgets']
            for point in curve['points']:
                b = point['budget']
                if model in ('pcfg', 'omen'):
                    hits = sum(n for w, n in state['history'].items() if index.prefixes[model]['ranks'].get(w, b+1) <= b)
                    assert point['prefix_reached_requested_budget'] and point['charged_attempts'] == b
                else:
                    per = b if model == 'union_each_model_B' else b//2
                    assert point['per_model_budget'] == per and point['max_total_attempts'] == per*2
                    hits = sum(n for w, n in state['history'].items() if any(
                        p['ranks'].get(w, per+1) <= per for p in index.prefixes.values()))
                assert hits == point['hits'] and hits/cfg['users'] == point['rate_per_initial_user']
        candidate_audits = []
        if arm == 'graded':
            for p in sorted((path/'joint').glob('candidate_audit_*.json')):
                a = json.loads(p.read_text('utf-8'))
                for row in a['candidates']:
                    if 'predicted_A1' in row:
                        for key in ('history_actual_dual', 'preview_actual_dual'):
                            assert row[key]['models'] == ['pcfg', 'omen'] and row[key]['estimated'] is False
                if a.get('confirmation'):
                    for name in ('reference', 'winner'):
                        for key in ('history_actual_dual', 'preview_actual_dual'):
                            assert a['confirmation'][name][key]['models'] == ['pcfg', 'omen']
                            assert a['confirmation'][name][key]['estimated'] is False
                candidate_audits.append({'file': p.name, 'sha256': digest(p), 'status': a['status']})
            assert len(candidate_audits) == cfg['batches']-1
        records.append({'evidence_id': f'E{len(records)+1:03d}', 'arm': arm,
                        'result_path': str(source), 'result_sha256': digest(source),
                        'training_sha256': counts_hash(training),
                        'prefix_hashes': {m: digest(p) for m, p in prefix_paths.items()},
                        'all_model_and_union_recounts_match': True, 'candidate_audits': candidate_audits,
                        'human_verification': 'pending'})
        results[arm] = r
    data = load_private(root/'private/partition.pkl.gz')
    targets = {i for i, _ in data['roles'][f"test_{cfg['seeds'][0]}"]}
    assert len(targets) == cfg['users']
    for source in data['metadata']['excluded_partition_sources']:
        p = ROOT/source['path']
        assert digest(p) == source['sha256']
        old = load_private(p)
        assert targets.isdisjoint(i for rows in old['roles'].values() for i, _ in rows)
    for role in ('train', 'tuning', 'preview_select', 'preview_confirm'):
        assert targets.isdisjoint(i for i, _ in data['roles'][role])
    write_json(root/'audit.json', {'status': 'passed' if len(results) == len(cfg['arms']) else 'partial', 'checks': records,
                                  'new_target_occurrences_disjoint': True, 'human_verification': 'pending'})
    return results, records


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
    ax.set(xlabel='人均额外提交次数', title='（d）注册成本（虚线：1次上限）')
    ax.set_xlim(0, max(values)*1.24)
    for i, value in enumerate(values):
        ax.text(value+max(values)*.02, i, f'{value:.4f}', va='center', fontsize=9)
    ax.axvline(cfg['max_extra'], color='#555555', linestyle='--', linewidth=.9)
    ax.grid(axis='x', alpha=.15)
    for ax in axes.flat:
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.set_axisbelow(True)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='lower center', bbox_to_anchor=(.5, .075), ncol=3, frameon=False, fontsize=9)
    fig.text(.5, .02, '联合曲线每模型各使用横轴所示次数，合计最多两倍；同一账户只计一次。仅一次模拟。',
             ha='center', fontsize=9, color='#505B65')
    for suffix in ('pdf', 'png', 'svg'):
        fig.savefig(dest/f'registration_dual_five_policy_comparison.{suffix}', dpi=240, facecolor='white')
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--partial', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    cfg = json.loads((root/'manifest.json').read_text('utf-8'))['config']
    results, records = audit(root, cfg, args.partial)
    dest = root/'deliverables'
    dest.mkdir(exist_ok=True)
    rows = []
    for arm, label, *_ in METHODS:
        if arm not in results:
            continue
        r = results[arm]
        rates = {m: r['actual_attacks'][m]['points'][-1]['rate_per_initial_user'] for m in ('pcfg', 'omen', 'union_each_model_B')}
        rows.append({'arm': arm, 'label': label, 'users': cfg['users'], 'per_model_budget': max(cfg['budgets']),
                     **rates, 'extra': r['cost']['mean_extra_submissions'], 'modification': r['cost']['modification_rate'],
                     'more_than_three': r['cost']['more_than_three_fraction'], 'cost_eligible': not r['eligibility_failures'],
                     'outside_both': r['final_dual_risk']['outside_model_support_fraction']})
    with (dest/'million_attempt_summary.csv').open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    if len(results) == len(cfg['arms']):
        plot(dest, cfg, results)
    write_json(dest/'figure_manifest.json', {'evidence': records, 'config_sha256': digest(root/'manifest.json'),
               'audit_sha256': digest(root/'audit.json'), 'code_sha256': digest(Path(__file__)),
               'budget_unit': 'raw attempts EACH model', 'repeats': 1, 'human_verification': 'pending',
               'outputs': {p.name: digest(p) for p in dest.iterdir() if p.suffix in ('.csv', '.pdf', '.png', '.svg')}})
    print(json.dumps({'audit': 'passed' if len(results) == len(cfg['arms']) else 'partial',
                      'summary': rows, 'artifacts': str(dest)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
