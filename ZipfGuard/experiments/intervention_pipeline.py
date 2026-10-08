"""Existing-account feedback experiments with independent adaptive references."""
from collections import Counter
from copy import deepcopy
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
from core.intervention_distribution import fitted_ideal_distance, fitted_top_mass, top_k
from core.ideal_distribution import METRIC as DISTANCE_METRIC, attach_ideal_analysis
from core.intervention_attack_area import GUARD_VERSION, curve_area, guard_batch, strength_diagnostics
from core.intervention_acceptance import AREA_ONLY_POLICY, INDIVIDUAL_POLICY, apply_response_policy
from core.intervention_risk import (InterventionRisk, CombinedInterventionRisk,
                                    reference_mutation_ranks, evaluate_mutations)
from experiments.intervention_config import ROOT, PROTOCOL, validate_intervention_config
from policy.intervention_response import InterventionResponder, RESPONSE_PROTOCOL, IndividualThresholdUnavailable
from policy.intervention_controller import (select_action, select_random_action,
                                            random_account_order, plan_once, predict_action)
from policy.local_actions import Action, MultiAction, account_order, capacities, generate_actions
from policy.user_response import satisfy_visible_rules, _rng
from policy.open_policy import edit_distance

METHODS = [('dynamic', '动态局部干预'), ('one_shot', '初始一次规划'),
           ('fixed_google', '固定 8 字符基础规则 分批')]
GOOGLE_DYNAMIC_METHOD = 'google_dynamic'
GOOGLE_DYNAMIC_LABEL = '8 字符基础规则 起点动态调整'
GOOGLE_DYNAMIC_ROUNDS = 10
GOOGLE_HOLD_METHOD = 'google_hold'
GOOGLE_RANDOM_METHOD = 'google_random'
GOOGLE_FROZEN_METHOD = 'google_frozen'
GOOGLE_METHODS = (GOOGLE_DYNAMIC_METHOD, GOOGLE_RANDOM_METHOD, GOOGLE_FROZEN_METHOD)
STOP_LABELS = {'target_reached': '达到配置中的风险目标', 'budget_exhausted': '累计干预预算用尽',
               'no_feasible_positive_gain_action': '当前候选没有可靠的正收益动作',
               'plan_exhausted': '初始计划已执行完', 'no_eligible_accounts': '没有剩余不合规账户',
               'realized_risk_stagnation': '连续多轮实现风险未改善', 'max_rounds': '达到轮数上限',
               'fixed_policy_hold': '8 字符基础规则 政策保持不变'}
STOP_LABELS['execution_plan_rejected'] = '模拟方案未通过攻击检查，未下发通知'


def make_index(counts, cfg):
    if cfg['attack_models']['mode'] == 'pcfg-omen-prefix':
        from ai.dual_attack import build_dual_index
        return build_dual_index(counts, cfg)
    grammar = Grammar.fit(counts, runtime_root=ROOT / 'reports' / 'intervention' / 'pcfg_runtime',
                          timeout=cfg['pcfg']['timeout_seconds'])
    return MonteCarloIndex(grammar, **cfg['monte_carlo'])


def make_selection_risk(population, frozen_risk, cfg, index_cache, baseline_counts=None):
    """Pair frozen F with an A1 PCFG retrained only on the independent reference."""
    counts = population.counts()
    key = counts_hash(counts)
    if key not in index_cache:
        index_cache[key] = make_index(counts, cfg)
    adaptive = InterventionRisk(index_cache[key], cfg['budgets'], cfg['risk_budget'])
    return CombinedInterventionRisk(frozen_risk, adaptive, baseline_counts=baseline_counts), key


def snapshot(population, evaluator, round_id):
    counts = population.counts()
    risk = evaluator.evaluate(counts)
    return {'round': round_id, 'state_sha256': population.fingerprint(),
            'account_state_sha256': population.account_fingerprint(),
            'ledger': population.ledger(), 'risk': risk, 'attack_log_area': curve_area(risk),
            'distribution': distribution_summary(counts),
            'google_compliance': google_compliance(population)}


def google_compliance(population):
    short = sum(len(account.password) < 8 for account in population.accounts)
    return {'required_minimum_length': 8,
            'minimum_observed_length': min(len(account.password) for account in population.accounts),
            'short_password_accounts': short, 'all_accounts_compliant': short == 0}


def assert_google_compliant(population):
    audit = google_compliance(population)
    if not audit['all_accounts_compliant']:
        raise AssertionError('8 字符基础规则 起点动态实验出现低于 8 字符的口令')
    return audit


