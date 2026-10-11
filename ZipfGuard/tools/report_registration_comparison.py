"""Generate evidence-bound Chinese report, paired tables, figures, and strict audit."""
from pathlib import Path
from collections import Counter
import csv
import hashlib
import itertools
import json
import math
import re
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.registration_joint_data import load_private
from experiments.registration_joint_pipeline import write_json
from core.corpus import counts_hash

LABELS = {'base': '共同基础', 'fixed': '一次建表', 'refresh': '热点刷新',
          'schechter': '流行度阈值', 'malone': '概率拒绝', 'counterfactual': '反事实收益选择',
          'current_joint': '现有联合控制', 'website_core': 'GitHub核心', 'risk_budgeted': '风险选择限频'}

TARGET = 'risk_budgeted'


def rate(result, model, budget=1000000):
    return next(p['rate_per_initial_user'] for p in result['actual_attacks'][model]['points'] if p['budget'] == budget)


def pair_summary(rows, comparator):
    differences = [100*(rate(r[comparator], 'union_equal_total_B')-rate(r[TARGET], 'union_equal_total_B')) for r in rows]
    mean = statistics.mean(differences)
    half = 2.7764451051977987*statistics.stdev(differences)/math.sqrt(len(differences))
    null = [abs(statistics.mean(s*d for s, d in zip(signs, differences)))
            for signs in itertools.product([-1, 1], repeat=len(differences))]
    p = sum(v >= abs(mean)-1e-12 for v in null)/len(null)
    extra = statistics.mean(r[TARGET]['cost']['mean_extra_submissions']-
                            r[comparator]['cost']['mean_extra_submissions'] for r in rows)
    modification = statistics.mean(r[TARGET]['cost']['modification_rate']-
                                   r[comparator]['cost']['modification_rate'] for r in rows)
    return {'comparator': comparator, 'differences_pp': differences, 'mean_improvement_pp': mean,
            'approximate_t95_low': mean-half, 'approximate_t95_high': mean+half,
            'positive_seeds': sum(d > 0 for d in differences), 'two_sided_exact_sign_flip_p': p,
            'extra_submission_difference': extra, 'modification_rate_difference': modification,
            'all_new_cost_eligible': all(not r[TARGET]['eligibility_failures'] for r in rows),
            'all_comparator_cost_eligible': all(not r[comparator]['eligibility_failures'] for r in rows),
            'practical_gate': mean >= .5 and sum(d > 0 for d in differences) >= 4,
            'strict_mean_cost_dominance': extra <= 0 and modification <= 0}


def audit(root, cfg, jobs):
    manifest = json.loads((root/'manifest.json').read_text('utf-8'))
    errors = []
    repair = json.loads((root/'omen_memory_fix.json').read_text('utf-8'))
    if not repair.get('cached_and_patched_one_million_first_ranks_equal') or repair['test_policy_changed']:
        errors.append('omen_runtime_repair_not_verified')
    if hashlib.sha256((ROOT/'local_attack_models/upstream/omen/src/createNG.c').read_bytes()).hexdigest() != repair['patched_source_sha256']:
        errors.append('omen_repaired_source_changed')
    if hashlib.sha256((ROOT/'local_attack_models/upstream/omen/createNG').read_bytes()).hexdigest() != repair['patched_binary_sha256']:
        errors.append('omen_repaired_binary_changed')
    for rel, expected in manifest['source_hashes'].items():
        if hashlib.sha256((ROOT/rel).read_bytes()).hexdigest() != expected:
            errors.append('implementation_hash_changed:'+rel)
    extension = json.loads((root/'extension_manifest.json').read_text('utf-8'))
    for rel, expected in extension['source_hashes'].items():
        if hashlib.sha256((ROOT/rel).read_bytes()).hexdigest() != expected:
            errors.append('extension_implementation_hash_changed:'+rel)
    data = load_private(root/'private/partition.pkl.gz')
    positions = [i for rows in data['roles'].values() for i, _ in rows]
    if len(positions) != len(set(positions)):
        errors.append('role_occurrence_overlap')
    old = load_private(ROOT/'reports/registration_joint_minimal_20261011/private/partition.pkl.gz')
    old_positions = {i for rows in old['roles'].values() for i, _ in rows}
    for phase in ['calibration', 'test']:
        for seed in cfg['seeds']:
            rows = data['roles'][f'{phase}_{seed}']
            if len(rows) != cfg['users'] or any(i in old_positions for i, _ in rows):
                errors.append('fresh_role_failure:'+phase+str(seed))
    opening = json.loads((root/'test_opening.json').read_text('utf-8'))
    seal = json.loads((root/'protocol_seal.json').read_text('utf-8'))
    if (seal['test_opened_at_seal'] or seal['sealed_at'] > opening['opened_at'] or
            seal['protocol_sha256'] != hashlib.sha256((root/'protocol.md').read_bytes()).hexdigest()):
        errors.append('protocol_not_sealed_before_test')
    for p in (root/'frozen_parameters').glob('*.json'):
        record = json.loads(p.read_text('utf-8'))
        if record['test_data_used'] or record['frozen_at'] > opening['opened_at']:
            errors.append('calibration_gate_failure:'+p.name)
        if hashlib.sha256(p.read_bytes()).hexdigest() != opening['frozen_parameter_hashes'][p.name]:
            errors.append('frozen_parameters_changed:'+p.name)
    checked = 0
    for experiment, rows in jobs.items():
        for job in rows:
            seed = job['seed']
            for arm in cfg['arms'][experiment]:
                checked += 1
                result = job['arms'][arm]
                path = root/'runs/test'/experiment/str(seed)/arm
                state_path = path/'state.pkl.gz' if arm != 'current_joint' else path.parent/'joint/state.pkl.gz'
                state = load_private(state_path)
                counts = (load_private(path/'developer_counts.pkl.gz') if arm != 'current_joint'
                          else state['dev_history'])
                if result['status'] != 'complete' or result['cost']['initial_users'] != cfg['users']:
                    errors.append(f'incomplete:{experiment}/{seed}/{arm}')
                if result['cost']['pending_users'] or sum(state['history'].values()) != cfg['users']:
                    errors.append(f'registration_loss:{experiment}/{seed}/{arm}')
                key = counts_hash(counts)
                if key != result['A1_training_sha256'] or sum(counts.values()) != len(data['roles']['train']):
                    errors.append(f'training_hash_or_size:{experiment}/{seed}/{arm}')
                prefixes = {}
                for model in ['pcfg', 'omen']:
                    prefix = load_private(root/'private/attacks'/experiment/str(seed)/'prefixes'/f'{key}-{model}-1000000.pkl.gz')
                    prefixes[model] = prefix
                    if not prefix['complete'] or prefix['raw_emissions'] != 1000000:
                        errors.append(f'incomplete_attack:{experiment}/{seed}/{arm}/{model}')
                    for point in result['actual_attacks'][model]['points']:
                        b = point['budget']
                        hits = sum(n for w, n in state['history'].items() if prefix['ranks'].get(w, b+1) <= b)
                        if hits != point['hits'] or abs(hits/cfg['users']-point['rate_per_initial_user']) > 1e-12:
                            errors.append(f'attack_reconstruction:{experiment}/{seed}/{arm}/{model}/{b}')
                for point in result['actual_attacks']['union_equal_total_B']['points']:
                    b = point['budget']//2
                    hits = sum(n for w, n in state['history'].items() if any(p['ranks'].get(w, b+1) <= b for p in prefixes.values()))
                    if hits != point['hits']:
                        errors.append(f'ensemble_reconstruction:{experiment}/{seed}/{arm}')
    value = {'status': 'passed' if not errors else 'failed', 'checked_final_trajectories': checked,
             'expected_final_trajectories': sum(len(v) for v in cfg['arms'].values())*len(cfg['seeds']),
             'errors': errors, 'audited_at': time.strftime('%Y-%m-%d %H:%M:%S'),
             'checks': ['implementation hashes', 'occurrence disjointness and previous test exclusion',
                        'calibration before test opening', 'all users retained', 'developer hashes and size',
                        'actual one-million prefixes', 'private hit reconstruction including union budget'],
             'human_source_review': 'pending', 'visual_review': 'pending'}
    write_json(root/'audit.json', value)
    if errors:
        raise RuntimeError('Strict audit failed: '+', '.join(errors[:8]))
    return value


