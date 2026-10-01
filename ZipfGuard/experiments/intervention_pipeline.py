"""Existing-account feedback experiments with independent adaptive references."""
from collections import Counter
import hashlib
import json
import math
import platform
import time
from pathlib import Path

from ai.pcfg_monte_carlo import Grammar, MonteCarloIndex, VERSION
from core.corpus import counts_hash
from core.registration import load_registration
from core.intervention_state import Population, distribution_summary
from core.intervention_risk import InterventionRisk, reference_mutation_ranks, evaluate_mutations
from experiments.intervention_config import ROOT, PROTOCOL, validate_intervention_config
from policy.intervention_response import InterventionResponder, RESPONSE_PROTOCOL
from policy.intervention_controller import select_action, plan_once, predict_action
from policy.local_actions import Action, account_order, capacities, generate_actions

METHODS = [('dynamic', '动态局部干预'), ('one_shot', '初始一次规划'),
           ('fixed_google', '固定 Google 分批')]
STOP_LABELS = {'target_reached': '达到配置中的风险目标', 'budget_exhausted': '累计干预预算用尽',
               'no_feasible_positive_gain_action': '当前候选没有可靠的正收益动作',
               'plan_exhausted': '初始计划已执行完', 'no_eligible_accounts': '没有剩余不合规账户',
               'realized_risk_stagnation': '连续多轮实现风险未改善', 'max_rounds': '达到轮数上限'}


def make_index(counts, cfg):
    grammar = Grammar.fit(counts, runtime_root=ROOT / 'reports' / 'intervention' / 'pcfg_runtime',
                          timeout=cfg['pcfg']['timeout_seconds'])
    return MonteCarloIndex(grammar, **cfg['monte_carlo'])


def snapshot(population, evaluator, round_id):
    counts = population.counts()
    return {'round': round_id, 'state_sha256': population.fingerprint(),
            'ledger': population.ledger(), 'risk': evaluator.evaluate(counts),
            'distribution': distribution_summary(counts)}


def reference_replay(reference, action, target_total, responder, cfg, round_id):
    """Known public action, independently simulated accounts; never target outcomes."""
    eligible = [i for i, a in enumerate(reference.accounts)
                if not a.notifications and action.group.matches(a.password)
                and not action.rule.accepts(a.password)]
    # Match selected fraction within the independently observed eligible group.
    fraction = len(action.indices)/action.eligible_count
    take = min(len(eligible), math.floor(len(eligible)*fraction + .5), capacities(reference, cfg))
    ids = tuple(account_order(reference, eligible, cfg['seed'])[:take])
    if ids:
        replay = Action(action.group, action.rule, action.rule_label, ids, len(eligible))
        reference.apply(responder.respond(reference, replay, cfg['seed'], f'adaptive-reference-{round_id}'))
    return {'eligible_reference_accounts': len(eligible), 'selected_reference_accounts': take,
            'target_selected_fraction': len(action.indices)/target_total,
            'reference_selected_fraction': take/reference.total,
            'selected_fraction_within_group': fraction}


def run_arm(method, initial, reference_words, evaluator, responder, cfg, progress=None):
    population = initial.clone()
    reference = Population(reference_words, 'adaptive-reference')
    first = snapshot(population, evaluator, 0)
    trajectory, rounds = [first], []
    plan = plan_once(initial, evaluator, responder, cfg) if method == 'one_shot' else None
    target = first['risk']['guarded_risk']*(1-cfg['controller']['target_relative_reduction'])
    stagnant, stop = 0, 'max_rounds'
    terminal_audit = []
    for round_id in range(1, cfg['controller']['max_rounds']+1):
        if capacities(population, cfg) <= 0:
            stop = 'budget_exhausted'
            break
        if trajectory[-1]['risk']['guarded_risk'] <= target:
            stop = 'target_reached'
            break
        if progress:
            progress(f'{dict(METHODS)[method]} · 第 {round_id} 轮：比较局部任务')
        if method == 'one_shot':
            if round_id > len(plan):
                stop = 'plan_exhausted'
                break
            action, prediction, audit = plan[round_id-1]
        elif method == 'fixed_google':
            choices = generate_actions(population, evaluator, cfg, fixed=True)
            if not choices:
                stop = 'no_eligible_accounts'
                break
            action = choices[0]
            prediction = predict_action(population, action, evaluator, responder, cfg, round_id)
            audit = [{'action': action.public(), **prediction}]
            # Fixed-policy baseline is deliberately not selected by predicted gain.
        else:
            winner, audit = select_action(population, evaluator, responder, cfg, round_id)
            if winner is None:
                terminal_audit = audit
                stop = 'no_feasible_positive_gain_action'
                break
            action, prediction = winner
        if len(action.indices) > capacities(population, cfg):
            raise AssertionError('动作超出剩余硬预算')
        previous = trajectory[-1]
        outcomes = responder.respond(population, action, cfg['seed'], f'execution-{round_id}')
        population.apply(outcomes)
        adaptation = reference_replay(reference, action, initial.total, responder, cfg, round_id)
        current = snapshot(population, evaluator, round_id)
        gain = previous['risk']['guarded_risk'] - current['risk']['guarded_risk']
        rounds.append({'round': round_id, 'action': action.public(), 'prediction': prediction,
                       'candidate_count': len(audit), 'candidate_audit': audit,
                       'before_state_sha256': previous['state_sha256'],
                       'after_state_sha256': current['state_sha256'],
                       'realized_guarded_gain': gain,
                       'changed': sum(r['status'] == 'changed' for r in outcomes),
                       'nonresponse': sum(r['status'] == 'nonresponse' for r in outcomes),
                       'failed_to_comply': sum(r['status'] == 'failed_to_comply' for r in outcomes),
                       'reference_replay': adaptation,
                       'selection_reason': ('在本轮可行候选中，每新增受影响账户的保守收益最高' if method == 'dynamic'
                                            else '执行第 0 轮冻结的无重复账户计划' if method == 'one_shot'
                                            else '固定长度要求，优先选择预计命中的不合规账户')})
        trajectory.append(current)
        stagnant = stagnant+1 if gain <= 0 else 0
        if stagnant >= cfg['controller']['stagnation_patience']:
            stop = 'realized_risk_stagnation'
            break
    # The final execution is always retained, including at a round limit.
    if trajectory[-1]['risk']['guarded_risk'] <= target:
        stop = 'target_reached'
    elif capacities(population, cfg) <= 0:
        stop = 'budget_exhausted'
    result = {'method': method, 'label': dict(METHODS)[method], 'trajectory': trajectory,
              'rounds': rounds, 'stop_reason': stop, 'stop_label': STOP_LABELS[stop],
              'terminal_candidate_audit': terminal_audit, 'target_guarded_risk': target, 'target_reached': trajectory[-1]['risk']['guarded_risk'] <= target,
              'final': trajectory[-1], 'adaptive_reference_ledger': reference.ledger()}
    return result, population.counts(), reference.counts()


