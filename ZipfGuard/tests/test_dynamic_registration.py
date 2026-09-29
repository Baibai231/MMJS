"""Behavioral checks for chronology, distribution and finite-rank claims."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.open_attack import GuessRun
from core.guess_difficulty import guess_difficulty
from core.paired_risk import paired_attack_difference
from core.registration import load_registration, parse_record, registration_from_counts
from core.strength_change import observed_rank, rank_change
from core.uniformity import concentration
from experiments.dynamic_config import load_dynamic_config
from experiments.dynamic_pipeline import run_dynamic_pipeline
from policy.dynamic_catalog import compatible, initial_policy, next_actions


class DynamicRegistrationTests(unittest.TestCase):
    def test_real_corpus_scan_cache_preserves_sample_and_skips_first_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'counts.txt'
            source.write_bytes(b'8 alpha\n7 beta\n6 gamma\n5 delta\n4 epsilon\n')
            cache = Path(folder) / 'cache'
            arguments = {'users': 7, 'development': 8, 'seed': 11,
                         'cohort_size': 7, 'cache_dir': cache}
            with patch('core.registration.parse_record', wraps=parse_record) as parse:
                first = load_registration(source, **arguments)
                self.assertEqual(parse.call_count, 10)
            with patch('core.registration.parse_record', wraps=parse_record) as parse:
                second = load_registration(source, **arguments)
                self.assertEqual(parse.call_count, 5)
            self.assertEqual(first['cohorts'], second['cohorts'])
            self.assertEqual(first['development'], second['development'])
            self.assertEqual(first['metadata'], second['metadata'])
            source.write_bytes(source.read_bytes() + b'3 zeta\n')
            with patch('core.registration.parse_record', wraps=parse_record) as parse:
                load_registration(source, **arguments)
                self.assertEqual(parse.call_count, 12)

    def test_collision_probability(self):
        self.assertEqual(concentration(['a', 'a', 'b'])['collision_probability'], 1 / 3)
        self.assertEqual(concentration(['a', 'b', 'c'])['collision_probability'], 0)

    def test_monotone_candidates(self):
        policy = initial_policy()
        candidates = next_actions(policy, {'password': 20, 'short': 10},
                                  {'password': 15, 'short': 7})
        self.assertTrue(all(compatible(policy, other) for _, other in candidates))

    def test_incomplete_guess_budget_is_unresolved(self):
        run = GuessRun('mock', {'old': 2}, {},
                       {'charged_count': 2, 'stop_reason': 'raw_limit'}, {})
        before = observed_rank('old', [run], 100)
        after = observed_rank('new', [run], 100)
        self.assertEqual(rank_change(before, after, budget=100)['status'], 'unresolved')
        self.assertEqual(guess_difficulty([run], {'old': 1, 'new': 1}, 100)['status'],
                         'incomplete_budget')

    def test_paired_difference_uses_common_completed_users(self):
        left = GuessRun('left', {'a': 1}, {},
                        {'charged_count': 2, 'stop_reason': 'reached_budget'}, {})
        right = GuessRun('right', {'y': 1}, {},
                         {'charged_count': 2, 'stop_reason': 'reached_budget'}, {})
        comparison = paired_attack_difference([('a', 'x'), ('b', 'y')],
                                              [left], [right], [2])[0]
        self.assertEqual(comparison['paired_users'], 2)
        self.assertEqual(comparison['left_only_hit'], 1)
        self.assertEqual(comparison['right_only_hit'], 1)
        self.assertEqual(comparison['difference'], 0)

    def test_future_data_cannot_change_earlier_policy(self):
        cfg = load_dynamic_config('dynamic_smoke')
        cfg['data'].update(users=90, development=90, cohort_size=30)
        cfg['budgets'] = [10, 100]
        data = registration_from_counts({'firstSecret123': 45,
                                         'secondSecret123': 45,
                                         'thirdSecret123': 45,
                                         'fourthSecret123': 45},
                                        users=90, development=90,
                                        seed=cfg['seed'], cohort_size=30)
        changed = {**data, 'cohorts': [list(batch) for batch in data['cohorts']]}
        changed['cohorts'][2] = ['futureOnly123!'] * 30
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            a = run_dynamic_pipeline(cfg, dataset=data, output_dir=first)
            b = run_dynamic_pipeline(cfg, dataset=changed, output_dir=second)
            self.assertEqual([r['policy'] for r in a['cohorts'][:2]],
                             [r['policy'] for r in b['cohorts'][:2]])
            self.assertEqual(a['baseline_completed_distribution']['users'],
                             a['final_distribution']['users'])
            self.assertEqual(a['attacks']['baseline_completed_F']['minauto'][-1]['target_weight'],
                             a['final_distribution']['users'])
            self.assertNotIn('firstSecret123', Path(first, 'report.json').read_text('utf-8'))
            self.assertIn('firstSecret123', Path(first, 'user_outcomes_private.jsonl').read_text('utf-8'))


if __name__ == '__main__':
    unittest.main()
