"""Mathematical correctness checks for the new anonymous representation."""
from __future__ import annotations

from collections import Counter
from itertools import product
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from policy.privacy_joint_profile import (decreasing_projection, simplex_projection, private_conditional,
    private_marginal, choose_shapes, choose_positions, predict_conditional, predict_marginal)
from policy.registration_joint import JointRule
from tools.run_privacy_joint_study import IdealHiddenState


class AnonymousProfileTests(unittest.TestCase):
    def test_marginals_cannot_identify_joint_overlap(self):
        # The same global frequency spectrum and same structure populations
        # induce different true joint coverage. This is not just rounding error.
        left = IdealHiddenState(Counter({'aaaa': 4, 'bbbb': 1, '1111': 3, '2222': 2}), ['L4', 'D4'], 2)
        right = IdealHiddenState(Counter({'aaaa': 3, 'bbbb': 2, '1111': 4, '2222': 1}), ['L4', 'D4'], 2)
        np.testing.assert_equal(left.shape_counts, right.shape_counts)
        np.testing.assert_equal(left.global_frequencies, right.global_frequencies)
        coverage = []
        for state in (left, right):
            profile = {'counts': state.shape_counts, 'spectra': state.spectra}
            positions = choose_positions(profile, 1)
            estimate = predict_conditional(profile, positions, (0,))
            owners = {state.owners[s][r] for s, r in positions}
            true = sum(n for word, n in state.counts.items() if word in owners or state.word_shape[word] == 0)
            self.assertEqual(estimate, true)
            coverage.append(true)
        self.assertEqual(coverage, [5, 9])

    def test_isotonic_and_simplex_known_solutions(self):
        # Analytic constrained least-squares solutions for independent examples.
        np.testing.assert_allclose(decreasing_projection([4, 1, 3, -2]), [4, 2, 2, 0])
        np.testing.assert_allclose(simplex_projection([-2, 5, 3], 4), [0, 3, 1])

    def test_private_outputs_preserve_public_constraints(self):
        counts = np.array([30.0, 20.0, 0.0])
        frequencies = np.array([[10.0, 5.0, 2.0], [7.0, 3.0, 1.0], [0.0, 0.0, 0.0]])
        for epsilon in [0.5, 1, 2]:
            private = private_conditional(counts, frequencies, epsilon, np.random.default_rng(321))
            np.testing.assert_allclose(private['counts'].sum(), 50)
            self.assertTrue(np.all(private['counts'] >= 0))
            self.assertTrue(np.all(private['spectra'] >= 0))
            self.assertTrue(np.all(np.diff(private['spectra'], axis=1) <= 1e-12))
            self.assertTrue(np.all(private['spectra'].sum(axis=1) <= private['counts'] + 1e-12))
            marginal = private_marginal(counts, np.array([10.0, 7.0, 5.0]), epsilon, np.random.default_rng(322))
            self.assertLessEqual(marginal['frequencies'].sum(), 50 + 1e-12)
            self.assertTrue(np.all(np.diff(marginal['frequencies']) <= 1e-12))

    def test_ideal_numeric_mapping_and_residual_coverage(self):
        words = ('aaaa', 'bbbb', '1111', '2222')
        base = JointRule(0)
        for values in product(range(5), repeat=4):
            if not sum(values) or sum(values) > 7:
                continue
            state = IdealHiddenState(Counter({w: n for w, n in zip(words, values) if n}), ['L4', 'D4'], 3)
            profile = {'counts': state.shape_counts, 'spectra': state.spectra}
            # Choose one shape manually: the policy's threshold20 is intentionally
            # too large for this synthetic population.
            for selected in [(0,), (1,)]:
                for k in [1, 2, 3]:
                    independent = choose_positions(profile, k)
                    residual = choose_positions(profile, k, selected, residual=True)
                    old = predict_conditional(profile, independent, selected)
                    new = predict_conditional(profile, residual, selected)
                    self.assertGreaterEqual(new, old)
                    self.assertTrue(all(s not in selected for s, _ in residual))
            rule, metric = state.rule_and_truth(base, 2, 0, 'B', profile)
            self.assertEqual(metric['predicted_users'], metric['true_users'])
            self.assertEqual(sum(n for w, n in state.counts.items() if not rule.accepts(w)), metric['true_users'])

    def test_numeric_api_does_not_return_password_values(self):
        values = private_conditional(np.array([100.0, 0.0]), np.array([[30.0, 15.0], [0.0, 0.0]]), 1, np.random.default_rng(5))
        self.assertEqual(set(values), {'counts', 'spectra'})
        self.assertTrue(all(isinstance(v, np.ndarray) and np.issubdtype(v.dtype, np.number) for v in values.values()))
        self.assertNotIn(1, choose_shapes(values['counts'], 1, 1))


if __name__ == '__main__':
    unittest.main()
