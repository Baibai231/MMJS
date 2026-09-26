import unittest

from collections import Counter

from core.attack_stream import account_emissions, checkpoint_raw, evaluate_ordered_stream, prepare_generator
from experiments.research19_splits import unique_disjoint_rows
from core.distributions import models_within_bic
from experiments.distribution_diagnostics import summarize_audit
from experiments.feature_stability import summarize_features
from experiments.fit_benchmark import benchmark_status
from experiments.split_stability import mass_cutoff, scale_factor, scaled_curvature_peak
from core.htpg_fit import curvature_maximum


class Research19PipelineTests(unittest.TestCase):
    def test_targets_cannot_enter_the_generator(self):
        prepared = prepare_generator(training_ids=["train-a"])
        self.assertFalse(prepared["targets_seen_by_generator"])
        with self.assertRaises(ValueError):
            prepare_generator(training_ids=["train-a"], targets=["held-out"])

    def test_stream_counts_duplicates_invalid_and_interruption(self):
        counted = account_emissions([
            {"text": "alpha", "valid": True},
            {"text": "alpha", "valid": True},
            {"text": "", "valid": False},
            {"text": "beta", "valid": True, "in_domain": False},
            {"text": "later", "valid": True, "interrupted": True},
        ])
        self.assertEqual(counted["raw_emissions"], 5)
        self.assertEqual(counted["valid_candidates"], 3)
        self.assertEqual(counted["unique_candidates"], 2)
        self.assertEqual(counted["duplicate_count"], 1)
        self.assertEqual(counted["outside_domain"], 1)
        self.assertTrue(counted["interrupted"])
        self.assertNotIn("later", counted["ordered_unique"])
        scored = evaluate_ordered_stream(["other", "alpha"], ["alpha"], budgets=(1, 2))
        self.assertEqual(scored["points"][0]["cracked"], 0)
        self.assertEqual(scored["points"][1]["cracked"], 1)
        checkpoint = checkpoint_raw(counted)
        self.assertEqual(checkpoint["resume_from_raw_index"], 5)
        self.assertTrue(checkpoint["interrupted"])
        self.assertFalse(checkpoint["plaintext_retained"])

    def test_unique_disjoint_split_keeps_a_string_in_one_side(self):
        counts = Counter({"alpha": 5, "beta": 1, "gamma": 3})
        rows = unique_disjoint_rows(counts, seed=19)
        sets = {name: set(values) for name, values in rows.items()}
        self.assertEqual(sets["train"] & sets["validation"], set())
        self.assertEqual(sets["train"] & sets["test"], set())
        self.assertEqual(sum(len(values) for values in rows.values()), 9)

    def test_bic_screen_drops_a_distant_model(self):
        models = [
            {"id": "close", "bic": 100},
            {"id": "also", "bic": 109},
            {"id": "far", "bic": 150},
        ]
        kept = [item["id"] for item in models_within_bic(models)]
        self.assertEqual(kept, ["close", "also"])

    def test_curvature_peak_scales_with_count_multiplier(self):
        peak = curvature_maximum(1000, 1)
        scaled = scaled_curvature_peak(1000, 1, 10)
        self.assertAlmostEqual(scaled / peak, scale_factor(1, 10))
        self.assertEqual(mass_cutoff([50, 30, 20], 0.5), 1)
        self.assertEqual(mass_cutoff([10, 10, 10], 0.5), 2)

    def test_nineteen_site_summary_does_not_treat_anomalies_as_account_risk(self):
        report = summarize_audit()
        self.assertEqual(report["sites"], 19)
        self.assertIn("linkedin", report["account_risk_not_claimed_sites"])
        self.assertIn("ashleymadison", report["account_risk_not_claimed_sites"])
        self.assertTrue(report["not_an_attack_result"])
        self.assertFalse(report["plaintext_retained"])

    def test_feature_summary_does_not_treat_correlation_as_a_fix(self):
        report = summarize_features()
        self.assertEqual(report["sites"], 19)
        self.assertIn("rockyou", report["features_skipped"])
        self.assertIn("taobao", report["english_lexicon_on_other_language_metadata"])
        self.assertIsNone(report["rows"][0]["causal_modification_effect"])
        self.assertEqual(report["attack_risk_association"], "incomplete")
        fit = benchmark_status()
        self.assertFalse(fit["independent_test_on_19_sites"])
        self.assertFalse(fit["claim_supported"])


if __name__ == "__main__":
    unittest.main()
