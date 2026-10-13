"""Edit-aware Pareto screening and sequential composite registration search.

The response stream and all acceptance gates remain the v1 protocol. Only
development candidate generation and allocation of the fixed fit budget change.
"""
from collections import Counter
from dataclasses import replace
from functools import lru_cache
import hashlib

from experiments.registration_exchange import (grade, frozen_rate, burden_failures,
                                               select_nominee, confirm_nominee)
from policy.registration_joint import structure, VERSION as PREFERENCE_VERSION
from policy.registration_joint import JointRule
from policy.open_policy import edit_distance

VERSION = 'registration-bidirectional-edit-pareto-v2'


@lru_cache(maxsize=8)
def membership_digest(values):
    return hashlib.sha256('\n'.join(sorted(values)).encode()).hexdigest()


def rule_summary(rule):
    """Exactly the existing summary, caching only the large membership hash."""
    if not hasattr(rule, 'soft_shapes'):
        return rule.summary()
    return {**JointRule.summary(rule), 'soft_shape_size': len(rule.soft_shapes),
            'soft_shape_sha256': hashlib.sha256('\n'.join(sorted(rule.soft_shapes)).encode()).hexdigest(),
            'soft_guess_set_sha256': membership_digest(rule.guessed) if rule.soft_shapes else None,
            'restriction_mode': 'within_structure' if rule.soft_shapes else 'whole_structure_or_exact'}


class PreviewReplay:
    """Lazily replay exactly the original policy-independent preference stream.

    Cache generated proposals and original-to-endpoint edit distances so every
    candidate can measure editing without regenerating random response streams.
    """
    def __init__(self, records, response, seed):
        self.entries = [{'original': w, 'stream': iter(response.proposals(w, i, seed)),
                         'words': [], 'distances': {w: 0}} for i, w in records]
        self.results = {}

    def distance(self, number, word):
        entry = self.entries[number]
        if word not in entry['distances']:
            entry['distances'][word] = edit_distance(entry['original'], word)
        return entry['distances'][word]

    def simulate(self, rule):
        signature = rule.signature()
        if signature in self.results:
            return self.results[signature]
        counts, users, attempts = Counter(), [], []
        edits = modified = visible = blocked = 0
        for number, entry in enumerate(self.entries):
            position = 0
            while True:
                if position == len(entry['words']):
                    try:
                        entry['words'].append(next(entry['stream']))
                    except StopIteration as exc:
                        raise ValueError('Incomplete preference stream cannot certify a candidate') from exc
                word = entry['words'][position]
                if rule.accepts(word):
                    break
                position += 1
            users.append(word); attempts.append(position); counts[word] += 1
            original = entry['original']
            modified += word != original
            edits += self.distance(number, word)
            visible += len(original) < rule.minimum
            blocked += len(original) >= rule.minimum and not rule.accepts(original)
        n = len(users)
        if not n:
            raise ValueError('Preview requires users')
        ordered = sorted(attempts)
        result = {'counts': counts, 'users': users, 'attempts': attempts,
                  'summary': {'initial_users': n, 'completed_users': n, 'pending_users': 0,
                    'completion_rate': 1., 'modification_rate': modified/n,
                    'mean_extra_submissions': sum(attempts)/n,
                    'p90_extra_submissions': ordered[min(n-1, int(.9*n))],
                    'more_than_three_fraction': sum(a > 3 for a in attempts)/n,
                    'visible_length_repairs': visible, 'hidden_initial_rejections': blocked,
                    'mean_edit_distance': edits/n,
                    'terminal_state': 'continue_until_accepted_no_retry_cap',
                    'preference_protocol': PREFERENCE_VERSION}}
        self.results[signature] = result
        return result


def objective(item):
    cost = item['preview']['summary']
    return (item['frozen_rate'], cost['mean_extra_submissions'], cost['mean_edit_distance'])


