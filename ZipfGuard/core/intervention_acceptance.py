"""Versioned response acceptance; attack estimates may be diagnostics only."""
from core.intervention_attack_area import area_change, guard_batch, guard_reasons

AREA_ONLY_POLICY = 'fixed-F-area-only-batch-v2'
STRICT_POLICY = 'fixed-F-strict-batch-v1'
INDIVIDUAL_POLICY = 'dual-F-individual-threshold-v1'


def apply_response_policy(population, outcomes, risk, budgets, policy):
    if policy == INDIVIDUAL_POLICY:
        from core.intervention_attack_area import fixed_model
        model = fixed_model(risk)
        passed = sum(row['status'] == 'changed' and row['new'] != row['old'] and
                     model.passes_threshold(row['new']) for row in outcomes)
        accepted = bool(outcomes) and passed == len(outcomes)
        change = area_change(risk, outcomes, population.total, budgets)
        return ([dict(row) for row in outcomes] if accepted else []), {
            'protocol': INDIVIDUAL_POLICY, 'enforced': True, 'accepted': accepted,
            'threshold_per_model': model.budget, 'models': ['pcfg', 'markov'],
            'individual_passed': passed, 'individual_total': len(outcomes),
            'proposed_area_gain': change['gain'], 'realized_area_gain': change['gain'] if accepted else 0.,
            'proposed_uncovered_rate_change': change['uncovered_rate_change'],
            'rejected_modifications': 0 if accepted else len(outcomes),
            'rejection_reasons': [] if accepted else ['存在新口令未通过双攻击个体门槛'],
            'diagnostic_warnings': []}
    if policy == STRICT_POLICY:
        return guard_batch(population, outcomes, risk, budgets)
    if policy != AREA_ONLY_POLICY:
        raise ValueError('未知响应接受协议')
    change = area_change(risk, outcomes, population.total, budgets)
    accepted = change['gain'] > 1e-12
    # Rejected proposals are not issued and do not mutate the notification ledger.
    return ([dict(row) for row in outcomes] if accepted else []), {
        'protocol': AREA_ONLY_POLICY, 'enforced': True, 'accepted': accepted,
        'proposed_area_gain': change['gain'],
        'proposed_uncovered_rate_change': change['uncovered_rate_change'],
        'realized_area_gain': change['gain'] if accepted else 0.,
        'rejected_modifications': 0 if accepted else len(outcomes),
        'rejection_reasons': [] if accepted else ['固定 F 猜测成功曲线的对数面积未下降'],
        'diagnostic_warnings': (['固定 F 模型未覆盖比例增加；估计命中下降不能单独证明安全提升']
                                if change['uncovered_rate_change'] > 1e-12 else [])}