def source_registry(root):
    sources = [
        ('E001', 'Popularity Is Everything: A New Approach to Protecting Passwords from Statistical-Guessing Attacks',
         ['Stuart Schechter', 'Cormac Herley', 'Michael Mitzenmacher'], 2010,
         'https://www.usenix.org/legacy/event/hotsec10/tech/full_papers/Schechter.pdf', '', 'Section 2, pp. 2-3; Section 3 privacy design'),
        ('E002', 'Investigating the Distribution of Password Choices', ['David Malone', 'Kevin Maher'], 2012,
         'https://www.maths.tcd.ie/~dwmalone/p/www2012.pdf', '10.1145/2187836.2187878', 'Section 6, printed p.308, PDF p.9'),
        ('E003', 'Diversify to Survive: Making Passwords Stronger with Adaptive Policies',
         ['Sean M. Segreti', 'William Melicher', 'Saranga Komanduri', 'Darya Melicher', 'Richard Shay', 'Blase Ur',
          'Lujo Bauer', 'Nicolas Christin', 'Lorrie Faith Cranor', 'Michelle L. Mazurek'], 2017,
         'https://www.usenix.org/system/files/conference/soups2017/soups2017-segreti.pdf', '', 'Sections 3.3, 3.5 and results'),
        ('E004', 'DPPG: A Dynamic Password Policy Generation System', ['Shukun Yang', 'Shouling Ji', 'Raheem Beyah'], 2018,
         'https://nesa.zju.edu.cn/download/DPPG%20A%20Dynamic%20Password%20Policy%20Generation%20System.pdf',
         '10.1109/TIFS.2017.2737971', 'Sections III-IV; source is accepted manuscript'),
        ('E005', 'Skeptic: Automatic, Justified and Privacy-Preserving Password Composition Policy Selection',
         ['Saul Johnson', 'João F. Ferreira', 'Alexandra Mendes', 'Julien Cordry'], 2020,
         'https://arxiv.org/html/2007.03809v2', '10.48550/arXiv.2007.03809', 'Sections 3.3, 3.4; 2024 revision'),
        ('E006', 'Creating a strong password', ['GitHub'], None,
         'https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-strong-password', '',
         'Official published length/category requirements, accessed 2026-10-11'),
        ('E007', 'Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents',
         ['Timothy Kassis', 'Vinayak Agarwal', 'Yuhuan He', 'Darshil Patel', 'Aubrey M. Brueckner'], 2026,
         'https://arxiv.org/abs/2609.00065', '10.48550/arXiv.2609.00065', 'arXiv v2 metadata, 2026-09-02; writing workflow only'),
        ('E010', 'Prospective registration comparison final results', [], 2026, '', '', 'runs/test and per_seed_results.csv'),
        ('E011', 'Frozen registration protocol and implementation', [], 2026, '', '', 'manifest.json; extension_manifest.json; protocol.md; frozen_parameters; candidate audits; omen_memory_fix.json'),
        ('E012', 'Previous joint registration experiment', [], 2026, '', '', '../registration_joint_minimal_20261011/deliverables/evidence.json')]
    payload = {'schema_version': '1.0', 'sources': []}
    for evidence, title, authors, year, url, doi, locator in sources:
        payload['sources'].append({'evidence_id': evidence, 'title': title, 'authors': authors, 'year': year,
            'source_type': ('webpage' if evidence == 'E006' else 'report' if evidence in ['E010', 'E011', 'E012']
                            else 'preprint' if evidence in ['E005', 'E007'] else 'conference_paper' if evidence in ['E001','E002','E003'] else 'journal_article'),
            'confidentiality': 'public' if url else 'restricted',
            'identifiers': {'url': url, 'doi': doi, 'pmid': '', 'pmcid': '', 'isbn': ''},
            'locator': locator, 'verification': {'source_opened': True, 'status': 'unverified',
            'verified_by': '', 'verified_on': ''}})
    write_json(root/'deliverables/source_manifest.json', payload)
    return payload


