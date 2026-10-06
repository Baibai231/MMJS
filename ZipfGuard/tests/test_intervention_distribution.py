import unittest
from collections import Counter

from core.intervention_distribution import (empirical_top_mass, fitted_top_mass,
    fitted_log_cdf_area, log_cdf_area, moved_counts)
from experiments.intervention_config import load_intervention_config


class DistributionDecisionTests(unittest.TestCase):
    def test_migration_improves_complete_password_cdf_without_ignoring_failures(self):
        before = Counter({'popular': 6, 'other': 2, 'unique': 1})
        rows = [
            {'old': 'popular', 'new': 'new', 'status': 'changed'},
            {'old': 'popular', 'new': 'popular', 'status': 'failed_to_comply'},
        ]
        after = moved_counts(before, rows)
        self.assertEqual(sum(after.values()), sum(before.values()))
        self.assertEqual(after['popular'], 5)
        self.assertLess(empirical_top_mass(after, 1), empirical_top_mass(before, 1))

    def test_fitted_top_mass_is_reproducible_and_no_weight_is_configured(self):
        counts = Counter({'a': 12, 'b': 7, 'c': 4, 'd': 2, 'e': 1})
        first = fitted_top_mass(counts, 2, 42)
        self.assertEqual(first, fitted_top_mass(counts, 2, 42))
        self.assertEqual(first['rank'], 2)
        self.assertTrue(0 < first['top_mass'] <= 1)
        self.assertNotIn('risk_weight', load_intervention_config()['controller'])

    def test_complete_cdf_area_detects_gain_outside_unchanged_top_rank(self):
        before = Counter({'a': 5, 'b': 3, 'c': 2})
        after = Counter({'a': 5, 'b': 2, 'c': 2, 'd': 1})
        self.assertEqual(empirical_top_mass(before, 1), empirical_top_mass(after, 1))
        self.assertLess(log_cdf_area(after), log_cdf_area(before))
        self.assertEqual(fitted_log_cdf_area(before, 42), fitted_log_cdf_area(before, 42))
