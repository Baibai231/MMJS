"""Bidirectional registration edits with paired response and burden constraints.

Screening uses fixed prefixes; shortlisted policies require conditioned dual fits.
The original baseline is a comparator, not a mandatory subset of every rule.
"""
from collections import Counter
from dataclasses import replace

from experiments.registration_joint_pilot import GradedRule
from policy.registration_joint import structure

VERSION = 'registration-bidirectional-exchange-v1'


def frozen_rate(result, ranks):
    n = result['summary']['initial_users']
    if result['summary']['pending_users'] or sum(result['counts'].values()) != n:
        raise ValueError('Incomplete response cannot certify improvement')
    return sum(v for w, v in result['counts'].items() if w in ranks)/n


def grade(rule, shapes, soft=(), guessed=None):
    return GradedRule(minimum=rule.minimum, common=rule.common, exact=rule.exact,
                     shapes=frozenset(shapes), k=rule.k, j=len(shapes),
                     soft_shapes=frozenset(soft),
                     guessed=frozenset(guessed or ()) if soft else frozenset())


def generate_candidates(baseline, hold, recent, records, response, ranks, cfg, seed, log):
    """Development-only search. Every composite edit receives a full replay."""
    output, seen = [], {}

    def add(rule, family, label, preview=None):
        if rule.minimum != baseline.minimum or rule.common != baseline.common:
            raise ValueError('An edit changed the common public requirements')
        signature = rule.signature()
        if signature in seen:
            output[seen[signature]]['roles'].append(label)
            return output[seen[signature]]
        if preview is None:
            preview = response.simulate(records, rule, seed)
        item = {'rule': rule, 'family': family, 'roles': [label], 'preview': preview,
                'frozen_rate': frozen_rate(preview, ranks)}
        seen[signature] = len(output)
        output.append(item)
        return item

    add(hold, 'anchor', 'hold')
    base_item = add(baseline, 'anchor', 'baseline')
    hold_item = output[0]
    releases = []
    for shape in sorted(hold.shapes):
        r = replace(hold, shapes=hold.shapes-{shape}, j=len(hold.shapes)-1)
        item = add(r, 'release', 'release_'+shape)
        saving = hold_item['preview']['summary']['mean_extra_submissions']-item['preview']['summary']['mean_extra_submissions']
        if saving > 0:
            releases.append((shape, item, saving))
    releases.sort(key=lambda x: (x[1]['frozen_rate'], -x[2], x[0]))
    shortlist_shapes = [s for s, _, _ in releases[:cfg['release_pool']]]
    # Joint releases avoid repeatedly paying for low-benefit blanket constraints.
    for size in cfg['release_sizes']:
        removed = frozenset(shortlist_shapes[:size])
        if removed:
            add(replace(hold, shapes=hold.shapes-removed, j=len(hold.shapes-removed)),
                'release', f'release_top_{size}')
            existing_soft = getattr(hold, 'soft_shapes', frozenset())
            # Refine blanket restrictions to risk membership under public-base
            # development attacks; no target test prefix enters this membership.
            add(grade(hold, hold.shapes-removed, existing_soft|removed, ranks),
                'refine', f'refine_top_{size}')
    # New hotspots are ranked by observed frozen-attack mass, not just frequency.
    shapes = Counter()
    for word, n in recent.items():
        if word in ranks and hold.accepts(word):
            shapes[structure(word)] += n
    additions = [s for s in sorted(shapes, key=lambda s: (-shapes[s], s))
                 if s not in hold.shapes][:cfg['add_pool']]
    for size in cfg['add_sizes']:
        new = frozenset(additions[:size])
        if not new:
            continue
        soft = getattr(hold, 'soft_shapes', frozenset())-new
        add(grade(hold, hold.shapes|new, soft, getattr(hold, 'guessed', ())),
            'add', f'add_top_{size}')
        for remove_size in cfg['release_sizes']:
            removed = frozenset(shortlist_shapes[:remove_size])
            if removed:
                add(grade(hold, (hold.shapes-removed)|new, soft, getattr(hold, 'guessed', ())),
                    'exchange', f'exchange_release_{remove_size}_add_{size}')
    reference = base_item['preview']['summary']
    feasible = []
    for item in output:
        c = item['preview']['summary']
        item['screening_cost_ok'] = (
            c['mean_extra_submissions'] <= reference['mean_extra_submissions']*(1-cfg['cost_saving_fraction'])
            and c['modification_rate'] <= reference['modification_rate']
            and c['more_than_three_fraction'] <= reference['more_than_three_fraction'])
        if item['family'] != 'anchor' and item['screening_cost_ok']:
            feasible.append(item)
    key = lambda x: (x['frozen_rate'], x['preview']['summary']['mean_extra_submissions'], x['roles'][0])
    chosen = [item for item in output if item['family'] == 'anchor']
    # Retain search diversity instead of filling all slots with nearly identical
    # single removals. Nonselected screening candidates remain in the audit.
    for family in ('release', 'refine', 'exchange', 'add'):
        members = [x for x in feasible if x['family'] == family]
        if members and len(chosen) < cfg['fit_shortlist']:
            chosen.append(min(members, key=key))
    for item in sorted(feasible, key=key):
        if len(chosen) >= cfg['fit_shortlist']:
            break
        if all(item is not other for other in chosen):
            chosen.append(item)
    log(f'Screened {len(output)} edits; {len(feasible)} burden-feasible; {len(chosen)} conditioned dual fits shortlisted')
    screening = [{'family': x['family'], 'roles': x['roles'], 'rule': x['rule'].summary(),
                  'frozen_rate': x['frozen_rate'], 'cost': x['preview']['summary'],
                  'cost_ok': x['screening_cost_ok'], 'shortlisted': any(x is y for y in chosen)}
                 for x in output]
    return chosen, screening