def reference_replay(reference, action, target_total, responder, cfg, round_id, *, frozen_priority=None):
    """Known public action, independently simulated accounts; never target outcomes."""
    parts = action.components if isinstance(action, MultiAction) else (action,)
    used, replays, ledgers = set(), [], []
    cap = capacities(reference, cfg)
    for part in parts:
        eligible = [i for i, a in enumerate(reference.accounts)
                    if not a.adaptive_notifications and i not in used
                    and part.group.matches(a.password)
                    and (cfg['controller'].get('require_exact_target', False) or not part.rule.accepts(a.password))]
        fraction = (len(part.indices)/target_total if part.group.kind == 'random'
                    else len(part.indices)/part.eligible_count)
        take = min(len(eligible), math.floor((reference.total if part.group.kind == 'random'
                                            else len(eligible))*fraction + .5), cap-len(used))
        order = (random_account_order(reference, eligible, cfg['seed'], round_id)
                 if part.group.kind == 'random' else account_order(reference, eligible, cfg['seed']))
        if frozen_priority is not None:
            order.sort(key=lambda i: frozen_priority[i])
        ids = tuple(order[:take])
        if ids:
            used.update(ids)
            replays.append(Action(part.group, part.rule, part.rule_label, ids, len(eligible)))
        ledgers.append({'eligible_reference_accounts': len(eligible), 'selected_reference_accounts': take,
                        'target_selected_fraction': len(part.indices)/target_total,
                        'reference_selected_fraction': take/reference.total,
                        'selected_fraction_within_group': fraction})
    ledger = (ledgers[0] if len(parts) == 1 else {
        'eligible_reference_accounts': sum(row['eligible_reference_accounts'] for row in ledgers),
        'selected_reference_accounts': len(used), 'target_selected_fraction': len(action.indices)/target_total,
        'reference_selected_fraction': len(used)/reference.total, 'components': ledgers})
    if replays:
        # Evaluate compensation across all components together, never per fragment.
        replay = replays[0] if len(replays) == 1 else MultiAction(tuple(replays))
        outcomes = responder.respond(reference, replay, cfg['seed'], f'adaptive-reference-{round_id}')
        if getattr(responder, 'rank_model', None) is not None:
            outcomes, ledger['aggregate_attack_guard'] = apply_response_policy(
                reference, outcomes, responder.rank_model, cfg['budgets'], cfg['controller']['execution_policy'])
        reference.apply(outcomes)
    return ledger


