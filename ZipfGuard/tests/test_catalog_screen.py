import unittest
from collections import Counter

from policy.user_response import phrase_vocabulary, weighted_pool
from tools.run_candidate_pool_pilot import settings
from tools.screen_policy_catalog import profile_rule, screen_profile
from tools.check_catalog_stability import context_users


def profile(length=8, history='off', blocklist=0):
    return {'id': 'test', 'min_length': length, 'required_classes': 0,
            'development_top_blocklist': blocklist,
            'historical_hotspot_blocklist': history, 'deny_features': []}


class CatalogScreenTests(unittest.TestCase):
    def test_order_diagnostics_preserve_user_identity(self):
        words = ['short', 'already-long-password', 'short', 'unique-password'] * 10
        train = Counter({'seed-password': 20, 'another-seed': 20})
        args = (tuple(train), settings(), weighted_pool(train), phrase_vocabulary(train))
        original, original_final, _ = screen_profile(profile(), words, *args, details=True)
        for name in ('shuffle43', 'popular_first', 'rare_first'):
            reordered, ids = context_users(words, name)
            changed, changed_final, _ = screen_profile(profile(), reordered, *args, details=True, identifiers=ids)
            self.assertEqual(original_final, changed_final)
            self.assertEqual(original['modified_users'], changed['modified_users'])

    def test_history_cannot_read_a_future_user(self):
        past = Counter({'past-password': 2})
        rule = profile_rule(profile(history='on'), ('training-password',), past, set())
        self.assertFalse(rule.accepts('past-password'))
        self.assertTrue(rule.accepts('future-password'))

    def test_switching_to_plain_policy_does_not_inherit_blacklists(self):
        rule = profile_rule(profile(), ('training-password',),
                            Counter({'past-password': 2}), {'past-password'})
        self.assertTrue(rule.accepts('training-password'))
        self.assertTrue(rule.accepts('past-password'))

    def test_paired_validation_users_are_retained_without_cost_filter(self):
        words = ['short', 'already-long-password'] * 20
        train = Counter({'seed-password': 20, 'another-seed': 20})
        args = (words, tuple(train), settings(), weighted_pool(train), phrase_vocabulary(train))
        weak = screen_profile(profile(), *args)
        strong = screen_profile(profile(length=16), *args)
        for row in (weak, strong):
            self.assertEqual(row['validation_users'], len(words))
            self.assertEqual(row['registered_users'], len(words))
            self.assertEqual(row['pending_users'], 0)
            self.assertFalse(row['attack_evaluated'])
            self.assertFalse(row['shortlist_selected'])
        self.assertGreaterEqual(strong['modification_rate'], weak['modification_rate'])


if __name__ == '__main__':
    unittest.main()
