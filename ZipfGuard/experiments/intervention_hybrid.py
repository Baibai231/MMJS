"""One pre-specified Yahoo/local hybrid; evaluate the entire mixed population."""
from collections import Counter
from copy import deepcopy
import hashlib
import math

from core.intervention_state import Population
from core.intervention_risk import InterventionRisk
from policy.intervention_response import InterventionResponder
from policy.local_actions import Action, Group, generate_actions
from policy.yahoo_japan import YahooJapanRule

METHOD = 'yahoo_dynamic_hybrid'
VERSION = 'yahoo-frequency-first-local-v1'


def yahoo_indices(population, fraction, seed):
    """Frequency screen, not a prediction of globally optimal W1 improvement."""
    if not 0 <= fraction <= 1:
        raise ValueError('Yahoo 分组比例应在 0—1 之间')
    counts, rule = population.counts(), YahooJapanRule()
    eligible = [i for i, a in enumerate(population.accounts) if not rule.accepts(a.password)]
    eligible.sort(key=lambda i: (-counts[population.accounts[i].password],
        hashlib.sha256(f'{seed}|hybrid-yahoo|{population.accounts[i].identifier}'.encode()).digest()))
    return tuple(eligible[:math.floor(population.total*fraction+1e-9)])


def prepare_hybrid(population, reference_words, evaluator, cfg, fraction, *, reference=False):
    """Strong phase consumes adaptive flags to reserve users; costs remain explicit."""
    strong = yahoo_indices(population, fraction, cfg['seed'])
    rule = YahooJapanRule()
    responder = InterventionResponder(Counter(reference_words), dict(cfg['response'], nonresponse=0.))
    stream = 'yahoo-japan-reference' if reference else 'yahoo-japan-target'
    if strong:
        action = Action(Group('all', '', '重复频次较高、未满足 Yahoo! 的账户'), rule,
                        'Yahoo! JAPAN 公开强规则', strong, len(strong))
        rows = responder.respond(population, action, cfg['seed'], stream)
        if any(r['status'] != 'changed' or not rule.accepts(r['new']) for r in rows):
            raise AssertionError('Yahoo 分组必须全部完成合规修改')
        population.apply(rows)
    else:
        rows = []
    strong_passwords = {i: population.accounts[i].password for i in strong}
    baseline_cfg = deepcopy(cfg)
    baseline_cfg['controller'].update(round_fraction=1., total_fraction=1.)
    choices = generate_actions(population, evaluator, baseline_cfg, fixed=True)
    baseline_rows = []
    if choices:
        baseline_rows = responder.respond(population, choices[0], cfg['seed'],
                    'google-baseline-reference' if reference else 'google-baseline-target')
        if any(r['status'] != 'changed' or len(r['new']) < 8 for r in baseline_rows):
            raise AssertionError('剩余账户基础规则修改未完成')
        population.apply(baseline_rows, phase='google')
    assert all(len(a.password) >= 8 for a in population.accounts)
    assert set(strong).isdisjoint(r['index'] for r in baseline_rows)
    return {'strong_assigned': len(strong), 'strong_modified': len(rows),
            'strong_rate': len(strong)/population.total,
            'strong_explicit_completions': sum(bool(r.get('explicit_completion')) for r in rows),
            'remaining_accounts': population.total-len(strong),
            'baseline_modified': len(baseline_rows),
            'strong_noncompliant': sum(not rule.accepts(w) for w in strong_passwords.values())}, strong_passwords


def dynamic_config(cfg, n, strong_count):
    """Use original N for W1, but local budgets are fractions of the remainder."""
    result = deepcopy(cfg)
    remaining = n-strong_count
    scale = remaining/n
    c = result['controller']
    per_round = math.floor(remaining*cfg['controller']['round_fraction']+1e-9)
    additional = math.floor(remaining*cfg['controller']['total_fraction']+1e-9)
    c.update(round_fraction=per_round/n, total_fraction=(strong_count+additional)/n,
             batch_fractions=[f*scale for f in c['batch_fractions']],
             min_batch_fraction=c['min_batch_fraction']*scale,
             policy_floor_minimum_length=8)
    return result, {'remaining_accounts': remaining, 'round_cap': per_round,
                    'total_local_cap': additional, 'maximum_rounds': 10,
                    'round_fraction_of_remaining': cfg['controller']['round_fraction'],
                    'total_fraction_of_remaining': cfg['controller']['total_fraction']}