def run_intervention_pipeline(config, *, dataset=None, index=None, output_dir=None, progress=None):
    cfg = validate_intervention_config(config)
    started = time.perf_counter()
    if dataset is None:
        d = cfg['data']
        source = Path(d['path'])
        if not source.is_absolute():
            source = ROOT/source
        dataset = load_registration(source, source_format=d['format'], encoding=d['encoding'],
                                    users=d['users'], development=d['development'], seed=cfg['seed'],
                                    cohort_size=d['users'], progress=progress)
    words = [w for batch in dataset['cohorts'] for w in batch]
    if len(words) != cfg['data']['users']:
        raise ValueError('输入账户数与配置不符')
    train = dataset['development']['train']
    reference_words = [w for w, n in sorted(train.items()) for _ in range(n)]
    initial = Population(words)
    if progress:
        progress('训练冻结 PCFG，建立蒙特卡洛查询索引')
    frozen_index = index if index is not None else make_index(train, cfg)
    evaluator = InterventionRisk(frozen_index, cfg['budgets'], cfg['risk_budget'])
    responder = InterventionResponder(train, cfg['response'])
    baseline = snapshot(initial, evaluator, 0)
    mutations = reference_mutation_ranks(train, cfg['evaluation']['mutation_reference_limit'])
    arms, adaptive_fits = {}, {counts_hash(train): frozen_index}
    for method, label in METHODS:
        result, final_counts, reference_counts = run_arm(method, initial, reference_words, evaluator, responder, cfg, progress)
        result['attacks'] = {'F': result['final']['risk'],
                             'reference_mutations': evaluate_mutations(final_counts, mutations, cfg['budgets'])}
        if cfg['evaluation']['adaptive']:
            if progress:
                progress(label + ' · 在独立参考群体上重训 A1')
            key = counts_hash(reference_counts)
            if key not in adaptive_fits:
                adaptive_fits[key] = make_index(reference_counts, cfg)
            ai = adaptive_fits[key]
            result['attacks']['A1'] = InterventionRisk(ai, cfg['budgets'], cfg['risk_budget']).evaluate(final_counts)
            result['adaptive_model'] = dict(ai.grammar.metadata)
        else:
            result['attacks']['A1'] = None
        arms[method] = result
    manifest_files = ['experiments/intervention_config.py', 'experiments/intervention_pipeline.py',
                      'core/intervention_state.py', 'core/intervention_risk.py', 'policy/local_actions.py',
                      'policy/intervention_controller.py', 'policy/intervention_response.py',
                      'ai/pcfg_monte_carlo.py', 'policy/user_response.py', 'policy/open_policy.py',
                      'core/registration.py', 'core/monte_carlo_attack.py']
    source_hashes = {f: hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in manifest_files}
    identity = {'config': cfg, 'dataset': dataset['metadata'], 'sources': source_hashes}
    run_id = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    report = {'schema_version': PROTOCOL+'-result', 'config': cfg, 'dataset': dataset['metadata'],
              'baseline': baseline, 'arms': arms,
              'baseline_mutations': evaluate_mutations(initial.counts(), mutations, cfg['budgets']),
              'metadata': {'run_id': run_id, 'protocol': PROTOCOL, 'response_protocol': RESPONSE_PROTOCOL,
                           'risk_method': VERSION, 'frozen_model': dict(frozen_index.grammar.metadata),
                           'python': platform.python_version(), 'source_hashes': source_hashes,
                           'runtime_seconds': time.perf_counter()-started,
                           'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                           'scope': '真实频次初始抽样；有限次模拟响应；PCFG 蒙特卡洛估计',
                           'a0_status': '未运行：局部响应状态未知时不全站套用规则掩码',
                           'adaptive_reference': '独立开发训练群体按公开群体筛选器及群体内干预比例迁移；非目标终态训练',
                           'decision_metric': 'PCFG 估计命中率 + 模型未覆盖比例；仅作保守选择代理，不是实测破解率或置信上界'}}
    directory = Path(output_dir) if output_dir is not None else ROOT/'reports'/'intervention'/run_id
    directory.mkdir(parents=True, exist_ok=True)
    from web.intervention_presentation import render_intervention_html, export_intervention_figures
    content = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n'
    (directory/'report.json').write_text(content, encoding='utf-8')
    (directory/'report.json.sha256').write_text(hashlib.sha256(content.encode()).hexdigest()+'  report.json\n', encoding='utf-8')
    (directory/'report.html').write_text(render_intervention_html(report), encoding='utf-8')
    export_intervention_figures(report, directory)
    return report
