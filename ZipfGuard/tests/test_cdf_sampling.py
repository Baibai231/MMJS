import json
import unittest
from collections import Counter

import numpy as np

from core.cdf_sampling import cdf_distance, fit_cdf_sampling, frequency_sample, replicate_fit
from core.uniformity import concentration
from experiments.cdf_fit_benchmark import counts_from_summary, compare_fits, intervention_fit_diagnostics
from web.cdf_fit_presentation import render_fit_diagnostics, fit_chart_specs


class CdfSamplingTests(unittest.TestCase):
    def test_lossless_plateaus_and_corrupt_input(self):
        summary = concentration(Counter({'a': 5, 'b': 3, 'c': 3, 'd': 1, 'e': 1}), full_curve=True)
        self.assertEqual(counts_from_summary(summary).tolist(), [5, 3, 3, 1, 1])
        summary['full_rank_frequency'] = [[1, 5], [5, 1]]
        with self.assertRaises(ValueError):
            counts_from_summary(summary)

    def test_full_support_error_and_bin_zero(self):
        self.assertAlmostEqual(cdf_distance([4], [1, 1, 1, 1]), .75)
        self.assertAlmostEqual(cdf_distance([1, 1, 1, 1], [4]), .75)
        self.assertEqual(frequency_sample(.5, 1., [.1, .2, .6, .9]).tolist(), [2, 2])

    def test_invalid_samples(self):
        for c, s, u in [(0, .5, [.5]), (.5, 0, [.5]), (.5, 2, [.5]),
                        (.5, .5, [float('nan')]), (.5, .5, []), (1e-9, .001, [.9])]:
            with self.assertRaises(ValueError):
                frequency_sample(c, s, u)

    def test_reproducible_search_and_independent_draws(self):
        data = frequency_sample(.04, .4, np.random.default_rng(44).random(4000))
        first = fit_cdf_sampling(data, seed=7, iterations=6)
        second = fit_cdf_sampling(data, seed=7, iterations=6)
        self.assertEqual(first, second)
        replication = replicate_fit(data, first, seeds=[8, 9, 10])
        self.assertLess(replication['mean_max_cdf_error'], .08)
        self.assertEqual(replication['seeds'], [8, 9, 10])
        json.dumps(first, allow_nan=False)

    def test_old_google_report_only_benchmarks_unmodified_population(self):
        summary = concentration(Counter({str(i): 2+i%3 for i in range(30)}), full_curve=True)
        report = {'config': {'seed': 3}, 'metadata': {'run_id': 'test'},
                  'baseline': {'distribution': summary}, 'google_round_zipf': {'arms': {}}}
        diagnostics = intervention_fit_diagnostics(report)
        self.assertEqual([r['key'] for r in diagnostics['results']], ['baseline'])
        self.assertFalse(diagnostics['policy_comparison_valid'])
        report['distribution_fits'] = diagnostics
        self.assertEqual(len(fit_chart_specs(report)['cdf_fit_baseline'][1]), 2)
        html = render_fit_diagnostics(report)
        self.assertIn('input type="checkbox"', html)
        self.assertIn('旧的局部试点', html)
        self.assertNotIn('普通 Zipf 拟合', html)
        self.assertNotIn('previous_models', diagnostics['results'][0]['fit'])
        json.dumps(diagnostics, allow_nan=False)

    def test_heldout_accounts_disjoint_and_errors_finite(self):
        result = compare_fits([80, 45, 20, 10]+[1]*200, seed=9)
        check = result['heldout_shape_check']
        self.assertEqual(check['training_accounts']+check['validation_accounts'], 355)
        self.assertIn('held-out', check['cdf_sampling']['scope'])
        for key in ('zipf', 'cdf_zipf', 'cdf_sampling'):
            self.assertTrue(0 <= check[key]['mean_max_cdf_error'] <= 1)


if __name__ == '__main__':
    unittest.main()
