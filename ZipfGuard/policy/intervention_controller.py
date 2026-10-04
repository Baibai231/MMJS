"""Choose the largest feasible whole-population gain. Predictions do not mutate state."""
from collections import Counter
import hashlib
from statistics import mean
from policy.intervention_fragments import LENGTHS, candidate_fragments
from policy.local_actions import Action, Group, LocalRule, MultiAction, capacities, generate_actions
from policy.open_policy import Rule


def predict_action(population, action, risk, responder, cfg, round_id, counts=None):
    counts, n = population.counts() if counts is None else counts, population.total
    trials, risk_gains, hits, hhi_changes, shape_gains = [], [], [], [], []
    for repeat in range(cfg['controller']['prediction_repeats']):
        # Same account noise for competing candidates; separate from execution.
        rows = responder.respond(population, action, cfg['seed'], f'prediction-{round_id}-{repeat}')
        raw_gain = sum(risk.loss(r['old'])-risk.loss(r['new']) for r in rows)/n
        risk_gains.append(raw_gain)
        hits.append(sum(risk.hit(r['old'])-risk.hit(r['new']) for r in rows)/n)
        delta = Counter()
        for row in rows:
            delta[row['old']] -= 1
            delta[row['new']] += 1
        hhi_delta = sum((counts.get(w, 0)+d)**2 - counts.get(w, 0)**2
                        for w, d in delta.items())/n**2
        hhi_changes.append(hhi_delta)
        if hasattr(risk, 'baseline_hhi_excess'):
            shape_gain = -hhi_delta/max(risk.baseline_hhi_excess, 1e-12)
            trials.append(risk.risk_weight*raw_gain/max(risk.baseline_risk, 1e-12)
                          + (1-risk.risk_weight)*shape_gain)
            shape_gains.append(shape_gain)
        else:
            trials.append(raw_gain)
            shape_gains.append(-hhi_delta)
    gain = mean(trials)
    reasons = []
    if gain <= cfg['controller']['min_gain']:
        reasons.append('扣除模型未覆盖质量后，预计风险改善不足')
    if min(trials) <= 0:
        reasons.append('至少一次模拟未显示保守风险下降')
    if mean(hits) <= 0:
        reasons.append('PCFG 点估计命中质量没有减少')
    if max(hhi_changes) > cfg['controller']['max_hhi_increase']:
        reasons.append('预测新分布聚集超过容忍量')
    return {'predicted_guarded_gain': mean(risk_gains), 'predicted_hit_gain': mean(hits),
            'predicted_distribution_gain': mean(shape_gains),
            'predicted_composite_gain': gain,
            'predicted_gain_range': [min(trials), max(trials)],
            'predicted_hhi_change': mean(hhi_changes),
            'max_predicted_hhi_increase': max(hhi_changes),
            'gain_per_notified_account': gain/(len(action.indices)/n),
            'score': gain, 'feasible': not reasons,
            'rejection_reasons': reasons}


def select_action(population, risk, responder, cfg, round_id, target_size=None):
    candidates = generate_actions(population, risk, cfg,
                                  minimum_length=cfg['controller'].get('policy_floor_minimum_length', 0),
                                  catalog_reference=getattr(responder, 'reference', None),
                                  round_id=round_id)
    if target_size is not None:
        # Freeze the same notification schedule as the feedback arm. Keep one
        # candidate per group and rule after trimming the nested batch sizes.
        exact = {}
        for action in candidates:
            if len(action.indices) >= target_size:
                key = (action.group.kind, action.group.value, action.rule.base.name)
                exact.setdefault(key, Action(action.group, action.rule, action.rule_label,
                                             action.indices[:target_size], action.eligible_count))
        candidates = list(exact.values())
    # Predict only the most promising structured choices; always retain every
    # random and cross-structure rule so the comparator's actions remain in scope.
    if len(candidates) > cfg['controller']['candidate_shortlist']:
        always = [a for a in candidates if a.group.kind in ('random', 'all')]
        structured = [a for a in candidates if a.group.kind not in ('random', 'all')]
        structured.sort(key=lambda a: sum(risk.hit(population.accounts[i].password)
                                          for i in a.indices), reverse=True)
        candidates = always + structured[:cfg['controller']['candidate_shortlist']]
    counts = population.counts()
    evaluated = [(a, predict_action(population, a, risk, responder, cfg, round_id, counts)) for a in candidates]
    if getattr(risk, 'models', None):
        random_winner, _ = select_random_action(population, risk, responder, cfg, round_id,
                                                target_size=target_size)
        if random_winner is not None and (target_size is None or
                                          len(random_winner[0].indices) == target_size):
            evaluated.append(random_winner)
    feasible = [(a, p) for a, p in evaluated if p['feasible']]
    # A tiny group may be efficient per person but barely change the sitewide
    # distribution. Prioritize total risk reduction under the existing hard
    # notification budget; use concentration and cost only for equal gains.
    winner = max(feasible, key=lambda row: (row[1]['score'],
                 -row[1]['predicted_hhi_change'], -len(row[0].indices)), default=None)
    audit = [{'action': a.public(), **p} for a, p in evaluated]
    return winner, audit