def pareto_frontier(items):
    """Keep nondominated risk/attempt/edit vectors, including tied behaviors."""
    output = []
    for item in items:
        value = objective(item)
        if not any(all(a <= b for a,b in zip(objective(other), value))
                   and any(a < b for a,b in zip(objective(other), value))
                   for other in items if other is not item):
            output.append(item)
    return output


def diverse(items, count):
    """Represent risk, editing, attempts and normalized compromise within count."""
    if not items or count < 1:
        return []
    frontier = pareto_frontier(items)
    lows = tuple(min(objective(x)[i] for x in items) for i in range(3))
    spans = tuple(max(objective(x)[i] for x in items)-lows[i] for i in range(3))
    stable = lambda x: x['roles'][0]
    orders = [lambda x: (objective(x)[0], objective(x)[2], objective(x)[1], stable(x)),
              lambda x: (objective(x)[2], objective(x)[0], objective(x)[1], stable(x)),
              lambda x: (objective(x)[1], objective(x)[2], objective(x)[0], stable(x)),
              lambda x: (max((objective(x)[i]-lows[i])/spans[i] if spans[i] else 0
                             for i in range(3)), stable(x))]
    chosen = []
    for key in orders:
        item = min(frontier, key=key)
        if all(item is not other for other in chosen):
            chosen.append(item)
        if len(chosen) == count:
            return chosen
    # Tied frozen objectives can still differ after attack conditioning. Keep
    # family diversity before filling remaining slots deterministically.
    families = {x['family'] for x in chosen}
    for item in sorted(frontier, key=orders[3]):
        if item['family'] not in families:
            chosen.append(item); families.add(item['family'])
            if len(chosen) == count:
                return chosen
    for item in sorted(frontier, key=orders[3]):
        if all(item is not other for other in chosen):
            chosen.append(item)
        if len(chosen) == count:
            return chosen
    return chosen


