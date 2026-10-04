"""Choose the largest feasible whole-population gain. Predictions do not mutate state."""
from collections import Counter
import hashlib
import math
from statistics import mean
from policy.intervention_fragments import LENGTHS, candidate_fragments
from policy.local_actions import Action, Group, LocalRule, MultiAction, capacities, generate_actions
from policy.open_policy import Rule


def predict_action(population, action, risk, responder, cfg, round_id, counts=None, repeats=None):
    counts, n = population.counts() if counts is None else counts, population.total
    primary_gains, guarded_gains, adaptive_gains, composite_gains = [], [], [], []
    hhi_changes, shape_gains = [], []
    repeats = repeats or cfg['controller']['prediction_repeats']
    for repeat in range(repeats):
        # Same account noise for competing candidates; separate from execution.
        rows = responder.respond(population, action, cfg['seed'], f'prediction-{round_id}-{repeat}')
        guarded_gain = sum(risk.loss(r['old'])-risk.loss(r['new']) for r in rows)/n
        primary_gains.append(sum(risk.hit(r['old'])-risk.hit(r['new']) for r in rows)/n)
        adaptive_gains.append(sum((risk.adaptive_hit(r['old'])-risk.adaptive_hit(r['new'])
                                   if hasattr(risk, 'adaptive_hit') else
                                   risk.hit(r['old'])-risk.hit(r['new'])) for r in rows)/n)
        guarded_gains.append(guarded_gain)
        delta = Counter()
        for row in rows:
            delta[row['old']] -= 1
            delta[row['new']] += 1
        hhi_delta = sum((counts.get(w, 0)+d)**2 - counts.get(w, 0)**2
                        for w, d in delta.items())/n**2
        hhi_changes.append(hhi_delta)
        if hasattr(risk, 'baseline_hhi_excess'):
            shape_gain = -hhi_delta/max(risk.baseline_hhi_excess, 1e-12)
            composite_gains.append(risk.risk_weight*guarded_gain/max(risk.baseline_risk, 1e-12)
                                   + (1-risk.risk_weight)*shape_gain)
            shape_gains.append(shape_gain)
        else:
            composite_gains.append(guarded_gain)
            shape_gains.append(-hhi_delta)
    gain = mean(primary_gains)
    c = cfg['controller']
    reasons = []
    if gain <= c['min_gain']:
        reasons.append('主指标 F 在目标猜测预算下预计改善不足')
    if sum(value > 0 for value in primary_gains)/repeats < c['min_positive_trial_fraction']:
        reasons.append('重复模拟中主指标改善不够稳定')
    if mean(adaptive_gains) < -c['max_a1_regression']:
        reasons.append('参考攻击模型 A1 预计恶化超过容忍量')
    if max(hhi_changes) > c['max_hhi_increase']:
        reasons.append('预测新分布聚集超过容忍量')
    return {'predicted_guarded_gain': mean(guarded_gains), 'predicted_hit_gain': gain,
            'predicted_primary_gain': gain, 'predicted_a1_gain': mean(adaptive_gains),
            'predicted_distribution_gain': mean(shape_gains),
            'predicted_composite_gain': mean(composite_gains),
            'predicted_gain_range': [min(primary_gains), max(primary_gains)],
            'positive_trial_fraction': sum(value > 0 for value in primary_gains)/repeats,
            'prediction_repeats_used': repeats,
            'predicted_hhi_change': mean(hhi_changes),
            'max_predicted_hhi_increase': max(hhi_changes),
            'gain_per_notified_account': gain/(len(action.indices)/n),
            'score': gain, 'feasible': not reasons,
            'rejection_reasons': reasons}


def _combined_actions(evaluated, population, risk, responder, cfg, round_id, counts, cap, minimum):
    """Join disjoint positive fragments; validate the full population outcome."""
    ordered = sorted((row for row in evaluated if row[1]['score'] > 0
                      and not isinstance(row[0], MultiAction)),
                     key=lambda row: row[1]['score'], reverse=True)
    width = cfg['controller']['combination_width']
    output, seen = [], set()
    for first, _ in ordered[:width]:
        if len(first.indices) >= cap:
            continue
        parts, used = [first], set(first.indices)
        for other, _ in ordered[:max(width*3, width)]:
            if other is first:
                continue
            room = cap-len(used)
            if room < minimum:
                break
            ids = tuple(i for i in other.indices if i not in used)[:room]
            if len(ids) < minimum:
                continue
            parts.append(Action(other.group, other.rule, other.rule_label, ids,
                                other.eligible_count))
            used.update(ids)
        if len(parts) < 2:
            continue
        signature = frozenset(used)
        if signature in seen:
            continue
        seen.add(signature)
        action = MultiAction(tuple(parts), label='组合局部干预')
        output.append((action, predict_action(population, action, risk, responder,
                                              cfg, round_id, counts)))
    return output


