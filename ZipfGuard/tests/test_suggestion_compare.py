import unittest

from experiments.suggestion_compare import (
    HELD_OUT,
    catalog,
    compare_scenario,
    registered_candidates,
    sample_scenario,
    select_budget_plan,
    summarize_seeds,
)
from experiments.suggestion_compare import _head_model
from core.htpg_features import HTPGFeatureExtractor


class SuggestionCompareTests(unittest.TestCase):
    def test_plan_does_not_read_test_and_budget_is_not_saturated(self):
        scenario = sample_scenario(size=360, seed=11, exponent=0.55)
        extractor = HTPGFeatureExtractor(["joy"], ["smith"])
        model = _head_model(scenario["train"], extractor)
        candidates = registered_candidates(catalog())
        first = select_budget_plan(scenario, model, candidates, 30)
        mutated = dict(scenario)
        mutated["test"] = [HELD_OUT[0]] * len(scenario["test"])
        second = select_budget_plan(mutated, model, candidates, 30)
        self.assertEqual(first["features"], second["features"])
        report = compare_scenario(scenario, budget=30)
        self.assertFalse(report["test_used_for_selection"])
        self.assertFalse(report["plaintext_retained"])
        self.assertFalse(report["main_budget_saturated"])
        self.assertLess(report["arms"]["none"]["adaptive_worst_rate"], 0.99)
        self.assertIn("absolute_point_change_vs_none", report["arms"]["budget_cost"])

    def test_unknown_strings_stay_outside_the_registered_set(self):
        candidates = set(registered_candidates(catalog()))
        self.assertTrue(set(HELD_OUT).isdisjoint(candidates))
        opened = compare_scenario(
            sample_scenario(size=360, seed=11, exponent=0.55, unknown_test_fraction=0.25),
            budget=30,
        )
        self.assertGreater(opened["unknown_test_count"], 0)
        self.assertLess(opened["arms"]["none"]["adaptive_coverage"], 1)

    def test_modern_baseline_and_paired_interval_stay_on_the_same_users(self):
        report = compare_scenario(sample_scenario(size=360, seed=11, exponent=0.55), budget=30)
        modern = report["arms"]["modern_blocklist"]
        paired = modern["paired_vs_none"]["frequency"]
        self.assertEqual(modern["features"], ["train_head_blocklist", "min_length_8"])
        self.assertEqual(paired["n"], 360 - int(360 * 0.6) - int(360 * 0.2))
        self.assertLessEqual(paired["ci95"][0], paired["point"])
        self.assertGreaterEqual(paired["ci95"][1], paired["point"])
        self.assertNotIn("password", str(modern["features"]))
        summary = summarize_seeds((11, 12), size=360, budget=30)
        self.assertEqual(summary["closed_catalog"]["budget_cost"]["seeds"], 2)
        self.assertIn("modern_blocklist", summary["open_unknown"])


if __name__ == "__main__":
    unittest.main()
