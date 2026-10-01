"""Count ten-cohort website-label paths; this does not evaluate any path."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from policy.site_catalog import site_catalog


def audit(cohorts=10):
    if cohorts < 1:
        raise ValueError('批次数必须为正数')
    sites = site_catalog()['sites']
    hard = [row for row in sites if row['basis'] == 'partial_hard']
    optional = [row for row in sites if row['basis'] == 'recommended_scenario']
    excluded = [row for row in sites if row['basis'] not in
                ('partial_hard', 'recommended_scenario')]
    groups = defaultdict(list)
    for row in hard + optional:
        groups[json.dumps(row['rule'], sort_keys=True)].append(row['site'])
    return {
        'status': 'structural_count_only; no path outcomes evaluated',
        'cohorts': cohorts,
        'first_cohort': 'free choice; repetitions permitted',
        'listed_labels': len(sites),
        'listed_structural_paths': len(sites) ** cohorts,
        'hard_rule_labels': [row['id'] for row in hard],
        'hard_rule_structural_paths': len(hard) ** cohorts,
        'recommended_scenario_labels': [row['id'] for row in optional],
        'all_executable_labels': len(hard) + len(optional),
        'executable_structural_paths': (len(hard) + len(optional)) ** cohorts,
        'equal_rule_groups': [names for names in groups.values() if len(names) > 1],
        'excluded_labels': [{'id': row['id'], 'basis': row['basis'],
                             'reason': row['limitations']} for row in excluded],
        'objective': {'primary': 'minimum PCFG A1 hit rate at 10^6 estimated guesses',
                      'constraint': 'each cohort modification rate <= 0.60',
                      'tie_break': 'minimum final password collision probability'},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cohorts', type=int, default=10)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.cohorts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n',
                           encoding='utf-8')
    print(json.dumps({'status': result['status'],
                      'listed_structural_paths': result['listed_structural_paths'],
                      'executable_structural_paths': result['executable_structural_paths']},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
