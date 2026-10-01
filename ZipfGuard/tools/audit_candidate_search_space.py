"""Count policy timelines exactly, without claiming to evaluate their outcomes.

One transition holds or unions one of the 15 proposed templates. Equivalent
next rule structures are deduplicated. Once enabled, the historical blacklist
is assumed to refresh before every later cohort using past users only.
Actual history contents and response distributions are NOT merged in an
experiment; structural merging here is only for counting possible timelines.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# minimum length, character classes, development blocklist size, history, patterns
# pattern bits: 1 = repeated; 2 = sequential_digits
SEEDS = (
    (8, 0, 0, 0, 0), (10, 0, 0, 0, 0), (12, 0, 0, 0, 0), (16, 0, 0, 0, 0),
    (8, 2, 0, 0, 0), (8, 3, 0, 0, 0),
    (8, 0, 100, 0, 0), (8, 0, 1000, 0, 0), (8, 0, 10000, 0, 0),
    (8, 0, 0, 1, 0), (8, 0, 0, 0, 1), (8, 0, 0, 0, 2),
    (12, 2, 0, 0, 0), (12, 0, 1000, 0, 0), (12, 0, 1000, 1, 0),
)


def combine(left, right):
    return (*[max(left[i], right[i]) for i in range(3)],
            left[3] | right[3], left[4] | right[4])


def transitions(states, actions):
    return {state: {combine(state, action) for action in actions} for state in states}


def restricted_preset_transitions():
    """Next cohort must itself be one of the same fifteen policies."""
    return {state: {candidate for candidate in SEEDS
                    if combine(state, candidate) == candidate} for state in SEEDS}


def count_by_depth(edges, steps):
    frontier = {SEEDS[0]: 1}
    totals = []
    for _ in range(steps):
        following = {}
        for state, paths in frontier.items():
            for successor in edges[state]:
                following[successor] = following.get(successor, 0) + paths
        frontier = following
        totals.append(sum(frontier.values()))
    return totals


def audit(cohorts=10):
    if cohorts < 2:
        raise ValueError('At least two cohorts are required')
    states = {SEEDS[0]}
    for seed in SEEDS:
        states |= {combine(state, seed) for state in states}
    single = transitions(states, SEEDS)
    unrestricted = transitions(states, states)
    steps = cohorts - 1
    one_totals = count_by_depth(single, steps)
    all_totals = count_by_depth(unrestricted, steps)
    restricted_totals = count_by_depth(restricted_preset_transitions(), steps)
    # Independent closed form: 4 lengths × 3 classes × 4 blocklist sizes,
    # and three binary restrictions, all monotone over the decision slots.
    closed_form = (math.comb(steps + 3, 3) ** 2
                   * math.comb(steps + 2, 2) * (steps + 1) ** 3)
    assert all_totals[-1] == closed_form
    two_step_paths = {(a, b) for a in single[SEEDS[0]] for b in single[a]}
    assert len(two_step_paths) == count_by_depth(single, 2)[-1]
    catalog_path = ROOT / 'docs' / 'policy_candidate_profiles.json'
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))['profiles']
    selected = []
    for i, (length, classes, block, history, patterns) in enumerate(SEEDS, 1):
        deny = [name for bit, name in ((1, 'repeated'), (2, 'sequential_digits')) if patterns & bit]
        matches = [p for p in catalog if p['min_length'] == length
                   and p['required_classes'] == classes
                   and p['development_top_blocklist'] == block
                   and (p['historical_hotspot_blocklist'] != 'off') == bool(history)
                   and p['deny_features'] == deny]
        assert len(matches) == 1
        selected.append({'pilot_id': f'S{i:02}', **matches[0]})
    return {
        'status': 'search_space_count_only; no candidate performance evaluated',
        'cohorts': cohorts, 'first_cohort': 'minimum length 8',
        'catalog_sha256': hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
        'selected_profiles': selected,
        'distinct_combined_structures': len(states),
        'one_template_or_hold_per_boundary': one_totals,
        'any_combination_per_boundary': all_totals,
        'only_selected_15_policies_per_boundary': restricted_totals,
        'restricted_alternatives': {
            'two_fixed_decision_boundaries_one_template_each': len(two_step_paths),
            'at_most_one_change_at_any_boundary': 1 + steps * (len(SEEDS) - 1),
        },
        'assumptions': [
            'Counts refer to rule-structure timelines, before cost or risk pruning.',
            'Equivalent actions yielding the same next structure are counted once.',
            'History blocking refreshes every cohort after activation; disabling or selectively delaying refresh is excluded.',
            'Previously registered passwords are never changed.',
            'Different histories must not be merged by rule structure when evaluating outcomes.',
            'No beam search or random sampling is used in this count.',
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cohorts', type=int, default=10)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.cohorts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: result[k] for k in (
        'status', 'distinct_combined_structures', 'one_template_or_hold_per_boundary',
        'any_combination_per_boundary', 'only_selected_15_policies_per_boundary',
        'restricted_alternatives')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
