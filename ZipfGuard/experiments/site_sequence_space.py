"""Exact per-cohort cost filtering for history-free website rules only."""
from __future__ import annotations

import json
from math import prod
from pathlib import Path

from policy.site_catalog import site_catalog


def _signature(rule):
    return json.dumps(rule, sort_keys=True, ensure_ascii=False)


def cost_feasible_space(result, cap=0.60):
    """Count cost-feasible labels; PCFG A1 outcomes remain unevaluated.

    Website rules here contain no historical blacklist. With a fixed seed and
    user identifier, simulate_users depends on the rule fields, not its site
    name; equal rule dictionaries therefore have equal per-cohort responses.
    """
    current = site_catalog()['sites']
    frozen = {row['id']: row for row in result['site_catalog']['sites']}
    source_for = {}
    for site in current:
        if site['basis'] not in ('partial_hard', 'recommended_scenario'):
            continue
        key = 'site_' + site['id']
        if key in result['controls']:
            source_for[key] = key
            continue
        equivalent = next((other for other in current
                           if _signature(other['rule']) == _signature(site['rule'])
                           and 'site_' + other['id'] in result['controls']
                           and frozen.get(other['id'], {}).get('rule') == other['rule']), None)
        if equivalent is not None:
            source_for[key] = 'site_' + equivalent['id']
    eligible = [site for site in current
                if site['basis'] in ('partial_hard', 'recommended_scenario')]
    if len(source_for) != len(eligible):
        missing = [site['id'] for site in eligible
                   if 'site_' + site['id'] not in source_for]
        return {'status': 'insufficient_fixed_controls', 'missing': missing}
    cohorts = len(result['cohorts'])
    rows = []
    for i in range(cohorts):
        allowed = []
        for site in eligible:
            key = 'site_' + site['id']
            observed = result['controls'][source_for[key]]['timeline'][i]['response']
            if observed['pending_users'] == 0 and observed['modification_rate'] <= cap:
                allowed.append(site)
        hard = [site for site in allowed if site['basis'] == 'partial_hard']
        rows.append({
            'cohort_id': i + 1,
            'hard_labels': [site['id'] for site in hard],
            'all_labels': [site['id'] for site in allowed],
            'distinct_hard_rules': len({_signature(site['rule']) for site in hard}),
            'distinct_all_rules': len({_signature(site['rule']) for site in allowed}),
        })
    return {
        'status': 'cost_feasible_structure_only; PCFG A1 paths not evaluated',
        'source_run_id': result['metadata']['run_id'],
        'cost_cap_each_cohort': cap,
        'cohorts': rows,
        'hard_label_paths': prod(len(row['hard_labels']) for row in rows),
        'all_label_paths': prod(len(row['all_labels']) for row in rows),
        'distinct_hard_rule_paths': prod(row['distinct_hard_rules'] for row in rows),
        'distinct_all_rule_paths': prod(row['distinct_all_rules'] for row in rows),
        'inferred_equal_rule_controls': {name: source for name, source in source_for.items()
                                         if name != source},
        'scope': ('Only each batch\'s modification rate and pending count are filtered. '
                  'Site rules and deterministic response do not depend on earlier cohorts. '
                  'Neither distribution nor retrained PCFG A1 is scored; no path optimum is claimed.'),
    }


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('report', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = json.loads(args.report.read_text(encoding='utf-8'))
    audit = cost_feasible_space(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n',
                           encoding='utf-8')
    print(json.dumps({key: audit.get(key) for key in
                      ('status', 'hard_label_paths', 'all_label_paths',
                       'distinct_all_rule_paths')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