def run_arm(method, initial, reference_words, evaluator, responder, cfg, progress=None,
            reference_initial=None, round_targets=None, selection_index_cache=None, checkpoint=None):
    if (method in (GOOGLE_RANDOM_METHOD, GOOGLE_FROZEN_METHOD) and round_targets is not None
            and cfg['controller']['execution_policy'] == INDIVIDUAL_POLICY):
        cfg = deepcopy(cfg)
        cfg['controller']['require_exact_target'] = True
    population = initial.clone()
    reference = reference_initial.clone() if reference_initial is not None else Population(reference_words, 'adaptive-reference')
    if method in GOOGLE_METHODS:
        if cfg['controller'].get('policy_floor_minimum_length', 0) < 8:
            raise AssertionError('8 字符基础规则 动态实验必须保留最低长度 8')
        assert_google_compliant(population)
        assert_google_compliant(reference)
    first = snapshot(population, evaluator, 0)
    trajectory, rounds = [first], []
    frozen_reference_priority = None
    if method == GOOGLE_FROZEN_METHOD and cfg['controller'].get('require_exact_target', False):
        reference_counts = reference.counts()
        order = random_account_order(reference, range(reference.total), cfg['seed'], 0)
        order.sort(key=lambda i: -reference_counts[reference.accounts[i].password])
        frozen_reference_priority = {i: rank for rank, i in enumerate(order)}
    if method == GOOGLE_FROZEN_METHOD:
        planning_cfg = deepcopy(cfg)
        planning_cfg['controller']['max_rounds'] = GOOGLE_DYNAMIC_ROUNDS
        if selection_index_cache is None:
            frozen_selection_risk, frozen_selection_model_key = evaluator, None
        else:
            frozen_selection_risk, frozen_selection_model_key = make_selection_risk(
                reference, evaluator, cfg, selection_index_cache, initial.counts())
        plan = plan_once(initial, frozen_selection_risk, responder, planning_cfg,
                         round_targets=round_targets)
    else:
        plan = plan_once(initial, evaluator, responder, cfg) if method == 'one_shot' else None
    target = first['risk']['guarded_risk']*(1-cfg['controller']['target_relative_reduction'])
    stagnant, stop = 0, 'max_rounds'
    terminal_audit = []
    unissued_proposals = []
    area_only = cfg['controller']['execution_policy'] == AREA_ONLY_POLICY
    round_limit = (0 if method == GOOGLE_HOLD_METHOD else
                   len(round_targets) if method in (GOOGLE_RANDOM_METHOD, GOOGLE_FROZEN_METHOD)
                   and round_targets is not None else GOOGLE_DYNAMIC_ROUNDS
                   if method in GOOGLE_METHODS else cfg['controller']['max_rounds'])
    method_label = ('8 字符基础规则 政策不变' if method == GOOGLE_HOLD_METHOD else GOOGLE_DYNAMIC_LABEL
                    if method == GOOGLE_DYNAMIC_METHOD else
                    '8 字符基础规则＋随机分批调整' if method == GOOGLE_RANDOM_METHOD else
                    '8 字符基础规则＋初始排序后分批执行' if method == GOOGLE_FROZEN_METHOD else
                    dict(METHODS)[method])
    for round_id in range(1, round_limit+1):
        if capacities(population, cfg) <= 0:
            stop = 'budget_exhausted'
            break
        if method not in (*GOOGLE_METHODS, GOOGLE_HOLD_METHOD) and trajectory[-1]['risk']['guarded_risk'] <= target:
            stop = 'target_reached'
            break
        if progress:
            progress(f'{method_label} · 第 {round_id} 轮：按当前口令分布比较动作')
        if method in (GOOGLE_DYNAMIC_METHOD, GOOGLE_RANDOM_METHOD) and selection_index_cache is not None:
            selection_risk, selection_model_key = make_selection_risk(
                reference, evaluator, cfg, selection_index_cache, initial.counts())
        elif method == GOOGLE_FROZEN_METHOD:
            selection_risk, selection_model_key = frozen_selection_risk, frozen_selection_model_key
        else:
            selection_risk, selection_model_key = evaluator, None
        distribution_rank = top_k(population, cfg)
        selection_before = fitted_ideal_distance(population.counts(), cfg['seed'])['score']
        primary_before = (selection_risk.primary_risk(population.counts())
                          if isinstance(selection_risk, CombinedInterventionRisk) else None)
        if method in ('one_shot', GOOGLE_FROZEN_METHOD):
            if round_id > len(plan):
                stop = 'plan_exhausted'
                break
            action, prediction, audit = plan[round_id-1]
        elif method == GOOGLE_RANDOM_METHOD:
            winner, audit = select_random_action(population, selection_risk, responder, cfg, round_id,
                                                 target_size=round_targets[round_id-1]
                                                 if round_targets is not None else None)
            if winner is None:
                terminal_audit = audit
                stop = 'no_feasible_positive_gain_action'
                break
            action, prediction = winner
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
            winner, audit = select_action(population, selection_risk, responder, cfg, round_id,
                                          target_size=capacities(population, cfg)
                                          if method == GOOGLE_DYNAMIC_METHOD else None)
            if winner is None:
                terminal_audit = audit
                stop = 'no_feasible_positive_gain_action'
                break
            action, prediction = winner
        if len(action.indices) > capacities(population, cfg):
            raise AssertionError('动作超出剩余硬预算')
        action_parts = action.components if isinstance(action, MultiAction) else (action,)
        if method in GOOGLE_METHODS and any(part.rule.base.min_length < 8 for part in action_parts):
            raise AssertionError('局部动作不能降低 8 字符基础规则 最低长度要求')
        previous = trajectory[-1]
        try:
            outcomes = responder.respond(population, action, cfg['seed'], f'execution-{round_id}')
        except IndividualThresholdUnavailable as exc:
            unissued_proposals.append({'planned_round': round_id, 'action': action.public(),
                                       'reason': str(exc), 'notified': 0})
            stop = 'execution_plan_rejected'
            break
        if cfg['response']['mode'] == 'all-notified-change-v1':
            if len(outcomes) != len(action.indices) or any(row['status'] != 'changed' or row['new'] == row['old'] for row in outcomes):
                raise AssertionError('所有被通知账户必须产生成功修改方案')
        if method in GOOGLE_METHODS and any(len(row['new']) < 8 for row in outcomes):
            raise AssertionError('动态响应产生了不符合 8 字符基础规则 规则的口令')
        batch_guard = None
        if method != 'fixed_google':
            outcomes, batch_guard = apply_response_policy(population, outcomes, evaluator, cfg['budgets'],
                                                         cfg['controller']['execution_policy'])
            if cfg['controller']['execution_policy'] in (AREA_ONLY_POLICY, INDIVIDUAL_POLICY) and not batch_guard['accepted']:
                unissued_proposals.append({'planned_round': round_id, 'action': action.public(),
                                           'prediction': prediction, 'candidate_audit': audit,
                                           'acceptance': batch_guard, 'notified': 0})
                stop = 'execution_plan_rejected'
                break
        strength = strength_diagnostics(outcomes, evaluator)
        population.apply(outcomes)
        adaptation = reference_replay(reference, action, initial.total, responder, cfg, round_id,
                                      frozen_priority=frozen_reference_priority)
        if method in GOOGLE_METHODS:
            assert_google_compliant(population)
            adaptation['google_compliance'] = assert_google_compliant(reference)
        current = snapshot(population, evaluator, round_id)
        if batch_guard is not None:
            batch_guard.update(before_area=previous['attack_log_area'],
                               after_area=current['attack_log_area'])
        selection_after = fitted_ideal_distance(population.counts(), cfg['seed'])['score']
        gain = selection_before - selection_after
        primary_after = (selection_risk.primary_risk(population.counts())
                         if isinstance(selection_risk, CombinedInterventionRisk) else None)
        rounds.append({'round': round_id, 'action': action.public(), 'prediction': prediction,
                       'candidate_count': len(audit), 'candidate_audit': audit,
                       'before_state_sha256': previous['state_sha256'],
                       'after_state_sha256': current['state_sha256'],
                       'realized_guarded_gain': gain,
                        'realized_distribution_gain': gain,
                        'distribution_score_metric': DISTANCE_METRIC,
                       'distribution_top_rank': distribution_rank,
                       'realized_primary_gain': (primary_before-primary_after
                                                 if primary_before is not None else None),
                       'bridge_action': bool(prediction.get('bridge')),
                       'selection_risk_before': selection_before,
                       'selection_risk_after_same_model': selection_after,
                       'selection_model_reference_sha256': selection_model_key,
                       'changed': sum(r['status'] == 'changed' for r in outcomes),
                       'explicit_completions': sum(bool(r.get('explicit_completion')) for r in outcomes),
                       'strength_rejections': sum(r.get('strength_rejections', 0) for r in outcomes),
                       'security_completion_attempts': sum(r.get('security_completion_attempts', 0) for r in outcomes),
                       'strength_improved': strength['improved'],
                       'strength_diagnostics': strength,
                       'aggregate_attack_guard': batch_guard,
                       'nonresponse': sum(r['status'] == 'nonresponse' for r in outcomes),
                       'failed_to_comply': sum(r['status'] == 'failed_to_comply' for r in outcomes),
                       'reference_replay': adaptation,
                        'selection_reason': ('先随机抽取当前可行动账户，再在相同的 18 条规则中分配预计有正收益的修改方法' if method == GOOGLE_RANDOM_METHOD
                                             else '只按 8 字符基础规则 起点一次性排好的账户和规则执行，不使用后续分布反馈' if method == GOOGLE_FROZEN_METHOD
                                              else ('按预计 W1 下降选动作；全员成功修改，固定 F 面积下降才下发方案'
                                                    if cfg['controller']['execution_policy'] == AREA_ONLY_POLICY else
                                                    '先比较 CDF 拟合完整排名累计分布，再要求整批修改后的固定 F 猜测成功曲线对数面积下降') if method == GOOGLE_DYNAMIC_METHOD
                                            else '在本轮可行候选中，预计全站保守风险下降最大' if method == 'dynamic'
                                            else '执行第 0 轮冻结的无重复账户计划' if method == 'one_shot'
                                            else '固定长度要求，优先选择预计命中的不合规账户')})
        trajectory.append(current)
        if checkpoint:
            checkpoint(method, {'method': method, 'label': method_label,
                                'rounds': rounds, 'trajectory': trajectory,
                                'adaptive_reference_ledger': reference.ledger(),
                                'status': 'running'})
        if progress:
            progress(f'{method_label} · 第 {round_id} 轮完成：通知 {len(action.indices):,} 人，'
                     f'修改 {sum(r["status"] == "changed" for r in outcomes):,} 人，'
                     f'短口令 {current["google_compliance"]["short_password_accounts"]:,} 个')
        stagnant = stagnant+1 if gain <= 0 else 0
        if method not in (*GOOGLE_METHODS,) and stagnant >= cfg['controller']['stagnation_patience']:
            stop = 'realized_risk_stagnation'
            break
    # The final execution is always retained, including at a round limit.
    if method not in (*GOOGLE_METHODS, GOOGLE_HOLD_METHOD) and trajectory[-1]['risk']['guarded_risk'] <= target:
        stop = 'target_reached'
    elif capacities(population, cfg) <= 0:
        stop = 'budget_exhausted'
    result = {'method': method, 'label': method_label, 'trajectory': trajectory,
              'rounds': rounds, 'stop_reason': stop, 'stop_label': STOP_LABELS[stop],
              'terminal_candidate_audit': terminal_audit, 'target_guarded_risk': target, 'target_reached': trajectory[-1]['risk']['guarded_risk'] <= target,
              'unissued_proposals': unissued_proposals,
              'final': trajectory[-1], 'adaptive_reference_ledger': reference.ledger(),
               'distribution_goal': {
                   'metric': DISTANCE_METRIC,
                   'top_rank': top_k(initial, cfg),
                   'start_fitted_distance': fitted_ideal_distance(initial.counts(), cfg['seed'])['score'],
                   'final_fitted_distance': fitted_ideal_distance(population.counts(), cfg['seed'])['score'],
                   'start_fitted_top_mass': fitted_top_mass(initial.counts(), top_k(initial, cfg), cfg['seed'])['top_mass'],
                  'final_fitted_top_mass': fitted_top_mass(population.counts(), top_k(initial, cfg), cfg['seed'])['top_mass']}}
    if checkpoint:
        checkpoint(method, {**result, 'status': 'complete'})
    return result, population.counts(), reference.counts()


