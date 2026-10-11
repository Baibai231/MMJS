"""Export two publication figures and one ablation table from recorded outcomes."""
from __future__ import annotations

from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

STUDY = ROOT / 'reports/privacy_joint_minimal_20261011'
ASSETS = ROOT / 'docs/privacy_joint_assets_20261011'
LABELS = {'A': '边际画像 A', 'B': '条件画像 B', 'C': '条件画像＋去重 C'}
COLORS = {'A': '#737b86', 'B': '#2873b9', 'C': '#d26028'}


def load_rows(name):
    with (STUDY / name).open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def save_figure(fig, name):
    for suffix in ['png', 'pdf', 'svg']:
        fig.savefig(ASSETS / f'{name}.{suffix}', dpi=240, bbox_inches='tight')
    plt.close(fig)


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    installed = {font.name for font in font_manager.fontManager.ttflist}
    font = next((s for s in ['Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC', 'SimSun'] if s in installed), 'DejaVu Sans')
    plt.rcParams.update({'font.family': font, 'axes.unicode_minus': False, 'font.size': 10,
                         'axes.spines.top': False, 'axes.spines.right': False, 'pdf.fonttype': 42,
                         'svg.fonttype': 'none'})
    cfg = json.loads((STUDY / 'manifest.json').read_text('utf-8'))['config']
    summary = json.loads((STUDY / 'summary.json').read_text('utf-8'))
    profile = load_rows('profile_results.csv')
    registration = load_rows('registration_results.csv')
    references = load_rows('profile_references.csv')
    epsilons = cfg['epsilons']
    source_data = []
    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.1))
    for method in ['A', 'B', 'C']:
        for panel, metric, joint_only in [(0, 'absolute_error_pp', False), (1, 'redundant_exact_slots_fraction', True)]:
            means, lowers, uppers = [], [], []
            for epsilon in epsilons:
                by_repeat = defaultdict(list)
                for row in profile:
                    if row['method'] == method and float(row['epsilon']) == epsilon and (not joint_only or int(row['j']) > 0):
                        value = float(row[metric]) * (100 if panel == 1 else 1)
                        by_repeat[int(row['repetition'])].append(value)
                values = np.array([np.mean(by_repeat[i]) for i in sorted(by_repeat)])
                mean = float(np.mean(values))
                low, high = (float(v) for v in np.quantile(values, [0.1, 0.9]))
                means.append(mean); lowers.append(mean - low); uppers.append(high - mean)
                source_data.append({'figure': 1, 'panel': panel + 1, 'method': method, 'epsilon': epsilon,
                                    'metric': metric, 'mean': mean, 'p10': low, 'p90': high, 'repetitions': len(values)})
            axes[panel].plot(epsilons, means, marker='o', linewidth=2,
                             markersize=5, color=COLORS[method], label=LABELS[method])
            for epsilon, mean, lower, upper in zip(epsilons, means, lowers, uppers):
                axes[panel].vlines(epsilon, mean-lower, mean+upper, color=COLORS[method], linewidth=1)
                axes[panel].hlines([mean-lower, mean+upper], epsilon-0.025, epsilon+0.025, color=COLORS[method], linewidth=1)
    axes[0].set_title('(a) 联合影响人数的预测误差')
    axes[0].set_ylabel('平均绝对误差（占历史样本的百分点）')
    axes[1].set_title('(b) 完整口令规则中的重复名额')
    axes[1].set_ylabel('已被结构规则覆盖的名额比例（%）')
    for ax in axes:
        ax.set_xlabel('单次画像隐私预算 ε')
        ax.set_xticks(epsilons)
        ax.set_ylim(bottom=0)
        ax.grid(axis='y', alpha=0.2)
    axes[0].legend(frameon=False, fontsize=9)
    fig.text(0.5, -0.01, '每档20次噪声重复；误差线为重复结果的10%—90%分位范围。左图平均6组参数；右图仅含4组联合参数。', ha='center', fontsize=9)
    fig.tight_layout()
    save_figure(fig, 'figure1_profile')

    order = ['base', 'O_independent', 'O_residual', 'A', 'B', 'C']
    tick_labels = ['基础规则', '明文独立', '明文去重', '边际 A', '条件 B', '条件＋去重 C']
    colors = ['#adb5bf', '#44545e', '#637465', COLORS['A'], COLORS['B'], COLORS['C']]
    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.4))
    for panel, metric in [(0, 'hit_rate_per_completed'), (1, 'modification_rate')]:
        for x, method in enumerate(order):
            values = np.array([100 * float(row[metric]) for row in registration if row['method'] == method])
            mean = float(values.mean())
            low, high = (float(v) for v in np.quantile(values, [0.1, 0.9]))
            axes[panel].bar(x, mean, color=colors[x], width=0.66)
            axes[panel].vlines(x, low, high, color='#333333', linewidth=1)
            axes[panel].hlines([low, high], x-0.06, x+0.06, color='#333333', linewidth=1)
            axes[panel].text(x, mean + (0.17 if panel == 0 else 0.28), f'{mean:.2f}', ha='center', fontsize=9)
            source_data.append({'figure': 2, 'panel': panel + 1, 'method': method, 'epsilon': 1 if method in LABELS else 'reference',
                                'metric': metric, 'mean': mean, 'p10': low, 'p90': high, 'repetitions': len(values)})
        axes[panel].set_xticks(range(len(order)), tick_labels, rotation=18, ha='right')
        axes[panel].grid(axis='y', alpha=0.2)
        axes[panel].set_axisbelow(True)
    axes[0].set_title('(a) 固定攻击前缀的猜测命中率')
    axes[0].set_ylabel('命中数 / 已完成模拟账户数（%）')
    axes[0].set_ylim(0, 14)
    axes[1].set_title('(b) 修改比例与完成情况')
    axes[1].set_ylabel('已完成且改过口令的数量 / 初始3000条记录（%）')
    axes[1].set_ylim(0, 65)
    axes[1].axhline(60, linestyle='--', color='#a53932', linewidth=1)
    axes[1].text(2.5, 61, '原有修改比例上限：60%', ha='center', fontsize=9, color='#a53932')
    fig.text(0.5, -0.02, 'ε=1，k=10，j=1，最多3次重试；基础规则3000/3000完成，所有联合方法2999/3000完成，均未满足零未完成约束。', ha='center', fontsize=9)
    fig.tight_layout()
    save_figure(fig, 'figure2_registration')

    table = []
    k, j = cfg['registration_pair']
    for method in order:
        if method == 'base':
            error = overlap = numeric_ms = rule_ms = 0.0
        elif method in LABELS:
            rows = [r for r in profile if r['method'] == method and float(r['epsilon']) == 1 and int(r['k']) == k and int(r['j']) == j]
            error = np.mean([float(r['absolute_error_pp']) for r in rows])
            overlap = np.mean([float(r['redundant_exact_slots_fraction']) for r in rows])
            numeric_ms = np.mean([float(r['numeric_profile_ms']) for r in rows])
            rule_ms = np.mean([float(r['ideal_rule_evaluation_ms']) for r in rows])
        else:
            rows = [r for r in references if r['method'] == method and int(r['k']) == k and int(r['j']) == j]
            error = float(rows[0]['absolute_error_pp'])
            overlap = float(rows[0]['redundant_exact_slots_fraction'])
            numeric_ms = rule_ms = None
        r = summary['registration'][method]
        table.append({'method': method, 'profile_prediction_error_pp': float(error), 'redundant_exact_slots_percent': 100 * float(overlap),
                      'completed_attack_hit_percent': 100 * r['hit_rate_per_completed'], 'modification_percent': 100 * r['modification_rate'],
                      'completed_records_mean': r['completed_users'], 'pending_records_mean': r['pending_users'],
                      'extra_submissions_mean': r['mean_extra_submissions'],
                      'eligible_runs': f"{r['cost_eligible_runs']}/{r['runs']}",
                      'profile_numeric_ms_mean': None if numeric_ms is None else float(numeric_ms),
                      'ideal_rule_evaluator_ms_mean': None if rule_ms is None else float(rule_ms),
                      'admin_password_values_output': 'not_applicable' if method == 'base' else ('yes_reference_evaluator' if method.startswith('O_') else 'no_by_interface_design')})
    for filename, rows in [('figure_source_data.csv', source_data), ('table1_ablation.csv', table)]:
        with (ASSETS / filename).open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)
    export_manifest = {'source_study': str(STUDY), 'font': font,
                       'source_hashes': {name: hashlib.sha256((STUDY / name).read_bytes()).hexdigest() for name in ['manifest.json', 'summary.json', 'profile_results.csv', 'profile_references.csv', 'registration_results.csv']},
                       'render_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (ASSETS / 'figure_manifest.json').write_text(json.dumps(export_manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'assets': str(ASSETS), 'font': font, 'table': table}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