def run_hybrid(initial, reference_words, evaluator, cfg, *, fraction=.30,
               progress=None, checkpoint=None):
    from experiments.intervention_pipeline import run_arm, make_index, GOOGLE_DYNAMIC_METHOD
    if cfg['response']['mode'] != 'all-notified-change-v1':
        raise ValueError('混合组要求与当前全员完成响应协议相同')
    if not getattr(evaluator.index, 'dual_attack', False):
        raise ValueError('混合组须使用当前真实 PCFG 与 OMEN 前缀')
    target = initial.clone()
    reference = Population(reference_words, 'yahoo-reference')
    if progress:
        progress('混合组：按原始完整口令频次选择 Yahoo! 分组，随后处理剩余账户的基础规则')
    target_audit, locked = prepare_hybrid(target, reference_words, evaluator, cfg, fraction)
    reference_audit, _ = prepare_hybrid(reference, reference_words, evaluator, cfg, fraction, reference=True)
    local_cfg, limits = dynamic_config(cfg, target.total, len(locked))
    responder = InterventionResponder(Counter(reference_words),
        dict(cfg['response'], security_threshold=cfg['attack_models']['threshold']), evaluator)
    if limits['round_cap'] < 1:
        raise ValueError('剩余账户不足以执行一轮；请降低 Yahoo 比例')
    def record(method, payload):
        if checkpoint:
            checkpoint(METHOD, payload)
    arm, counts, reference_counts = run_arm(GOOGLE_DYNAMIC_METHOD, target, reference_words,
        evaluator, responder, local_cfg, progress, reference_initial=reference, checkpoint=record)
    # run_arm clones populations; with strong flags locked, any change to them
    # would also violate the duplicate-phase invariant. Check the dynamic costs.
    local_modified = arm['final']['ledger']['adaptive_affected']-len(locked)
    assert 0 <= local_modified <= limits['total_local_cap']
    assert all(r['action']['selected'] <= limits['round_cap'] for r in arm['rounds'])
    assert local_modified == sum(r['changed'] for r in arm['rounds'])
    arm.update(method=METHOD, label=f'Yahoo! 最多 {fraction:.0%}＋剩余账户动态调整',
               protocol=VERSION, starting_state_sha256=initial.fingerprint(),
               target_audit={**target_audit, 'local_modified': local_modified,
                   'total_distinct_modified': arm['final']['ledger']['changed'],
                   'total_distinct_modified_rate': arm['final']['ledger']['changed_rate'],
                   'modification_events': arm['final']['ledger']['modification_events']},
               reference_audit={**reference_audit,
                   'local_modified': arm['adaptive_reference_ledger']['adaptive_affected']-reference_audit['strong_assigned']},
               hybrid_design={'requested_strong_fraction': fraction, **limits,
                   'selection': '原始完整口令频次降序；相同频次按种子和账户编号打散；只选未满足 Yahoo 的账户',
                   'metric_population': target.total, 'dynamic_policy': cfg['controller']['execution_policy'],
                   'strong_accounts_locked': True, 'A1_training': 'independent-reference-only',
                   'remaining': '其余账户有资格参与动态调整，并非全部必须修改',
                   'cost_accounting': 'adaptive 字段包含 Yahoo 阶段；local_modified 才是后续动态人数'},
               attacks={'F': arm['final']['risk'], 'A1': None})
    if checkpoint:
        checkpoint(METHOD+'_before_A1', arm)
    if cfg['evaluation']['adaptive']:
        if progress:
            progress('混合组：仅用独立参考群体最终分布重训 A1 的 PCFG 与 OMEN')
        arm['attacks']['A1'] = InterventionRisk(make_index(reference_counts, cfg),
            cfg['budgets'], cfg['risk_budget']).evaluate(counts)
    return arm