def google_round_zipf_experiment(initial, reference_words, evaluator, responder, cfg,
                                  fixed_google=None, progress=None, checkpoint=None):
    """Give all four 8 字符基础规则 arms the exact same compliant starting state."""
    google_cfg = deepcopy(cfg)
    google_cfg['controller']['max_rounds'] = GOOGLE_DYNAMIC_ROUNDS
    bootstrap_cfg = deepcopy(cfg)
    bootstrap_cfg['controller']['round_fraction'] = 1.
    bootstrap_cfg['controller']['total_fraction'] = 1.
    bootstrap_response = dict(cfg['response'], nonresponse=0.)
    bootstrap_responder = InterventionResponder(Counter(reference_words), bootstrap_response)
    google_start = initial.clone()
    reference_start = Population(reference_words, 'google-baseline-reference')

    def apply_google_baseline(population, stream):
        choices = generate_actions(population, evaluator, bootstrap_cfg, fixed=True)
        if choices:
            action = choices[0]
            outcomes = bootstrap_responder.respond(population, action, cfg['seed'], stream)
            completions = sum(bool(row.get('explicit_completion')) for row in outcomes)
            # The common start is defined to be fully compliant. Finite response
            # attempts can still fail with zero refusal; explicitly construct a
            # compliant completion instead of claiming those failures complied.
            for row in outcomes:
                if row['status'] != 'changed':
                    account = population.accounts[row['index']]
                    new = satisfy_visible_rules(row['new'], min_length=8, classes=0,
                        rng=_rng(cfg['seed'], f'{stream}|completion|{account.identifier}'))
                    row.update(new=new, status='changed',
                        edit_cost=edit_distance(row['old'], new)/max(1, len(row['old']), len(new)))
                    completions += 1
            population.apply(outcomes, phase='google')
            failures = sum(row['status'] != 'changed' for row in outcomes)
        else:
            action, failures, completions = None, 0, 0
        short = sum(len(account.password) < 8 for account in population.accounts)
        if short:
            raise ValueError(f'8 字符基础规则 起始规则未能使全部账户合规，仍有 {short} 个短口令')
        return {'eligible': action.eligible_count if action else 0,
                'notified': len(action.indices) if action else 0,
                'changed': population.ledger()['changed'],
                'failures': failures,
                'explicit_length_completions': completions,
                'affected_rate': population.ledger()['affected_rate'],
                'short_passwords_remaining': short,
                'all_accounts_compliant': short == 0,
                'rule': '8 字符基础规则 最低长度 8 字符'}

    bootstrap_target = apply_google_baseline(google_start, 'google-baseline-target')
    bootstrap_reference = apply_google_baseline(reference_start, 'google-baseline-reference')
    if bootstrap_target['failures'] or bootstrap_reference['failures']:
        raise AssertionError('全量 8 字符基础规则 起点存在未完成的账户修改')

    # The configured total budget applies to the ten adaptive rounds after the
    # 8 字符基础规则 baseline. Count the full-population 8 字符基础规则 migration separately.
    google_cfg['controller']['total_fraction'] = cfg['controller']['total_fraction']
    google_cfg['controller']['policy_floor_minimum_length'] = 8
    google_cfg['controller']['distribution_top_k'] = max(
        1, math.ceil(len(google_start.counts()) * cfg['controller']['distribution_top_fraction']))
    control_snapshot = snapshot(google_start, evaluator, 0)
    control = {'method': GOOGLE_HOLD_METHOD, 'label': '8 字符基础规则 政策不变',
               'trajectory': [control_snapshot], 'rounds': [],
               'stop_reason': 'fixed_policy_hold', 'stop_label': STOP_LABELS['fixed_policy_hold'],
               'terminal_candidate_audit': [], 'final': control_snapshot,
               'adaptive_reference_ledger': reference_start.ledger(),
               'google_baseline': bootstrap_target}
    # Candidate selection uses the distribution alone. A1 is trained only for
    # the independent endpoint evaluation, not repeatedly inside each round.
    selection_index_cache = None
    # Fix the notification schedule before running any arm. Each method may
    # stop early if no feasible action exists; unused budget is disclosed.
    per_round = math.floor(initial.total*cfg['controller']['round_fraction'] + 1e-9)
    remaining = math.floor(initial.total*cfg['controller']['total_fraction'] + 1e-9)
    round_targets = []
    for _ in range(GOOGLE_DYNAMIC_ROUNDS):
        if remaining <= 0:
            break
        take = min(per_round, remaining)
        round_targets.append(take)
        remaining -= take
    experimental, final_counts, final_reference = run_arm(
        GOOGLE_DYNAMIC_METHOD, google_start, reference_words, evaluator, responder,
        google_cfg, progress, reference_initial=reference_start,
        selection_index_cache=selection_index_cache, checkpoint=checkpoint)
    experimental['google_baseline'] = bootstrap_target
    if cfg['controller']['execution_policy'] == INDIVIDUAL_POLICY:
        round_targets = [row['action']['selected'] for row in experimental['rounds']]
    random_arm, random_counts, random_reference = run_arm(
        GOOGLE_RANDOM_METHOD, google_start, reference_words, evaluator, responder,
        google_cfg, progress, reference_initial=reference_start,
        round_targets=round_targets, selection_index_cache=selection_index_cache, checkpoint=checkpoint)
    frozen_arm, frozen_counts, frozen_reference = run_arm(
        GOOGLE_FROZEN_METHOD, google_start, reference_words, evaluator, responder,
        google_cfg, progress, reference_initial=reference_start,
        round_targets=round_targets, selection_index_cache=selection_index_cache, checkpoint=checkpoint)
    control_counts = google_start.counts()
    control_reference = reference_start.counts()
    start_index = 0
    common_start = (control['final']['account_state_sha256'] ==
                    experimental['trajectory'][start_index]['account_state_sha256'])
    if not common_start or any(arm['trajectory'][0]['account_state_sha256'] !=
                               control['final']['account_state_sha256']
                                for arm in (random_arm, frozen_arm)):
        raise AssertionError('四组 8 字符基础规则 实验的账户起点不一致')
    google_start_snapshot = control['final']
    train = Counter(reference_words)
    mutations = reference_mutation_ranks(train, cfg['evaluation']['mutation_reference_limit'])
    adaptive_fits = selection_index_cache if selection_index_cache is not None else {}
    for arm, counts, reference_counts in (
            (control, control_counts, control_reference),
            (random_arm, random_counts, random_reference),
            (frozen_arm, frozen_counts, frozen_reference),
            (experimental, final_counts, final_reference)):
        arm['attacks'] = {'F': arm['final']['risk'], 'A1': None,
                          'reference_mutations': evaluate_mutations(counts, mutations, cfg['budgets'])}
        if cfg['evaluation']['adaptive']:
            if progress:
                progress(arm['label'] + ' · 在独立参考群体上重训 A1')
            key = counts_hash(reference_counts)
            if key not in adaptive_fits:
                adaptive_fits[key] = make_index(reference_counts, cfg)
            ai = adaptive_fits[key]
            arm['attacks']['A1'] = InterventionRisk(ai, cfg['budgets'], cfg['risk_budget']).evaluate(counts)
            arm['adaptive_model'] = dict(ai.grammar.metadata)
    return {
        'protocol': 'google-started-five-arm-comparison-v8-area-only-full-response',
        'requested_rounds': GOOGLE_DYNAMIC_ROUNDS,
        'common_google_start_verified': common_start,
        'google_baseline': {'target': bootstrap_target, 'reference': bootstrap_reference,
                            'interpretation': '全体账户先执行 8 字符基础规则 最低 8 字符规则；基线迁移成本单独计入覆盖率'},
        'arms': {GOOGLE_HOLD_METHOD: control, GOOGLE_RANDOM_METHOD: random_arm,
                 GOOGLE_FROZEN_METHOD: frozen_arm, GOOGLE_DYNAMIC_METHOD: experimental},
        'comparison_budget': {'per_round_fraction': cfg['controller']['round_fraction'],
                               'distribution_top_k': google_cfg['controller']['distribution_top_k'],
                               'additional_fraction_after_google': cfg['controller']['total_fraction'],
                               'planned_round_notification_schedule': round_targets,
                               'dynamic_round_notification_schedule':
                               [row['action']['selected'] for row in experimental['rounds']],
                               'controls_request_same_round_sizes': cfg['controller']['execution_policy'] == INDIVIDUAL_POLICY,
                               'matched_notification_counts': {
                                   key: arm['final']['ledger']['adaptive_affected'] == experimental['final']['ledger']['adaptive_affected']
                                   for key, arm in [('google_random', random_arm), ('google_frozen', frozen_arm)]},
                              'random_only_notifies_accounts_ineligible_for_selected_rule':
                              cfg['controller']['execution_policy'] != INDIVIDUAL_POLICY,
                              'dynamic_selection_uses_updated_independent_reference_a1': bool(selection_index_cache),
                              'actual_coverage_may_differ': True,
                               'cost_axis': '8 字符基础规则 共同起点后累计通知的不同账户比例；8 字符基础规则 起点迁移成本另列'},
        'control': {
            'label': '8 字符基础规则 政策不变',
            'rounds_completed': len(control['rounds']),
            'state_sha256': google_start_snapshot['state_sha256'],
            'affected_rate': google_start_snapshot['ledger']['affected_rate'],
            'distribution': google_start_snapshot['distribution'],
        },
        'experimental': {
            'label': GOOGLE_DYNAMIC_LABEL,
            'start_state_sha256': experimental['trajectory'][start_index]['state_sha256'],
            'rounds_completed': len(experimental['rounds']),
            'stop_reason': experimental['stop_reason'],
            'stop_label': experimental['stop_label'],
            'snapshots': [
                {
                    'round': row['round'],
                    'action': row['action'],
                    'affected_rate': experimental['trajectory'][row['round']]['ledger']['affected_rate'],
                    'distribution': experimental['trajectory'][row['round']]['distribution'],
                    'google_compliance': experimental['trajectory'][row['round']]['google_compliance'],
                }
                for row in experimental['rounds']
            ],
        },
    }