def burden_failures(cost, baseline_cost, cfg):
    failures = []
    if cost['pending_users']:
        failures.append('pending_users')
    for metric, tolerance in [('mean_extra_submissions', 1e-12),
                              ('modification_rate', 1e-12),
                              ('more_than_three_fraction', 1e-12),
                              ('mean_edit_distance', 1e-12)]:
        if cost[metric] is None or baseline_cost[metric] is None:
            failures.append('missing_'+metric)
            continue
        cap = baseline_cost[metric]
        if metric == 'mean_extra_submissions':
            cap *= 1-cfg['cost_saving_fraction']
        if cost[metric] > cap+tolerance:
            failures.append(metric)
    return failures


def select_nominee(rows, cfg):
    baseline = next(x for x in rows if 'baseline' in x['roles'])
    current = next(x for x in rows if 'hold' in x['roles'])
    feasible = []
    for row in rows:
        row['failures'] = burden_failures(row['cost'], baseline['cost'], cfg)
        if row['predicted_outside'] > baseline['predicted_outside']+cfg['outside_increase']:
            row['failures'].append('outside_support_increase')
        if row['frozen_rate'] > baseline['frozen_rate']+cfg['frozen_increase']:
            row['failures'].append('frozen_risk_increase')
        if row['predicted_A1'] > baseline['predicted_A1']-cfg['min_gain']:
            row['failures'].append('no_risk_improvement_over_baseline')
        if not row['failures'] and row is not current:
            # Never trade more predicted risk for a cheaper update. A cost-only
            # improvement is allowed if risk is unchanged at floating precision.
            risk_better = current['predicted_A1']-row['predicted_A1'] >= cfg['min_gain']
            cost_better = (row['predicted_A1'] <= current['predicted_A1']+1e-12
                           and row['cost']['mean_extra_submissions'] < current['cost']['mean_extra_submissions']-1e-12)
            if risk_better or cost_better:
                feasible.append(row)
    return min(feasible, key=lambda x: (x['predicted_A1'], x['cost']['mean_extra_submissions'], x['number'])) if feasible else None


def confirm_nominee(rows, nominee_number, cfg):
    # Exactly one nominee may be confirmed; confirmation does not select a runner-up.
    nominee = select_nominee(rows, cfg)
    if nominee is not None and nominee['number'] == nominee_number:
        return nominee_number, 'confirmed_bidirectional_update'
    hold = next(x for x in rows if 'hold' in x['roles'])
    return hold['number'], 'hold_no_confirmed_double_improvement'
