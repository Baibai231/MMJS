import unittest

from experiments.registration_exchange import burden_failures, select_nominee, confirm_nominee, generate_candidates
from policy.registration_joint import JointRule, Preferences
from collections import Counter


CFG = {'cost_saving_fraction': .05, 'min_gain': .0005, 'outside_increase': .02,
       'frozen_increase': .005, 'release_pool': 3, 'release_sizes': [1, 3],
       'add_pool': 3, 'add_sizes': [1], 'fit_shortlist': 6}


def row(number, roles, risk, attempts, edits=2):
    return {'number': number, 'roles': roles, 'predicted_A1': risk,
            'predicted_outside': 0, 'frozen_rate': .01,
            'cost': {'mean_extra_submissions': attempts, 'mean_edit_distance': edits,
                     'modification_rate': .8, 'more_than_three_fraction': .1,
                     'pending_users': 0}}


class ExchangeTests(unittest.TestCase):
    def test_lower_risk_with_greater_burden_cannot_win(self):
        rows = [row(0, ['hold', 'baseline'], .10, 2), row(1, ['add'], .05, 2.1)]
        self.assertIsNone(select_nominee(rows, CFG))
        self.assertIn('mean_extra_submissions', rows[1]['failures'])

    def test_edit_distance_constraint_is_not_replaced_by_attempts(self):
        rows = [row(0, ['hold', 'baseline'], .10, 2), row(1, ['release'], .08, 1, edits=4)]
        self.assertIsNone(select_nominee(rows, CFG))
        self.assertIn('mean_edit_distance', rows[1]['failures'])

    def test_confirmation_never_replaces_failed_nominee_with_second_choice(self):
        rows = [row(0, ['hold', 'baseline'], .10, 2), row(1, ['release'], .11, 1)]
        self.assertEqual(confirm_nominee(rows, 1, CFG)[0], 0)
        rows[1]['predicted_A1'] = .08
        self.assertEqual(confirm_nominee(rows, 1, CFG), (1, 'confirmed_bidirectional_update'))

    def test_candidates_can_release_baseline_and_keep_common_blacklist(self):
        baseline = JointRule(minimum=8, common=frozenset({'forbidden'}), shapes=frozenset({'L8'}))
        response = Preferences(['abcdefgh', 'abcd1234'], limit=100)
        records = [(i, 'abcdefgh') for i in range(30)]
        proposals, screening = generate_candidates(baseline, baseline, Counter(), records,
            response, frozenset(), CFG, 11, lambda _: None)
        self.assertTrue(any(x['rule'].shapes < baseline.shapes for x in proposals))
        self.assertTrue(all(x['rule'].common == baseline.common for x in proposals))
        self.assertTrue(all(not x['rule'].accepts('forbidden') for x in proposals))
        self.assertTrue(any('baseline' in x['roles'] for x in proposals))


if __name__ == '__main__':
    unittest.main()
