import json
import unittest

from core.markov_substitute import NOT_OMEN, enumerate_markov, raw_positions
from core.metrics import evaluate_open_generation
from experiments.robustness_protocol import (
    evaluate_scenario,
    hash_order,
    held_out_strings,
    public_candidates,
    run_ablation,
    sample_mechanism,
    support_strings,
)


class RobustnessProtocolTests(unittest.TestCase):
    def test_markov_stream_keeps_raw_indexes(self):
        train = ["cedar2024"] * 12 + ["maple123"] * 4 + ["riverqwer"] * 2
        stream = enumerate_markov(train, limit=12)
        positions = raw_positions(stream)
        self.assertEqual(list(positions), list(range(1, len(stream) + 1)))
        self.assertGreaterEqual(len(stream), 3)
        self.assertTrue(any(guess not in set(train) for guess in stream))
        target = stream[2]
        opened = evaluate_open_generation(stream, [target], budgets=(2, 3))
        by_budget = {point["budget"]: point for point in opened["points"]}
        self.assertEqual(by_budget[2]["cracked"], 0)
        self.assertEqual(by_budget[3]["cracked"], 1)
        outside = ["qx-a", "qx-b", target]
        prefixed = evaluate_open_generation(outside, [target], budgets=(1, 3))
        prefixed_points = {point["budget"]: point for point in prefixed["points"]}
        self.assertEqual(prefixed_points[1]["cracked"], 0)
        self.assertEqual(prefixed_points[3]["cracked"], 1)
        self.assertIn("不是论文使用的 OMEN", NOT_OMEN)

    def test_support_is_not_the_576_grammar_and_hash_order_is_not_generation_order(self):
        support = support_strings("zipf")
        self.assertGreater(len(support), 576)
        ranked = hash_order(support)
        self.assertNotEqual(ranked, support)
        self.assertEqual(ranked, sorted(ranked, key=lambda value: __import__("hashlib").sha256(value.encode()).hexdigest()))
        candidates = public_candidates("unknown_structure")
        self.assertTrue(set(held_out_strings()).isdisjoint(candidates))

    def test_three_mechanisms_share_budget_and_do_not_saturate(self):
        for mechanism in ("zipf", "long_tail", "unknown_structure"):
            report = evaluate_scenario(sample_mechanism(mechanism, size=300, seed=1), budget=40, with_markov=True)
            rendered = json.dumps(report)
            self.assertNotIn("qxheld", rendered)
            self.assertNotIn("stem00", rendered)
            self.assertFalse(report["main_budget_saturated"])
            self.assertFalse(report["main_budget_decided_by_576"])
            self.assertGreater(report["candidate_count"], 40)
            self.assertEqual(report["denominator"], "test_users")
            for arm in ("none", "legacy_complexity", "modern_blocklist", "paper_igr_unique", "budget_cost"):
                self.assertIn(arm, report["arms"])
            self.assertEqual(report["arms"]["budget_cost"]["adaptive_worst_attacker_protocol"], "closed_hash_order")
            self.assertIsNotNone(report["arms"]["budget_cost"]["open_absolute_point_change_vs_none"])
            self.assertIn("fixed-order-markov-not-omen", report["test_attackers"])
            self.assertEqual(report["arms"]["none"]["paired_vs_none"]["frequency"]["n"], report["test_users"])
        unknown = evaluate_scenario(
            sample_mechanism("unknown_structure", size=300, seed=1), budget=40, with_markov=False,
        )
        self.assertGreater(unknown["unknown_test_count"], 0)
        self.assertLess(unknown["arms"]["none"]["adaptive_coverage"], 1)

    def test_ablation_changes_one_switch_and_shared_template_concentrates(self):
        report = run_ablation(seed=1, size=300, budget=40)
        self.assertIn("budget_cost", report["rows"])
        self.assertEqual(report["rows"]["model_uncertainty"]["status"], "absent")
        self.assertIsNone(report["rows"]["model_uncertainty"]["absolute_point_change_vs_none"])
        self.assertIn("closed_absolute_point_change", report["rows"]["budget_cost"])
        self.assertIn("open_absolute_point_change", report["rows"]["budget_cost"])
        self.assertGreater(
            report["shared_template_concentration"],
            report["per_user_template_concentration"],
        )
        rendered = json.dumps(report)
        self.assertNotIn("stem00", rendered)


if __name__ == "__main__":
    unittest.main()
