import unittest
from collections import Counter

from policy.registration_joint import JointRule
from experiments.registration_segreti_dynamic import (
    anchored_candidates, add_risk_failures, select_winner, confirm_choice,
)


def row(number, roles, rate):
    return {"number": number, "roles": roles, "predicted_A1": rate,
            "frozen_rate": .01, "predicted_outside": 0,
            "collision_forecast": .001, "cost": {"mean_extra_submissions": 2},
            "rule": {"shape_size": 213+number}, "failures": []}


class AnchoredDynamicTests(unittest.TestCase):
    cfg = {"min_gain": .005, "frozen_increase": .005, "outside_increase": .02}

    def test_large_baseline_never_truncated_and_both_anchors_survive(self):
        shapes = frozenset(f"existing{i}" for i in range(213))
        baseline = JointRule(shapes=shapes, j=213)
        hold = JointRule(shapes=shapes | {"L9D1"}, j=214)
        proposals = anchored_candidates(
            baseline, hold, Counter({"password1": 50, "password12": 40}), [5, 20])
        self.assertTrue(any("baseline" in p["roles"] for p in proposals))
        self.assertTrue(any("hold" in p["roles"] for p in proposals))
        self.assertTrue(all(shapes <= p["rule"].shapes for p in proposals))
        self.assertTrue(any(len(p["rule"].shapes) > 213 for p in proposals))
        self.assertEqual(len(proposals), len({p["rule"].signature() for p in proposals}))

    def test_deduplicated_baseline_keeps_both_labels(self):
        baseline = JointRule(shapes=frozenset({"L8"}))
        proposals = anchored_candidates(baseline, baseline, Counter(), [5, 20])
        self.assertEqual(len(proposals), 1)
        self.assertTrue({"hold", "baseline"} <= set(proposals[0]["roles"]))

    def test_losing_a_baseline_structure_is_rejected(self):
        baseline = JointRule(shapes=frozenset({"L8", "L9"}))
        with self.assertRaises(ValueError):
            anchored_candidates(baseline, JointRule(shapes=frozenset({"L8"})), Counter(), [5])

    def test_selection_must_improve_over_baseline_not_only_hold(self):
        rows = [row(0, ["hold"], .15), row(1, ["baseline"], .10),
                row(2, ["baseline_plus_5"], .12)]
        winner, gain = select_winner(rows, self.cfg)
        self.assertIsNone(winner)
        rows[2]["predicted_A1"] = .09
        winner, gain = select_winner(rows, self.cfg)
        self.assertEqual(winner["number"], 2)
        self.assertAlmostEqual(gain, .01)

    def test_confirmation_rejects_winner_when_other_anchor_is_better(self):
        rows = [row(0, ["hold"], .10), row(1, ["baseline"], .08),
                row(2, ["baseline_plus_5"], .09)]
        number, status = confirm_choice(rows, 2, self.cfg)
        self.assertEqual(number, 1)
        self.assertEqual(status, "restore_segreti_baseline")
        self.assertIn("confirmation_insufficient_gain", rows[2]["failures"])

    def test_baseline_cannot_fail_its_relative_guard(self):
        baseline = row(1, ["baseline"], .1)
        bad = row(2, ["hold_plus_5"], .08)
        bad["frozen_rate"] = .1
        add_risk_failures([baseline, bad], baseline, self.cfg)
        self.assertEqual(baseline["failures"], [])
        self.assertIn("frozen_risk_increase", bad["failures"])

    def test_confirmed_extension_and_unchanged_anchor(self):
        rows = [row(0, ["hold", "baseline"], .1), row(1, ["baseline_plus_5"], .08)]
        self.assertEqual(confirm_choice(rows, 1, self.cfg), (1, "confirmed_extension"))
        self.assertEqual(confirm_choice([row(0, ["hold", "baseline"], .1)], None, self.cfg),
                         (0, "hold_anchor"))

    def test_any_positive_gain_is_accepted_but_zero_is_not(self):
        cfg = {**self.cfg, "min_gain": 0, "gain_tolerance": 1e-12}
        rows = [row(0, ["hold", "baseline"], .1),
                row(1, ["baseline_plus_5"], .1 - .000001)]
        winner, _ = select_winner(rows, cfg)
        self.assertEqual(winner["number"], 1)
        self.assertEqual(confirm_choice(rows, 1, cfg), (1, "confirmed_extension"))
        rows = [row(0, ["hold", "baseline"], .1),
                row(1, ["baseline_plus_5"], .1)]
        self.assertIsNone(select_winner(rows, cfg)[0])
        self.assertEqual(confirm_choice(rows, 1, cfg), (0, "hold_anchor"))


if __name__ == "__main__":
    unittest.main()