def figures(root, cfg, jobs, comparisons):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({'font.sans-serif': ['Microsoft YaHei', 'DejaVu Sans'], 'axes.unicode_minus': False,
                         'font.size': 10, 'pdf.fonttype': 42})
    output = root/'deliverables/figures'
    output.mkdir(parents=True, exist_ok=True)
    files = []
    markers = ['o', 's', '^', 'D', 'v', 'P', 'X']
    def save(fig, name):
        fig.tight_layout()
        for ext in ['png', 'pdf', 'svg']:
            file = output/(name+'.'+ext)
            fig.savefig(file, dpi=190, bbox_inches='tight')
            files.append(str(file))
        plt.close(fig)
    for experiment, rows in jobs.items():
        fig, axes = plt.subplots(1, 3, figsize=(15, 4.7))
        for i, arm in enumerate(cfg['arms'][experiment]):
            for ax, model, title in zip(axes, ['pcfg', 'omen', 'union_equal_total_B'],
                                       ['PCFG（每模型预算）', 'OMEN（每模型预算）', '联合攻击（两模型总预算）']):
                values = [[100*rate(j['arms'][arm], model, b) for b in cfg['budgets']] for j in rows]
                ys = np.mean(values, axis=0)
                ax.plot(cfg['budgets'], ys, marker=markers[i], label=LABELS[arm], linewidth=1.5)
                ax.set(xscale='log', xlabel='实际尝试次数（含重复）', ylabel='命中率 / 初始用户 (%)', title=title)
                ax.grid(alpha=.2)
        axes[-1].legend(fontsize=8, loc='upper left', bbox_to_anchor=(1, 1))
        fig.suptitle(('文献机制对照' if experiment == 'literature' else '网站规则对照')+'：每组10万用户 × 5次配对，图示均值')
        save(fig, experiment+'_attack_curves')
        fig, axes = plt.subplots(1, 2, figsize=(11, 4.7))
        for i, arm in enumerate(cfg['arms'][experiment]):
            rs = [j['arms'][arm] for j in rows]
            x = [r['cost']['mean_extra_submissions'] for r in rs]
            y = [100*rate(r, 'union_equal_total_B') for r in rs]
            axes[0].scatter(x, y, marker=markers[i], alpha=.6, label=LABELS[arm])
            axes[0].scatter([statistics.mean(x)], [statistics.mean(y)], marker=markers[i], s=100,
                            facecolors='none', edgecolors='black')
            axes[1].bar(i, 100*statistics.mean(r['cost']['modification_rate'] for r in rs),
                        label=LABELS[arm], hatch=['', '/', '\\', 'x', '.', '+', 'o'][i])
        axes[0].set(xlabel='平均额外提交次数', ylabel='等总预算100万联合命中率 (%)', title='成本与攻击风险（小点为单次、大空心为均值）')
        axes[0].legend(fontsize=8)
        axes[0].grid(alpha=.2)
        axes[1].set(xticks=range(len(cfg['arms'][experiment])),
                    xticklabels=[LABELS[a] for a in cfg['arms'][experiment]], ylabel='修改用户比例 (%)', title='绝对修改成本')
        axes[1].tick_params(axis='x', rotation=35)
        save(fig, experiment+'_cost_risk')
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    for ax, experiment in zip(axes, jobs):
        rows = comparisons[experiment]
        for i, row in enumerate(rows):
            ax.scatter([i]*5, row['differences_pp'], marker='o', s=24, alpha=.7)
            ax.errorbar(i, row['mean_improvement_pp'],
                yerr=[[row['mean_improvement_pp']-row['approximate_t95_low']],
                      [row['approximate_t95_high']-row['mean_improvement_pp']]], marker='D', color='black', capsize=4)
        ax.axhline(0, color='grey', linestyle='--')
        ax.set(xticks=range(len(rows)), xticklabels=[LABELS[r['comparator']] for r in rows],
               ylabel='对照减风险选择方法（百分点）', title='文献比较' if experiment == 'literature' else '网站比较')
        ax.tick_params(axis='x', rotation=35)
        ax.grid(alpha=.2)
    fig.suptitle('等总预算100万的配对差：正值有利于风险选择方法；区间为近似 t95%')
    save(fig, 'paired_improvement')
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    for ax, (experiment, rows) in zip(axes, jobs.items()):
        for job in rows:
            timeline = job['arms'][TARGET]['timeline']
            values = [float('nan')]+[r['threshold_rate'] or 1 for r in timeline[1:]]
            ax.step(range(1, 11), values, where='post', label=str(job['seed']))
        ax.set(xlabel='注册批次', ylabel='流行度阈值 r（对数轴）', yscale='log',
               title='文献环境（五次轨迹重合）' if experiment == 'literature' else '网站环境（r=1为宽松回退）')
        ax.legend(title='种子', fontsize=8)
        ax.grid(alpha=.2)
    fig.suptitle('第1批使用共同基础规则；第2批开始在线选择流行度阈值')
    save(fig, 'threshold_trajectory')
    return files


