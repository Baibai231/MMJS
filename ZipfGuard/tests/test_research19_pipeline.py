import json
import tempfile
import unittest
from pathlib import Path

from collections import Counter

from core.attack_stream import account_emissions, checkpoint_raw, evaluate_ordered_stream, prepare_generator, score_axes
from experiments.build_figures import _destination, _polyline, main as build_figures_main
from experiments.research19_neural_eval import scheduled_passllm_trajectories
from experiments.research19_splits import digest_multiset, unique_disjoint_rows
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

    def test_duplicate_emissions_do_not_share_one_budget_axis(self):
        records = [{"text": "wrong", "valid": True} for _ in range(100)]
        records.append({"text": "target", "valid": True})
        records.append({"text": "other", "valid": True})
        ledger = score_axes(records, ["target"], (2, 100), completion="reached_budget")
        raw = {point["budget"]: point for point in ledger["axes"]["raw_position"]["points"]}
        unique = {point["budget"]: point for point in ledger["axes"]["unique_position"]["points"]}
        self.assertEqual(raw[100]["cracked"], 0)
        self.assertFalse(raw[100]["incomplete"])
        self.assertIsNone(unique[100]["cracked"])
        self.assertTrue(unique[100]["incomplete"])
        self.assertEqual(unique[2]["cracked"], 1)
        self.assertFalse(unique[2]["incomplete"])

    def test_invalid_emissions_consume_raw_budget(self):
        records = [{"text": "", "valid": False} for _ in range(100)]
        records.append({"text": "target", "valid": True})
        ledger = score_axes(records, ["target"], (1, 101), completion="reached_budget")
        raw = {point["budget"]: point for point in ledger["axes"]["raw_position"]["points"]}
        valid = {point["budget"]: point for point in ledger["axes"]["valid_position"]["points"]}
        self.assertEqual(ledger["raw_emissions"], 101)
        self.assertEqual(raw[1]["cracked"], 0)
        self.assertFalse(raw[1]["incomplete"])
        self.assertEqual(raw[101]["cracked"], 1)
        self.assertEqual(valid[1]["cracked"], 1)
        self.assertTrue(valid[101]["incomplete"])

    def test_interrupt_overrides_a_reached_budget_claim(self):
        records = [{"text": "", "valid": False} for _ in range(10)]
        records.append({"text": "target", "valid": True})
        records.append({"text": "later", "valid": True, "interrupted": True})
        ledger = score_axes(records, ["target"], (11, 100), completion="reached_budget")
        self.assertTrue(ledger["interrupted"])
        self.assertEqual(ledger["completion"], "interrupted")
        self.assertEqual(ledger["requested_completion"], "reached_budget")
        raw = {point["budget"]: point for point in ledger["axes"]["raw_position"]["points"]}
        self.assertEqual(raw[11]["cracked"], 1)
        self.assertEqual(raw[11]["completion"], "reached_before_interrupt")
        self.assertFalse(raw[11]["incomplete"])
        self.assertIsNone(raw[100]["cracked"])
        self.assertTrue(raw[100]["incomplete"])
        self.assertEqual(raw[100]["completion"], "interrupted")

    def test_short_stream_is_not_a_finished_budget(self):
        ledger = score_axes([{"text": "only", "valid": True}], ["only"], (1000,), completion="unspecified")
        point = ledger["axes"]["raw_position"]["points"][0]
        self.assertTrue(point["incomplete"])
        self.assertIsNone(point["cracked"])
        exhausted = score_axes(
            [{"text": "only", "valid": True}], ["only"], (1000,), completion="dictionary_exhausted",
        )
        finished = exhausted["axes"]["unique_position"]["points"][0]
        self.assertFalse(finished["incomplete"])
        self.assertEqual(finished["cracked"], 1)

    def test_split_digest_keeps_counts_and_newlines(self):
        self.assertNotEqual(digest_multiset(["a", "a", "b"]), digest_multiset(["a", "b", "b"]))
        self.assertNotEqual(digest_multiset(["a\nb"]), digest_multiset(["a", "b"]))

    def test_rate_curves_share_a_zero_to_one_axis(self):
        low = _polyline([(1, 0.01), (2, 0.02)], 640, 360, x_range=(1, 2), y_range=(0, 1))
        high = _polyline([(1, 0.1), (2, 0.2)], 640, 360, x_range=(1, 2), y_range=(0, 1))
        self.assertNotEqual(low, high)
        name, warning = _destination("budget_bars", "robustness-v1")
        self.assertTrue(name.endswith("_historical.svg"))
        self.assertIn("历史", warning)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reports = root / "reports"
            reports.mkdir()
            (reports / "shift_head_grid.json").write_text(json.dumps({
                "protocol": "robustness-v1",
                "mechanisms": {"head_shift": {"900": {"arms": {
                    "modern_blocklist": {"mean": 0.1},
                    "paper_igr_unique": {"mean": None},
                    "budget_cost": {"mean": -0.2},
                    "per_password": {"mean": 0.0},
                }}}},
            }), encoding="utf-8")
            (reports / "robustness_protocol.json").write_text(json.dumps({
                "grid": {"protocol": "robustness-v1", "mechanisms": {"long_tail": {"900": {"arms": {
                    "none": {"mean": 0.0, "modification_mean": 0.0},
                    "legacy_complexity": {"mean": 0.1, "modification_mean": 0.2},
                    "modern_blocklist": {"mean": 0.1, "modification_mean": 0.2},
                    "paper_igr_unique": {"mean": 0.2, "modification_mean": 0.3},
                    "budget_cost": {"mean": 0.2, "modification_mean": 0.4},
                }}}}}
            }), encoding="utf-8")
            build_figures_main(root)
            figures = root / "docs" / "figures"
            shift = (figures / "head_shift_historical.svg").read_text(encoding="utf-8")
            pareto = (figures / "pareto_historical.svg").read_text(encoding="utf-8")
            self.assertFalse((figures / "head_shift.svg").exists())
            self.assertIn("历史", shift)
            self.assertIn("历史", pareto)
            self.assertNotIn("paper", shift)

    def test_passllm_requested_thousand_schedules_more_trajectories(self):
        self.assertEqual(scheduled_passllm_trajectories(1000, 8), 1104)

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
        self.assertTrue(all(row["account_risk_applicable"] is False for row in report["rows"]))
        self.assertTrue(all(row["account_risk_status"] == "unknown" for row in report["rows"]))
        self.assertIn("linkedin", report["account_risk_not_claimed_sites"])
        self.assertIn("ashleymadison", report["account_risk_not_claimed_sites"])
        hak5 = next(row for row in report["rows"] if row["name"] == "hak5")
        self.assertTrue(hak5["occurrence_weighted_metric_available"])
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
