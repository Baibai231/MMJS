"""One-step marginal-gain selection. Predictions never mutate actual state."""
from collections import Counter
from statistics import mean
from policy.local_actions import generate_actions


def predict_action(population, action, risk, responder, cfg, round_id, counts=None):
    counts, n = population.counts() if counts is None else counts, population.total
    trials, hits, hhi_changes = [], [], []
    for repeat in range(cfg['controller']['prediction_repeats']):
        # Same account noise for competing candidates; separate from execution.
        rows = responder.respond(population, action, cfg['seed'], f'prediction-{round_id}-{repeat}')
        trials.append(sum(risk.loss(r['old'])-risk.loss(r['new']) for r in rows)/n)
        hits.append(sum(risk.hit(r['old'])-risk.hit(r['new']) for r in rows)/n)
        delta = Counter()
        for row in rows:
            delta[row['old']] -= 1
            delta[row['new']] += 1
        hhi_changes.append(sum((counts.get(w, 0)+d)**2 - counts.get(w, 0)**2
                               for w, d in delta.items())/n**2)
    gain = mean(trials)
    reasons = []
    if gain <= cfg['controller']['min_gain']:
        reasons.append('扣除模型未覆盖质量后，预计风险改善不足')
    if mean(hits) <= 0:
        reasons.append('PCFG 点估计命中质量没有减少')
    if max(hhi_changes) > cfg['controller']['max_hhi_increase']:
        reasons.append('预测新分布聚集超过容忍量')
    return {'predicted_guarded_gain': gain, 'predicted_hit_gain': mean(hits),
            'predicted_gain_range': [min(trials), max(trials)],
            'max_predicted_hhi_increase': max(hhi_changes),
            'score': gain/(len(action.indices)/n), 'feasible': not reasons,
            'rejection_reasons': reasons}


def select_action(population, risk, responder, cfg, round_id):
    candidates = generate_actions(population, risk, cfg)
    counts = population.counts()
    evaluated = [(a, predict_action(population, a, risk, responder, cfg, round_id, counts)) for a in candidates]
    feasible = [(a, p) for a, p in evaluated if p['feasible']]
    winner = max(feasible, key=lambda row: (round(row[1]['score'], 12),
                 row[1]['predicted_guarded_gain'], -len(row[0].indices)), default=None)
    audit = [{'action': a.public(), **p} for a, p in evaluated]
    return winner, audit


def plan_once(initial, risk, responder, cfg):
    # Reserve disjoint IDs without executing outcomes. All rules, hotspot lists,
    # predictions and priorities use the unchanged round-0 password distribution.
    planning = initial.clone()
    schedule = []
    for round_id in range(1, cfg['controller']['max_rounds']+1):
        winner, audit = select_action(planning, risk, responder, cfg, round_id)
        if winner is None:
            break
        action, prediction = winner
        schedule.append((action, prediction, audit))
        for i in action.indices:
            planning.accounts[i].notifications = 1
    return schedule