def run_intervention_pipeline(config, *, dataset=None, index=None, output_dir=None, progress=None,
                              include_legacy=False, checkpoint=None):
    cfg = validate_intervention_config(config)
    area_only = cfg['controller']['execution_policy'] == AREA_ONLY_POLICY
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
        progress('训练并准备冻结攻击索引：' + cfg['attack_models']['mode'])
    frozen_index = index if index is not None else make_index(train, cfg)
    evaluator = InterventionRisk(frozen_index, cfg['budgets'], cfg['risk_budget'])
    response_cfg = dict(cfg['response'])
    if cfg['controller']['execution_policy'] == INDIVIDUAL_POLICY:
        response_cfg['security_threshold'] = cfg['attack_models']['threshold']
        if not getattr(frozen_index, 'dual_attack', False):
            raise ValueError('个体门槛运行必须提供真实双攻击前缀')
    responder = InterventionResponder(train, response_cfg, rank_model=evaluator)
    baseline = snapshot(initial, evaluator, 0)
    mutations = reference_mutation_ranks(train, cfg['evaluation']['mutation_reference_limit'])
    arms, adaptive_fits = {}, {counts_hash(train): frozen_index}
    for method, label in (METHODS if include_legacy else []):
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
    round_zipf = google_round_zipf_experiment(initial, reference_words, evaluator, responder,
                                              cfg, arms.get('fixed_google'), progress, checkpoint=checkpoint)
    manifest_files = ['experiments/intervention_config.py', 'experiments/intervention_pipeline.py',
                      'core/intervention_state.py', 'core/intervention_risk.py', 'policy/local_actions.py',
                      'policy/intervention_fragments.py',
                      'policy/intervention_controller.py', 'policy/intervention_response.py',
                      'ai/pcfg_monte_carlo.py', 'policy/user_response.py', 'policy/open_policy.py',
                      'core/registration.py', 'core/monte_carlo_attack.py',
                      'core/cdf_sampling.py', 'core/distribution_analysis.py',
                      'core/intervention_distribution.py',
                      'core/intervention_attack_area.py', 'core/ideal_distribution.py',
                      'core/intervention_acceptance.py',
                      'experiments/intervention_site_controls.py', 'policy/yahoo_japan.py',
                      'experiments/cdf_fit_benchmark.py', 'ai/dual_attack.py']
    source_hashes = {f: hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in manifest_files}
    identity = {'config': cfg, 'dataset': dataset['metadata'], 'sources': source_hashes,
                'include_legacy': include_legacy}
    run_id = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    report = {'schema_version': PROTOCOL+'-result', 'config': cfg, 'dataset': dataset['metadata'],
              'baseline': baseline, 'arms': round_zipf['arms'], 'google_round_zipf': round_zipf,
              'baseline_mutations': evaluate_mutations(initial.counts(), mutations, cfg['budgets']),
              'metadata': {'run_id': run_id, 'protocol': PROTOCOL, 'response_protocol': RESPONSE_PROTOCOL,
                           'risk_method': VERSION, 'frozen_model': dict(frozen_index.grammar.metadata),
                           'python': platform.python_version(), 'source_hashes': source_hashes,
                           'runtime_seconds': time.perf_counter()-started,
                           'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                           'scope': ('真实频次初始抽样；8 字符基础规则 起点；1—18 条片段候选；全员成功修改模拟；PCFG 蒙特卡洛估计'
                                     if area_only else '真实频次初始抽样；8 字符基础规则 起点；1—18 条片段候选；有限次模拟响应；PCFG 蒙特卡洛估计'),
                           'candidate_pool': cfg['controller']['candidate_pool'],
                           'a0_status': '未运行：局部响应状态未知时不全站套用规则掩码',
                           'adaptive_reference': '独立开发训练群体按公开群体筛选器及群体内干预比例迁移；非目标终态训练',
                           'aggregate_attack_guard': {
                               'protocol': (AREA_ONLY_POLICY if cfg['controller']['execution_policy'] == AREA_ONLY_POLICY else GUARD_VERSION),
                               'enforced': True, 'model': 'fixed F',
                               'integration': 'normalized trapezoid on log10 budget',
                               'budgets': cfg['budgets'],
                               'outside_support': ('diagnostic only; not a rejection condition' if area_only else 'population share must not increase'),
                               'execution': ('simulate full success, issue and charge notifications only if F area decreases'
                                             if cfg['controller']['execution_policy'] == AREA_ONLY_POLICY else
                                             'collect proposals, reject the whole batch if area does not decrease; notifications still count')},
                           'decision_metric': ('以预计 W1 距离下降选动作；全员成功修改方案仅要求固定 F 面积严格下降才下发；未覆盖比例只作诊断；A1 独立评价'
                                               if cfg['controller']['execution_policy'] == AREA_ONLY_POLICY else
                                               '以 CDF 拟合分布到固定 N 单例理想分布的对数排名 Wasserstein-1 距离下降选动作；整批修改要求固定 F 猜测成功曲线对数面积下降、模型未覆盖比例不增加；分布与猜测不加权')}}
    if cfg['controller']['execution_policy'] == INDIVIDUAL_POLICY:
        from ai.dual_attack import VERSION as DUAL_VERSION
        report['metadata'].update(
            risk_method=DUAL_VERSION, response_protocol='all-notified-dual-threshold-v1',
            scope='8 字符共同起点；分布选群体；双攻击个体门槛；实际候选前缀',
            attack_budget='B raw attempts per model; union <= 2B attempts; overlaps counted once',
            attack_sources=frozen_index.metadata['sources'],
            decision_metric='个体通过 PCFG 和 OMEN 各 B 次检查后，按预计 W1 距离下降选择群体',
            aggregate_attack_guard={'protocol': INDIVIDUAL_POLICY, 'enforced': True,
                                    'model': 'fixed PCFG + OMEN union',
                                    'threshold_per_model': cfg['attack_models']['threshold'],
                                    'outside_support': 'both models outside domain: reject',
                                    'execution': 'individual threshold; attack area diagnostic only'})
        round_zipf['protocol'] = 'same-start-dynamic-realized-budget-dual-individual-v1'
        for arm in report['arms'].values():
            for row in arm['rounds']:
                row['selection_reason'] = ('在个体双攻击门槛约束下，按当前分布选择群体' if arm['method'] == GOOGLE_DYNAMIC_METHOD
                                           else '使用相同个体双攻击门槛，并申请动态组实际逐轮通知预算')
    if progress:
        progress('使用 CDF 采样方法拟合各组终态，独立种子复核')
    if include_legacy:
        report['legacy_arms'] = arms
    if cfg['evaluation']['yahoo_control']:
        from experiments.intervention_site_controls import run_yahoo_control
        report['site_controls'] = {'yahoo_japan': run_yahoo_control(
            initial, reference_words, evaluator, cfg, progress)}
    from experiments.cdf_fit_benchmark import (intervention_fit_diagnostics,
                                               intervention_round_parameter_diagnostics)
    report['distribution_fits'] = intervention_fit_diagnostics(report)
    if progress:
        progress('拟合 8 字符基础规则 起点和每轮动态调整后的分布参数')
    report['round_parameter_fits'] = intervention_round_parameter_diagnostics(report)
    attach_ideal_analysis(report)
    report['metadata']['runtime_seconds'] = time.perf_counter()-started
    directory = Path(output_dir) if output_dir is not None else ROOT/'reports'/'intervention'/run_id
    directory.mkdir(parents=True, exist_ok=True)
    from web.intervention_presentation import render_intervention_html, export_intervention_figures
    content = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n'
    (directory/'report.json').write_text(content, encoding='utf-8')
    (directory/'report.json.sha256').write_text(hashlib.sha256((directory/'report.json').read_bytes()).hexdigest()+'  report.json\n', encoding='utf-8')
    (directory/'report.html').write_text(render_intervention_html(report), encoding='utf-8')
    export_intervention_figures(report, directory)
    return report