def generate_candidates(baseline, hold, recent, records, response, ranks, cfg, seed, log):
    replay = PreviewReplay(records, response, seed)
    output, seen = [], {}
    membership = frozenset(ranks)

    def add(rule, family, label):
        if rule.minimum != baseline.minimum or rule.common != baseline.common:
            raise ValueError('An edit changed the common public requirements')
        signature = rule.signature()
        if signature in seen:
            item = output[seen[signature]]
            if label not in item['roles']:
                item['roles'].append(label)
            return item
        preview = replay.simulate(rule)
        item = {'rule': rule, 'family': family, 'roles': [label], 'preview': preview,
                'frozen_rate': frozen_rate(preview, ranks)}
        seen[signature] = len(output); output.append(item)
        return item

    current = add(hold, 'anchor', 'hold')
    base_item = add(baseline, 'anchor', 'baseline')
    soft = getattr(hold, 'soft_shapes', frozenset())
    singles = []
    for shape in sorted(hold.shapes | soft):
        rule = grade(hold, hold.shapes-{shape}, soft-{shape}, getattr(hold, 'guessed', ()))
        singles.append((shape, add(rule, 'release', 'release_'+shape)))
    # Three distinct removal rankings; no editing information is deferred until
    # after the fit shortlist. Beam intermediates may fail final gates, but are
    # never submitted: only a completed combination may be selected.
    rankings = [sorted(singles, key=lambda x: (objective(x[1])[i], objective(x[1]), x[0]))
                for i in (0, 2, 1)]
    pool = list(dict.fromkeys(shape for ranked in rankings
                             for shape,_ in ranked[:cfg['release_pool']]))
    for number, ranked in enumerate(rankings):
        for size in cfg['release_sizes']:
            removed = frozenset(s for s,_ in ranked[:size])
            if not removed:
                continue
            add(grade(hold, hold.shapes-removed, soft-removed, getattr(hold,'guessed',())),
                'release', f'release_rank{number}_top{size}')
            add(grade(hold, hold.shapes-removed, soft|removed, membership),
                'refine', f'refine_rank{number}_top{size}')
    beam = [frozenset()]
    for depth in range(1, cfg['beam_depth']+1):
        expansions, subset_by_id = [], {}
        for removed in beam:
            for shape in pool:
                if shape in removed:
                    continue
                subset = removed|{shape}
                item = add(grade(hold, hold.shapes-subset, soft-subset, getattr(hold,'guessed',())),
                           'release', 'beam_release_'+','.join(sorted(subset)))
                if id(item) not in subset_by_id:
                    expansions.append(item); subset_by_id[id(item)] = subset
        kept = diverse(expansions, cfg['beam_width'])
        beam = [subset_by_id[id(item)] for item in kept]
        if not beam:
            break
    # Search additions AFTER each release seed. Recompute destinations from the
    # released response, including structures responsible for increased editing.
    seeds = diverse([x for x in output if x['family'] in ('release','refine')], cfg['exchange_seeds'])
    seeds = [current]+seeds
    base_preview = base_item['preview']
    for seed_number, item in enumerate(seeds):
        rule, preview = item['rule'], item['preview']
        risk_mass, edit_penalty, edit_support = Counter(), Counter(), Counter()
        for word,n in (recent+preview['counts']).items():
            if word in ranks and rule.accepts(word):
                risk_mass[structure(word)] += n
        for user_number,(word,original_endpoint) in enumerate(zip(preview['users'],base_preview['users'])):
            delta = replay.distance(user_number,word)-replay.distance(user_number,original_endpoint)
            if delta > 0:
                shape = structure(word)
                edit_penalty[shape] += delta; edit_support[shape] += 1
        ranked_risk = [s for s in sorted(risk_mass,key=lambda s:(-risk_mass[s],s))
                       if s not in rule.shapes][:cfg['add_pool']]
        ranked_edit = [s for s in sorted(edit_penalty,key=lambda s:(-edit_penalty[s],s))
                       if edit_support[s] >= cfg['edit_repair_min_support'] and s not in rule.shapes][:cfg['add_pool']]
        for role, ranked in (('risk',ranked_risk),('edit',ranked_edit)):
            for size in cfg['add_sizes']:
                new = frozenset(ranked[:size])
                if not new:
                    continue
                current_soft = getattr(rule,'soft_shapes',frozenset())-new
                family = 'add' if item is current else 'exchange'
                add(grade(rule,rule.shapes|new,current_soft,getattr(rule,'guessed',())),
                    family,f'{role}_exchange_seed{seed_number}_add{size}')
        for risk_size in cfg['paired_add_sizes']:
            for edit_size in cfg['paired_add_sizes']:
                new = frozenset(ranked_risk[:risk_size]+ranked_edit[:edit_size])
                if new:
                    add(grade(rule,rule.shapes|new,getattr(rule,'soft_shapes',frozenset())-new,
                              getattr(rule,'guessed',())), 'exchange',
                        f'joint_seed{seed_number}_risk{risk_size}_edit{edit_size}')
    reference = base_item['preview']['summary']
    feasible = []
    for item in output:
        item['screening_failures'] = burden_failures(item['preview']['summary'],reference,cfg)
        item['screening_cost_ok'] = not item['screening_failures']
        if item['family']!='anchor' and item['screening_cost_ok']:
            feasible.append(item)
    frontier = pareto_frontier(feasible)
    chosen = [x for x in output if x['family']=='anchor']
    chosen += diverse(feasible, max(0,cfg['fit_shortlist']-len(chosen)))
    log(f'Edit-aware screened {len(output)} composites; {len(feasible)} fully burden-feasible; '
        f'{len(frontier)} Pareto candidates; {len(chosen)} conditioned fit shortlist')
    screening = [{'family':x['family'],'roles':x['roles'],'rule':rule_summary(x['rule']),
                  'frozen_rate':x['frozen_rate'],'cost':x['preview']['summary'],
                  'cost_ok':x['screening_cost_ok'],'failures':x['screening_failures'],
                  'pareto':any(x is y for y in frontier),
                  'shortlisted':any(x is y for y in chosen)} for x in output]
    return chosen, screening