def main(root, audited_record=None):
    root = Path(root)
    cfg = json.loads((root/'manifest.json').read_text('utf-8'))['config']
    if (root/'report_scope.json').exists():
        scope = json.loads((root/'report_scope.json').read_text('utf-8'))
        cfg = {**cfg, 'arms': scope['arms']}
    jobs = {experiment: [json.loads((root/'runs/test'/experiment/str(seed)/'summary.json').read_text('utf-8'))
                         for seed in cfg['seeds']] for experiment in cfg['arms']}
    audit_record = audited_record if audited_record is not None else audit(root, cfg, jobs)
    if (audit_record['status'] != 'passed' or audit_record['errors'] or
            audit_record['checked_final_trajectories'] != sum(len(v) for v in cfg['arms'].values())*len(cfg['seeds'])):
        raise RuntimeError('Report needs a passed audit for all retained trajectories')
    output = root/'deliverables'
    output.mkdir(parents=True, exist_ok=True)
    source_registry(root)
    comparisons = {e: [pair_summary([j['arms'] for j in rows], a) for a in cfg['arms'][e] if a != TARGET]
                   for e, rows in jobs.items()}
    write_json(output/'paired_comparisons.json', comparisons)
    export = []
    for experiment, rows in jobs.items():
        for job in rows:
            for arm in cfg['arms'][experiment]:
                result = job['arms'][arm]
                export.append({'experiment': experiment, 'seed': job['seed'], 'arm': arm,
                    'initial_users': result['cost']['initial_users'], 'pcfg_1m': rate(result, 'pcfg'),
                    'omen_1m': rate(result, 'omen'), 'union_total_1m': rate(result, 'union_equal_total_B'),
                    'pcfg_mc_1m': result['A1_MC']['rates']['1000000'],
                    'outside_fraction': result['A1_MC']['outside_model_support_fraction'],
                    'modification_rate': result['cost']['modification_rate'],
                    'mean_extra_submissions': result['cost']['mean_extra_submissions'],
                    'more_than_three_fraction': result['cost']['more_than_three_fraction'],
                    'incremental_modification_rate': result['cost'].get('incremental_modification_rate'),
                    'incremental_mean_extra_submissions': result['cost'].get('incremental_mean_extra_submissions'),
                    'pending_users': result['cost']['pending_users'],
                    'eligibility_failures': '|'.join(result['eligibility_failures'])})
    with (output/'per_seed_results.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(export[0]))
        writer.writeheader()
        writer.writerows(export)
    aggregates = []
    for experiment, arms in cfg['arms'].items():
        for arm in arms:
            rows = [r for r in export if r['experiment'] == experiment and r['arm'] == arm]
            aggregate = {'experiment': experiment, 'arm': arm, 'method': LABELS[arm],
                         'repeats': len(rows), 'users_per_repeat': cfg['users'],
                         'cost_eligible_repeats': sum(not r['eligibility_failures'] for r in rows)}
            for name in ['pcfg_1m', 'omen_1m', 'union_total_1m', 'modification_rate',
                         'mean_extra_submissions', 'more_than_three_fraction',
                         'incremental_modification_rate', 'incremental_mean_extra_submissions']:
                values = [r[name] for r in rows if r[name] is not None]
                aggregate[name+'_mean'] = statistics.mean(values) if values else None
                aggregate[name+'_sd'] = statistics.stdev(values) if len(values) > 1 else None
            aggregates.append(aggregate)
    with (output/'mean_results.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(aggregates[0]))
        writer.writeheader()
        writer.writerows(aggregates)
    figure_files = figures(root, cfg, jobs, comparisons)
    lines = ['# 注册阶段动态口令策略：文献复现、网站对照与改进方案报告', '',
             '**状态:** 本地科研审阅稿。实验数据已生成并通过机器审计；引用及科学表述待作者人工核验，未声明可直接投稿。', '']
    claims = []
    def add(text, evidence='E011', kind='method', section='方法'):
        identifier = f'C{len(claims)+1:03}'
        digest = hashlib.sha256(' '.join(text.split()).encode()).hexdigest()
        lines.append(text+f' [claim:{identifier}] [evidence:{evidence}]')
        if not text.startswith('|'):
            lines.append('')
        claims.append({'claim_id': identifier, 'section': section, 'claim_kind': kind,
            'claim_text_sha256': digest, 'evidence_ids': evidence, 'verification_status': 'unverified',
            'uncertainty': 'five paired same-source simulation repeats; human verification pending',
            'analysis_intent': 'descriptive' if kind == 'result' else 'not_applicable'})
    lines += ['## 1. 当前方案的判断', '']
    add('此前联合控制器没有选择结构阻断，完整字符串策略与联合策略在先前验证中表现相同；旧实验不能支持结构联合的独立贡献，也不能把动态调整本身写成首次提出。', 'E012,E011', 'interpretive', '创新判断')
    add('开发校准中纯反事实收益列表较弱，因此在终测未开封时改为风险选择限频：只对训练预测可猜中字符串增加流行度限制，按重选模拟的攻击收益和提交成本动态选择阈值，再用另一预览集确认。它是有可检验差异的改进候选，文献新颖性不能仅凭胜出认定。', 'E011', 'interpretive', '创新判断')
    add('可讨论的贡献是：将限频对象缩小到训练攻击器在指定预算下能猜中的字符串；利用完整重选结果而非仅流行度或均匀度选择阈值；把注册成本纳入可行条件；用另一预览集确认单一赢家，并用重新训练的攻击器评价。各组成思想均有相关研究，本研究应表述为这一具体组合与条件下的实现和实验证据，不宣称首次动态生成或首次考虑可用性。', 'E001,E003,E004,E005,E011', 'interpretive', '创新判断')
    lines += ['']
    for experiment, comparator in [('literature', 'schechter'), ('website', 'base')]:
        pair = next(p for p in comparisons[experiment] if p['comparator'] == comparator)
        target_rate = 100*statistics.mean(rate(j['arms'][TARGET], 'union_equal_total_B') for j in jobs[experiment])
        control_rate = 100*statistics.mean(rate(j['arms'][comparator], 'union_equal_total_B') for j in jobs[experiment])
        if experiment == 'website':
            add(f"主要结果：网站共同基础的联合命中率为{control_rate:.3f}%，风险选择方法为{target_rate:.3f}%，差为{pair['mean_improvement_pp']:.3f}个百分点；平均额外提交差为{pair['extra_submission_difference']:+.4f}次。动态贡献应以该共同基础比较，而非把公共黑名单收益也算作动态收益。", 'E010', 'result', '主要结果')
        else:
            add(f"较强文献对照：流行度阈值的联合命中率为{control_rate:.3f}%，风险选择方法为{target_rate:.3f}%；新方法相对对照的平均额外提交差为{pair['extra_submission_difference']:+.4f}次。攻击风险与成本必须同时解释，不能把较低成本写成已证明安全更强或安全等效。", 'E010', 'result', '主要结果')
    lines += ['', '## 2. 现有研究与改进空间', '',
              '| 研究 | 已做工作 | 与本研究问题相关的局限及复现范围 |', '| --- | --- | --- |']
    add('| Schechter 等（2010） | 用流行度阈值限制过于常见的完整口令，并设计 sketch 与防泄露机制 | 阈值关注频次，未直接计算本次具体重选过程的攻击收益。本次是理想精确计数接受机制复现，未复现隐私系统。 |', 'E001,E011', 'factual', '文献')
    add('| Malone、Maher（2012） | 在线估计提议频率并概率接受，目标是更均匀的口令选择 | 理论独立提议假设不覆盖本次修补相关响应；均匀化也不等于对指定猜测器最优。本次按第6节时序复现。 |', 'E002,E011', 'factual', '文献')
    add('| Segreti 等（2017） | 真人研究结构及字符串自适应限制、反馈和可用性 | 已有真实可用性证据，不能说未研究成本；其预计算黑名单实验不等于本项目简单按热点建结构表，本次未宣称完整复现。 |', 'E003,E011', 'factual', '文献')
    add('| DPPG | 动态生成特征要求，探索与利用平衡分布，含可用性控制和攻击评价 | 已覆盖动态生成及攻击者视角；本次聚焦攻击可猜字符串的选择性限频、完整重选收益与具体预算，不能宣称首次考虑攻击或动态策略。 |', 'E004,E011', 'factual', '文献')
    add('| Skeptic | 按不同重选假设模拟策略诱导分布，以均匀度帮助选策略 | 已覆盖自动选择与行为模型；本次用实际攻击前缀作选择预测并做在线终测，仍受响应模型与预测器限制。 |', 'E005,E011', 'factual', '文献')
    lines += ['', '### 两个复现机制的具体设置', '']
    add('Schechter对所有符合共同基础的完整字符串使用流行度门槛：未出现过的字符串允许，否则仅当已完成注册次数小于r乘已完成账户数时允许；每次注册成功立即递增该字符串计数。使用精确计数以避免sketch近似误差削弱该对照。', 'E001,E011')
    add('Malone–Maher从过去已见的不同字符串中均匀选取参考字符串v，每名用户在重试期间保持v。对本次提议w，先从[0,F(w)]均匀抽取u，再递增F(w)，最后读取更新后的F(v)；只有共同基础满足且u不超过F(v)时接受。F统计所有提议，包括失败提议和不满足共同基础的提议，不仅是成功注册结果。', 'E002,E011')
    add('热点刷新对照按最近两批已完成注册计数更新完整字符串拒绝表，仅纳入至少出现两次且满足共同基础的字符串，按频次排序截取校准容量。它用于检验风险选择方法相对单纯刷新热门列表的增益，未将其冒充另一篇论文的完整复现。', 'E011')
    lines += ['', '## 3. 方法设置与理论依据', '']
    add('令共同基础为P0、全部完成注册口令计数为C、已完成账户数为N、训练预测器在等总预算B内的猜测字符串并集为G。所有完成注册字符串均计数，但只有G中的字符串受附加流行度限制；新字符串在启动时允许。', 'E011')
    lines += ['', '$$', r'\operatorname{Accept}_t(w;r)=P_0(w)\land[w\notin G_B\lor C_t(w)=0\lor C_t(w)<rN_t].', '$$', '']
    add('设在阈值r下完整重选所得预览计数为H_t^(r)，预览用户数为n，下一批用户数为m。选择目标是固定预测猜测集合内的累计账户命中比例；不是仅删除热门口令后重新归一化原始数据。', 'E011')
    lines += ['', '$$', r'\widehat R_t(r)=\frac{\sum_{w\in G_B}C_t(w)+(m/n)\sum_{w\in G_B}H_t^{(r)}(w)}{N_t+m},\qquad r_t^*=\arg\min_{r\in\mathcal F_t}\widehat R_t(r).', '$$', '']
    add('可行集合F_t由注册修改率、提交次数、长尾提交、较小猜测预算和联合支持域条件共同限定；同风险时先选提交成本较低者，再选较宽松阈值。选择集合决定一个赢家，确认集合只评估该赢家与当前阈值，确认不通过则保持；无可行阈值则使用r=1的宽松阈值回退。', 'E011')
    add('固定阈值的一段注册期间，对G中的任意字符串w，期末频数不超过max{期初频数, 1, ceil(r×期末账户数)}：已有过多历史账户不会被删除；每次新增只在计数为零或低于rN时发生，整数计数增加一后满足上述界。该计数性质解释了限频的作用，但不保证整体攻击风险下降，因为用户重选可能迁移到其他易猜字符串，故仍需完整重选预测和最终实际攻击验证。', 'E011', 'interpretive')
    add('每批克隆过去的计数状态，对六个阈值及保持当前阈值使用相同预览序列完整模拟在线接受和重选，再比较累计预测攻击风险；阈值网格为1e-6、1e-5、3e-5、1e-4、3e-4、1e-3。候选预览不得更新真实注册状态。', 'E011')
    add('主预测器是经共同基础规则转换的独立开发训练用户所训练的固定PCFG与OMEN前缀；以历史账户和预测新批账户的加权命中率选取成本合格赢家。历史项对固定预测器下的候选是常数，主要影响累计更新门槛，不能说选择阶段已经解决攻击者重新训练问题。', 'E011')
    add('选择及确认各使用6000名模拟预览用户，半数来自各自独立角色，半数来自过去两批初始口令；确认角色不同，但历史共享。当前阈值在选择预览中成本合格时，累计预测改善在选择和确认两阶段均须至少0.05个百分点；当前阈值成本不合格时允许修复约束的更新，不强制收益门槛。两种情况均要求新阈值成本合格、较小预算风险增加不超过0.2个百分点、两个模型均不支持的比例增加不超过2个百分点。PCFG单模型外另外披露。', 'E011')
    add('该依据是可计算的收益与成本决策目标，并非全局最优或真实安全证明。最终使用策略条件化的开发训练数据重新训练两个攻击器，验证固定预测器上的改善是否仍成立。', 'E011', 'interpretive')
    lines += ['', '## 4. 两项实验的共同条件', '']
    add('数据来自本地rockyou-withcount.txt，按文件声明的频次展开出现位置，仅使用ASCII有效条目，再对出现位置不放回抽样。模拟用户代表源数据中的出现次数，不是已核验的独立真人；没有把去重后的口令字符串等概率当作用户。数据源哈希与各角色位置哈希保存在data_manifest.json。', 'E010,E011')
    add('每组100000用户、10批、5个配对种子（11、23、42、67、101）。校准与终测新增100万出现位置，排除旧实验全部50万出现位置；终测种子之间位置不重叠，两类实验共享同一组用户以便配对。相同字符串可跨角色出现，出现位置不可重叠。', 'E010,E011')
    add('响应沿用60%修补、25%短语拼接、15%重新选择，以及原有确定性长度补齐；所有策略共享策略无关的候选序列。该模拟不能替代真人注册、记忆、放弃及长期使用实验。', 'E011')
    add('文献实验共同基础为长度至少8及训练频次前1000公共黑名单，修改率上限60%、平均额外提交上限1、超过3次额外提交比例上限5%。网站实验采用GitHub官方核心：至少15字符，或至少8字符且有小写字母和数字，未复现其不可得的完整服务端检查。', 'E006,E011')
    add('网站实验附加控制相对网站核心加公共黑名单计算增量，限制为最终字符串变化20%、平均正向提交差0.5、正向提交差超过3次比例5%。同时报告绝对成本；共同约束并不表示实际成本严格相等。', 'E011')
    add('刷新容量及Schechter阈值分别校准，使用五组独立十万用户校准集，冻结后才开封终测。概率拒绝不加弱化系数。两篇在线文献机制共享首批启动；前者统计完成注册口令，后者统计所有提议，含基础规则拒绝的提议。报告范围缩减为35条完整轨迹，仅去掉额外旧项目控制器比较，未更改最终方法、封存参数或数据。', 'E011')
    add('最终攻击只使用60000名开发训练用户的策略响应；公开列表规则用于转换开发用户，在线频数机制在开发用户上独立运行，因此其样本人数与目标100000不同。各攻击器实际生成100万尝试，重复计费；等总预算联合指标按各模型50万尝试计数，不把两个各100万写成总100万。', 'E010,E011')
    add('OMEN作者代码的训练路径内存分配少了字符串结束符空间，正式运行中触发崩溃；仅增加该分配的一字节，模型和排序逻辑不变。修复后用同一开发输入重放100万猜测，其首次排名字典与原缓存完全相同；实现及二进制哈希另存，缓存保留。', 'E011')
    add('五次配对区间采用近似t区间，另报双侧精确符号翻转值；五次全同向时最小双侧值为0.0625，不据此硬写5%显著。多方法比较未作确认性多重检验结论。', 'E011')
    lines += ['', '## 5. 封存参数', '']
    for experiment in jobs:
        p = json.loads((root/'frozen_parameters'/f'{experiment}.json').read_text('utf-8'))
        add(f"{'文献' if experiment == 'literature' else '网站'}环境：刷新容量 {p['refresh']['k']}，文献流行度阈值 {p['schechter']['rate'] if experiment == 'literature' else '不适用'}；最终风险选择方案使用封存的在线阈值网格与确认规则。文献对照参数通过校准角色选取；新方法在终测中按封存规则使用过去注册状态和预览角色在线选择阈值。", 'E011', 'method', '封存参数')
    lines += ['', '### 文献对照参数的选取依据', '',
              '| 校准候选 | 固定预测联合命中率 (%) | 平均额外提交 | 五次均成本合格 |',
              '| --- | --- | --- | --- |']
    calibrated = json.loads((root/'frozen_parameters/literature.json').read_text('utf-8'))
    for row in calibrated['ranking']:
        add(f"| {row['label']} | {100*row['mean_risk']:.3f} | {row['mean_extra']:.4f} | {'是' if row['eligible_all_seeds'] else '否'} |", 'E011', 'method', '校准')
    lines += ['']
    add('本表仅用于说明终测前的选择依据，使用固定开发预测器，不能充当策略条件化攻击器重新训练后的终测结果。刷新和流行度对照均选各自成本合格候选中的最低预测风险，风险持平时优先较低提交成本，再以候选名称作确定性排序；未人为选择风险更高的文献对照参数。', 'E011', 'method', '校准')
    lines += ['', '## 6. 终测数据与配对结论', '']
    facts = []
    for experiment, rows in jobs.items():
        lines += ['### '+('实验一：文献机制' if experiment == 'literature' else '实验二：网站规则'), '',
                  '| 方法 | PCFG@100万 (%) | OMEN@100万 (%) | 等总预算联合@100万 (%) | 修改 (%) | 额外提交 | 超过三次 (%) | PCFG模型外 (%) | 成本合格次数 |',
                  '| --- | --- | --- | --- | --- | --- | --- | --- | --- |']
        mean = lambda arm, func: statistics.mean(func(j['arms'][arm]) for j in rows)
        for arm in cfg['arms'][experiment]:
            rs = [j['arms'][arm] for j in rows]
            pcfg, omen, union = [100*mean(arm, lambda r, m=m: rate(r, m)) for m in ['pcfg', 'omen', 'union_equal_total_B']]
            mod = 100*mean(arm, lambda r: r['cost']['modification_rate'])
            extra = mean(arm, lambda r: r['cost']['mean_extra_submissions'])
            tail = 100*mean(arm, lambda r: r['cost']['more_than_three_fraction'])
            outside = 100*mean(arm, lambda r: r['A1_MC']['outside_model_support_fraction'])
            eligible = sum(not r['eligibility_failures'] for r in rs)
            add(f'| {LABELS[arm]} | {pcfg:.3f} | {omen:.3f} | {union:.3f} | {mod:.2f} | {extra:.4f} | {tail:.2f} | {outside:.2f} | {eligible}/5 |', 'E010', 'result', experiment)
            facts.append({'fact_id': f'N{len(facts)+1:03}', 'concept': experiment+'/'+arm+'/union_total_1m',
                'value': union, 'unit': 'percent', 'sample_size': 500000, 'denominator': 500000,
                'numerator': sum(next(p['hits'] for p in r['actual_attacks']['union_equal_total_B']['points'] if p['budget']==1000000) for r in rs),
                'analysis_set': 'five fresh paired target cohorts', 'section': experiment, 'evidence_ids': ['E010']})
        lines += ['', '| 对照减风险选择方法 | 平均降低 (百分点) | 近似t95%区间 | 同向次数 | 双侧符号翻转值 | 额外提交差（新减对照） |',
                  '| --- | --- | --- | --- | --- | --- |']
        for pair in comparisons[experiment]:
            add(f"| {LABELS[pair['comparator']]} | {pair['mean_improvement_pp']:.3f} | [{pair['approximate_t95_low']:.3f}, {pair['approximate_t95_high']:.3f}] | {pair['positive_seeds']}/5 | {pair['two_sided_exact_sign_flip_p']:.4f} | {pair['extra_submission_difference']:+.4f} |", 'E010', 'result', experiment)
        lines += ['']
        primary = ['schechter', 'malone', 'refresh'] if experiment == 'literature' else ['website_core', 'base']
        for comparator in primary:
            p = next(x for x in comparisons[experiment] if x['comparator'] == comparator)
            if p['practical_gate'] and p['all_new_cost_eligible']:
                interpretation = ('且均值修改和提交成本均不高于该对照' if p['strict_mean_cost_dominance'] else
                                  '但存在实际成本变化，应解释为共同预算内的效果与成本权衡')
                text = f"相对{LABELS[comparator]}，风险选择方案主指标降低{p['mean_improvement_pp']:.3f}个百分点，{p['positive_seeds']}/5次同向，达到预设实际改善门槛；{interpretation}。"
            elif (abs(p['mean_improvement_pp']) < .5 and p['extra_submission_difference'] < 0
                  and p['modification_rate_difference'] <= 0 and p['all_new_cost_eligible']):
                baseline_extra = mean(comparator, lambda r: r['cost']['mean_extra_submissions'])
                reduction = -100*p['extra_submission_difference']/baseline_extra if baseline_extra else 0
                text = f"相对{LABELS[comparator]}，主指标均值差为{p['mean_improvement_pp']:+.3f}个百分点，平均额外提交减少{reduction:.2f}%，修改率降低{-100*p['modification_rate_difference']:.3f}个百分点。可以报告本模拟下的提交成本优势与攻击均值差，但不能据此宣称攻击等效或安全优越。"
            elif p['mean_improvement_pp'] > 0:
                text = f"相对{LABELS[comparator]}，风险选择方案均值降低{p['mean_improvement_pp']:.3f}个百分点，但未同时满足实际改善或成本门槛，不足以宣称稳定优越。"
            else:
                text = f"相对{LABELS[comparator]}，风险选择方案主指标没有均值优势（对照减新方法{p['mean_improvement_pp']:.3f}个百分点）；该负结果保留，不能写成新方法更强。"
            add(text, 'E010', 'result', experiment)
        feasible = [a for a in cfg['arms'][experiment] if all(not j['arms'][a]['eligibility_failures'] for j in rows)]
        if feasible:
            winner = min(feasible, key=lambda a: (mean(a, lambda r: rate(r, 'union_equal_total_B')),
                                                  mean(a, lambda r: r['cost']['mean_extra_submissions'])))
            add(f'在全部五次成本合格的方法中，本次样本主指标最低的是{LABELS[winner]}。这是该实验条件下的描述性推荐，不是使用终测重新调参后得到的独立确认结论。', 'E010', 'result', experiment)
        if experiment == 'website':
            add('网站核心对动态的总差包含公共黑名单收益，动态独立贡献以网站核心加黑名单的共同基础作为参照；网站核心组成本合格标记仅表示本模拟增量协议，不等于真实网站体验验证。', 'E010,E011', 'interpretive', experiment)
    lines += ['', '## 7. 图表', '']
    for name, caption in [('literature_attack_curves', '文献机制的实际攻击曲线'),
                          ('website_attack_curves', '网站规则的实际攻击曲线'),
                          ('literature_cost_risk', '文献环境成本与风险'),
                          ('website_cost_risk', '网站环境成本与风险'),
                          ('paired_improvement', '配对百分点差与近似区间'),
                          ('threshold_trajectory', '动态流行度阈值轨迹')]:
        lines += [f'![{caption}](figures/{name}.png)', '']
    for experiment, rows in jobs.items():
        updates = [sum(d['status'] == 'confirmed_threshold_update' for d in j['arms'][TARGET]['decisions']) for j in rows]
        fallbacks = [sum(d['status'] == 'base_fallback_no_feasible_threshold' for d in j['arms'][TARGET]['decisions']) for j in rows]
        confirmations = [sum(d['status'] == 'hold_confirmation_failed' for d in j['arms'][TARGET]['decisions']) for j in rows]
        add(f"{'文献' if experiment == 'literature' else '网站'}环境按种子11、23、42、67、101依次统计：确认更新次数为{updates}，无可行候选的宽松回退次数为{fallbacks}，确认失败保持次数为{confirmations}。r=1表示宽松回退，不是强限频；文献环境的五次阈值轨迹重合。", 'E010', 'result', '轨迹')
    lines += ['## 8. 结论边界与后续优化', '']
    add('结果仅支持同一来源口令语料、声明的响应混合及两个有限预算猜测器下的模拟结论。未命中、PCFG模型外状态和更均匀的分布均不等于已证实安全；需要进一步扩展攻击器、行为模型与真实用户验证。', 'E010,E011', 'interpretive', '限制')
    add('本次较强刷新对照用于检验控制是否超出更新列表的收益；两篇文献对照检验是否超出已有在线机制。若攻击风险与修改成本有不同方向，应给出具体权衡，不能挑选攻击预算制造全面优势。', 'E010,E011', 'interpretive', '限制')
    add('下一步可在独立开发角色刷新策略条件化预测器、学习更真实的修补转移模型，并加入更强攻击器。当前没有这些实验数据，不能将其写成已经验证的贡献。隐私部署也需要独立设计，本地精确计数与明文模拟不构成线上隐私保护方案。', 'E011', 'interpretive', '限制')
    lines += ['', '## 9. 文献与软件流程', '']
    registry = json.loads((output/'source_manifest.json').read_text('utf-8'))
    for src in registry['sources']:
        if src['identifiers']['url']:
            add(f"- [{src['evidence_id']}: {src['title']}]({src['identifiers']['url']})。定位：{src['locator']}。", src['evidence_id'], 'factual', '参考文献')
            lines.append('')
    lines += ['Scientific Agent Skills 引用仅记录科研写作工作流，不作为口令算法优势证据。论文引用、署名和投稿信息待作者人工确认。', '']
    text = '\n'.join(lines)
    # Portable Markdown/math structural lint before saving.
    if sum(line.strip() == '$$' for line in lines) % 2 or text.count('```') % 2 or '\\(' in text or '\\[' in text:
        raise RuntimeError('Unbalanced or nonportable Markdown mathematics')
    (output/'注册策略对照实验报告.md').write_text(text, encoding='utf-8')
    readable = re.sub(r'\s*\[claim:[^\]]+\]\s*\[evidence:[^\]]+\]', '', text)
    (output/'注册策略对照实验报告_阅读版.md').write_text(readable, encoding='utf-8')
    with (output/'claims.csv').open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(claims[0]))
        writer.writeheader()
        writer.writerows(claims)
    write_json(output/'consistency_manifest.json', {'schema_version': '1.0', 'numeric_facts': facts,
        'methods': [{'method_id': 'M001', 'name': 'paired simulated registration and observed attacks',
                     'analysis_intent': 'descriptive', 'protocol_status': 'prespecified', 'outcome_ids': ['O001']}],
        'results': [{'result_id': 'R001', 'method_id': 'M001', 'outcome_id': 'O001',
                     'sample_size': 500000, 'analysis_intent': 'descriptive',
                     'evidence_ids': ['E010'], 'reported_sections': ['literature', 'website']}]})
    write_json(output/'evidence.json', {'comparisons': comparisons, 'rows': export,
               'source_result_paths': [str(root/'runs/test'/e/str(s)/'summary.json') for e in jobs for s in cfg['seeds']],
               'audit': audit_record, 'figures': figure_files, 'submission_ready': False})
    write_json(root/'deliverable_status.json', {'status': 'generated_machine_audited',
              'report': str(output/'注册策略对照实验报告.md'), 'human_source_review': 'pending',
              'visual_review': 'pending', 'submission_ready': False})
    print(json.dumps({'status': 'generated', 'rows': len(export), 'report': str(output/'注册策略对照实验报告.md')}, ensure_ascii=False))


if __name__ == '__main__':
    main(sys.argv[1])