def random_account_order(population, indices, seed, round_id):
    """A seeded draw independent of passwords, risk scores and candidate rules."""
    return sorted(indices, key=lambda i: hashlib.sha256(
        f'{seed}|random-cohort|{round_id}|{population.accounts[i].identifier}'.encode()).digest())


def select_random_action(population, risk, responder, cfg, round_id, target_size=None):
    """Draw a fixed actionable cohort, then assign its members local rules."""
    available = [i for i, a in enumerate(population.accounts) if not a.adaptive_notifications]
    take = capacities(population, cfg)
    if target_size is not None:
        take = min(take, target_size)
    if not available or take <= 0:
        return None, []
    random_rank = {i: rank for rank, i in enumerate(
        random_account_order(population, available, cfg['seed'], round_id))}
    counts = population.counts()
    ranked = sorted(counts, key=lambda w: (-counts[w], w))
    hot = frozenset(w for w in ranked[:cfg['controller']['popular_k']] if counts[w] >= 2)
    fragments = candidate_fragments(
        hot=hot, development=getattr(responder, 'reference', None),
        predictable_terms=cfg['controller']['predictable_terms'])
    group = Group('random', '', '随机抽取账户')
    rules = []
    for fragment in fragments:
        if fragment.number in LENGTHS and LENGTHS[fragment.number] not in cfg['controller']['lengths']:
            continue
        rule = LocalRule(Rule(f'fragment-{fragment.number}',
                              min_length=max(cfg['controller'].get('policy_floor_minimum_length', 0),
                                             LENGTHS.get(fragment.number, 0))),
                         fragment=fragment)
        rules.append((fragment, rule))
    actionable = [i for i in available if any(not rule.accepts(population.accounts[i].password)
                                              for _, rule in rules)]
    cohort = tuple(sorted(actionable, key=lambda i: random_rank[i])[:take])
    if not cohort:
        return None, []
    evaluated = []
    for fragment, rule in rules:
        eligible = tuple(i for i in cohort if not rule.accepts(population.accounts[i].password))
        if not eligible:
            continue
        action = Action(group, rule, f'第 {fragment.number} 条：{fragment.label}',
                        eligible, len(eligible))
        evaluated.append((action, predict_action(population, action, risk, responder,
                                                 cfg, round_id, counts)))
    # Assign the drawn users in order of rule benefit per eligible member.
    # The account draw itself does not depend on risk, password or chosen rule.
    ranked_rules = sorted((row for row in evaluated if row[1]['feasible']),
                          key=lambda row: (row[1]['score']/len(row[0].indices),
                                           row[1]['score']), reverse=True)
    remaining = set(cohort)
    components = []
    for action, _ in ranked_rules:
        ids = tuple(i for i in action.indices if i in remaining)
        if ids:
            components.append(Action(action.group, action.rule, action.rule_label,
                                     ids, action.eligible_count))
            remaining.difference_update(ids)
    if not components:
        return None, [{'action': a.public(), **p} for a, p in evaluated]
    batch = MultiAction(tuple(components))
    prediction = predict_action(population, batch, risk, responder, cfg, round_id, counts)
    audit = [{'action': a.public(), **p} for a, p in evaluated]
    audit.append({'action': batch.public(), **prediction})
    return ((batch, prediction) if prediction['feasible'] else None), audit


def plan_once(initial, risk, responder, cfg, round_targets=None):
    # Reserve disjoint IDs without executing outcomes. All rules, hotspot lists,
    # predictions and priorities use the unchanged round-0 password distribution.
    planning = initial.clone()
    schedule = []
    for round_id in range(1, cfg['controller']['max_rounds']+1):
        if round_targets is not None and round_id > len(round_targets):
            break
        target_size = round_targets[round_id-1] if round_targets is not None else None
        winner, audit = select_action(planning, risk, responder, cfg, round_id,
                                      target_size=target_size)
        if winner is None:
            break
        action, prediction = winner
        schedule.append((action, prediction, audit))
        for i in action.indices:
            planning.accounts[i].adaptive_notifications = 1
            planning.accounts[i].notifications += 1
    return schedule
