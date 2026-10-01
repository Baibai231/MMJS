"""Visible constraints are constructed; hidden rejection never models dropout."""
import unittest
from unittest.mock import patch

from experiments.dynamic_config import load_dynamic_config, validate_dynamic_config
from policy.open_policy import Rule
from policy.user_response import simulate_users, weighted_pool


class InformedResponseTests(unittest.TestCase):
    def simulate(self, words, rule, **kwargs):
        return simulate_users(words, rule, pool=weighted_pool({'cedar': 1}),
                              vocabulary=('cedar', 'forest'), seed=42, **kwargs)

    def test_visible_rules_satisfied_in_one_amendment_and_valid_original_retained(self):
        words = ['a', '123', '中文', '١', 'AlreadyGood123!']
        rule = Rule('visible', min_length=12, classes=4)
        result = self.simulate(words, rule)
        self.assertTrue(all(rule.accepts(w) for w in result['final_by_user']))
        self.assertEqual([r['attempts'] for r in result['records']], [1, 1, 1, 1, 0])
        self.assertEqual(result['final_by_user'][-1], words[-1])
        self.assertEqual(result['summary']['hidden_rejections'], 0)
        self.assertEqual(result['summary']['pending_users'], 0)
        self.assertEqual(result, self.simulate(words, rule))

    def test_hidden_rejection_continues_after_reporting_threshold(self):
        rule = Rule('hidden', min_length=8, blocklist=frozenset({'blocked1'}))
        with patch('policy.user_response._propose', side_effect=['blocked1'] * 9 + ['allowed1']):
            result = self.simulate(['blocked1'], rule, retry_report_after=8, candidate_limit=20)
        row = result['records'][0]
        self.assertEqual(row['final'], 'allowed1')
        self.assertEqual(row['attempts'], 10)
        self.assertEqual(row['hidden_rejections'], 10)  # original + nine candidates
        self.assertTrue(row['retry_threshold_reached'])
        self.assertEqual(row['registration_status'], 'registered')
        self.assertEqual(result['summary']['retry_reported_users'], 1)

    def test_visible_repair_is_submitted_to_hidden_checks(self):
        rule = Rule('hidden', min_length=8, classes=3, deny=('keyboard_walk',))
        with patch('policy.user_response._propose', return_value='cedar'):
            result = self.simulate(['qwerty'], rule)
        row = result['records'][0]
        self.assertEqual(row['attempts'], 2)
        self.assertEqual(row['hidden_rejections'], 1)
        self.assertTrue(rule.accepts(row['final']))

    def test_resource_limit_preserves_pending_user_without_dropout(self):
        rule = Rule('hidden', min_length=8, blocklist=frozenset({'blocked1'}))
        with patch('policy.user_response._propose', return_value='blocked1'):
            result = self.simulate(['blocked1', 'allowed1'], rule,
                                   retry_report_after=2, candidate_limit=3)
        self.assertEqual(len(result['records']), 2)
        self.assertEqual(result['records'][0]['registration_status'], 'pending')
        self.assertEqual(result['records'][0]['pending_reason'], 'candidate_generation_limit')
        summary = result['summary']
        self.assertEqual(summary['completed_users'] + summary['pending_users'], 2)
        self.assertNotIn('abandoned_users', summary)
        self.assertNotIn('failed_users', summary)

    def test_old_configuration_migrates_to_reporting_threshold(self):
        cfg = load_dynamic_config('dynamic_smoke')
        cfg['controller'].pop('retry_report_after')
        cfg['controller'].pop('candidate_limit')
        cfg['controller'].update(max_attempts=8, abandon_probability=1, min_completion=.9)
        updated = validate_dynamic_config(cfg)['controller']
        self.assertEqual(updated['retry_report_after'], 8)
        self.assertEqual(updated['candidate_limit'], 1000)
        self.assertNotIn('abandon_probability', updated)


if __name__ == '__main__':
    unittest.main()
