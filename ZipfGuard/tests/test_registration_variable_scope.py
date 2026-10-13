"""Behavioral checks for the revised single policy; no attack training here."""
import unittest
from itertools import islice
from collections import Counter
from unittest.mock import Mock, patch
from pathlib import Path
import tempfile
from policy.registration_joint import Preferences, JointRule
from experiments.registration_variable_scope import UnlimitedPreferences, scoped_candidates, ScopedRule, score_training_group
from experiments.registration_variable_pipeline import cost_failures


class VariableScopeTests(unittest.TestCase):
    def test_all_burden_limits_removed_but_pending_never_dropped(self):
        cfg = {'user_cost_caps_disabled': True}
        summary = {'modification_rate': 1, 'mean_extra_submissions': 500,
                   'more_than_three_fraction': 1, 'pending_users': 0}
        self.assertEqual(cost_failures(summary, cfg), [])
        summary['pending_users'] = 1
        self.assertEqual(cost_failures(summary, cfg), ['pending_users'])

    def test_unbounded_preferences_preserve_paired_stream(self):
        args = (['password', 'secondpass'], 8, (.6, .25, .15), 'mixed', 1000)
        old, new = Preferences(*args), UnlimitedPreferences(*args)
        self.assertEqual(list(old.proposals('short', 17, 11)),
                         list(islice(new.proposals('short', 17, 11), 1001)))
        self.assertEqual(len(list(islice(new.proposals('short', 17, 11), 1100))), 1100)
        r = new.simulate([(17, 'short')], JointRule(), 11)
        self.assertEqual(r['summary']['pending_users'], 0)
        self.assertEqual(r['summary']['terminal_state'], 'continue_until_accepted_no_retry_cap')

    def test_scope_changes_rejection_inside_same_structure(self):
        base = JointRule()
        recent = Counter({'password1': 25, 'password2': 25})
        memberships = {5: frozenset({'password1'}), 10: frozenset({'password1', 'password2'})}
        proposals = scoped_candidates(base, recent, base, [0], [0, 5], memberships)
        selective = [r for r, _ in proposals if isinstance(r, ScopedRule) and r.soft_shapes]
        self.assertEqual({r.risk_cutoff for r in selective}, {5, 10})
        light = next(r for r in selective if r.risk_cutoff == 5)
        heavy = next(r for r in selective if r.risk_cutoff == 10)
        self.assertFalse(light.accepts('password1'))
        self.assertTrue(light.accepts('password2'))
        self.assertFalse(heavy.accepts('password2'))
        self.assertTrue(heavy.accepts('unlisted3'))
        self.assertEqual(sum(hold for _, hold in proposals), 1)
        self.assertEqual(len({r.signature() for r, _ in proposals}), len(proposals))

    def test_group_risk_matches_direct_dual_scoring(self):
        from tests.test_registration_dual import RegistrationDualTests
        from experiments.registration_dual_pipeline import risk
        index = RegistrationDualTests().index()
        task = {'root': 'unused', 'key': 'key', 'counts': Counter({'abc': 1}),
                'history': Counter({'abc': 3, 'bbb': 1}), 'cohort_size': 2,
                'cfg': {'objective_budget': 10, 'preview_users': 2},
                'frozen_ranks': {'cab': 4},
                'items': [{'number': 0, 'preview_counts': Counter({'cab': 1, 'bbb': 1})}]}
        cache = Mock();cache.index.return_value = index
        with patch('experiments.registration_variable_scope.ReusingDualAttackCache', return_value=cache):
            score = score_training_group(task)['items'][0]['score']
        self.assertEqual(score['predicted_A1'], 4/6)
        self.assertEqual(score['frozen_rate'], .5)
        self.assertEqual(score['preview_actual_dual'], risk(index, task['items'][0]['preview_counts'], [10], 2))


if __name__ == '__main__':
    unittest.main()