def _bridge_action(population, evaluated, risk, responder, cfg, round_id, minimum):
    """Bounded two-step preview, used only after the normal search has failed."""
    c = cfg['controller']
    if round_id >= c['max_rounds'] or capacities(population, cfg) < 2*minimum:
        return None
    before = risk.primary_risk(population.counts()) if hasattr(risk, 'primary_risk') else None
    if before is None:
        return None
    near = sorted(((action, prediction) for action, prediction in evaluated
                   if len(action.indices) >= minimum
                   and prediction['score'] >= -c['bridge_max_first_loss']
                   and prediction['predicted_a1_gain'] >= -c['max_a1_regression']
                   and prediction['max_predicted_hhi_increase'] <= c['max_hhi_increase']),
                  key=lambda row: row[1]['score'], reverse=True)[:c['lookahead_width']]
    options = []
    for first, prediction in near:
        totals, next_actions = [], []
        for trial in range(c['lookahead_rollouts']):
            preview = population.clone()
            first_rows = responder.respond(preview, first, cfg['seed'],
                                           f'bridge-first-{round_id}-{trial}')
            preview.apply(first_rows)
            second, _ = select_action(preview, risk, responder, cfg, round_id+1,
                                      allow_bridge=False, validation=False)
            if second is None:
                break
            next_action, _ = second
            second_rows = responder.respond(preview, next_action, cfg['seed'],
                                            f'bridge-second-{round_id}-{trial}')
            preview.apply(second_rows)
            totals.append(before-risk.primary_risk(preview.counts()))
            next_actions.append(next_action.public())
        if len(totals) == c['lookahead_rollouts'] and min(totals) > c['min_gain']:
            extended = {**prediction, 'feasible': True, 'bridge': True,
                        'bridge_first_round_predicted_gain': prediction['score'],
                        'bridge_expected_two_round_gain': mean(totals),
                        'bridge_next_action_preview': next_actions[0],
                        'rejection_reasons': ['单轮预计未通过普通准入；两步展望预计累计改善']}
            options.append((first, extended))
    return max(options, key=lambda row: row[1]['bridge_expected_two_round_gain'],
               default=None)


def select_action(population, risk, responder, cfg, round_id, target_size=None,
                  *, allow_bridge=True, validation=True):
    candidates = generate_actions(population, risk, cfg,
                                  minimum_length=cfg['controller'].get('policy_floor_minimum_length', 0),
                                  catalog_reference=getattr(responder, 'reference', None),
                                  round_id=round_id)
    cap = min(capacities(population, cfg),
              target_size if target_size is not None else capacities(population, cfg))
    minimum = max(1, math.floor(population.total*cfg['controller']['min_batch_fraction']+1e-9))
    candidates = [a for a in candidates if minimum <= len(a.indices) <= cap]
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
        if random_winner is not None and minimum <= len(random_winner[0].indices) <= cap:
            evaluated.append(random_winner)
    evaluated.extend(_combined_actions(evaluated, population, risk, responder, cfg,
                                       round_id, counts, cap, minimum))
    if validation and evaluated:
        order = sorted(range(len(evaluated)), key=lambda i: evaluated[i][1]['score'], reverse=True)
        feasible = []
        for rank, i in enumerate(order):
            if rank >= cfg['controller']['validation_shortlist'] and feasible:
                break
            action, _ = evaluated[i]
            evaluated[i] = (action, predict_action(population, action, risk, responder, cfg,
                                                  round_id, counts,
                                                  cfg['controller']['validation_repeats']))
            if evaluated[i][1]['feasible']:
                feasible.append(evaluated[i])
    else:
        feasible = [(a, p) for a, p in evaluated if p['feasible']]
    winner = max(feasible, key=lambda row: (row[1]['score'],
                 -row[1]['predicted_hhi_change'], -len(row[0].indices)), default=None)
    if winner is None and allow_bridge:
        winner = _bridge_action(population, evaluated, risk, responder, cfg, round_id, minimum)
        if winner is not None:
            evaluated.append(winner)
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
    minimum = max(1, math.floor(population.total*cfg['controller']['min_batch_fraction']+1e-9))
    if sum(len(part.indices) for part in components) < minimum:
        return None, [{'action': a.public(), **p} for a, p in evaluated]
    batch = MultiAction(tuple(components))
    prediction = predict_action(population, batch, risk, responder, cfg, round_id, counts,
                                cfg['controller']['validation_repeats'])
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
                                      target_size=target_size, allow_bridge=False)
        if winner is None:
            break
        action, prediction = winner
        schedule.append((action, prediction, audit))
        for i in action.indices:
            planning.accounts[i].adaptive_notifications = 1
            planning.accounts[i].notifications += 1
    return schedule
