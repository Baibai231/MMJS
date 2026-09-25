import unittest

from core.markov_substitute import SUBSTITUTE_ID
from experiments.evaluation_validity import finalize_closed_publication, seed_summary
from experiments.robustness_protocol import evaluate_scenario, public_comparison_html, sample_mechanism


class PublicationTests(unittest.TestCase):
    def test_train_leak_blanks_closed_derivatives_and_keeps_open(self):
        row = {
            "headline_published": False,
            "candidate_audit": {
                "train": {"edit_caused_outside": 4, "users": 12, "headline_published": False},
                "validation": {"edit_caused_outside": 0, "users": 4, "headline_published": True},
                "test": {"edit_caused_outside": 0, "users": 4, "headline_published": True},
            },
            "absolute_point_change_vs_none": None,
            "withheld_point_change": 0.05,
            "relative_change_vs_none": 0.2,
            "all_users": {"absolute_point_change": None, "users": 4},
            "head_users": {"frequency_absolute_point_change": 1.0, "users": 2},
            "paired_vs_none": {
                "frequency": {"point": 0.03333, "ci95": [0.01, 0.04], "n": 4, "method": "paired"},
                SUBSTITUTE_ID: {"point": 0.1, "ci95": [0.0, 0.2], "n": 4, "method": "paired"},
            },
        }
        finalize_closed_publication(row, open_attackers={SUBSTITUTE_ID})
        self.assertIsNone(row["head_users"]["frequency_absolute_point_change"])
        self.assertIsNone(row["paired_vs_none"]["frequency"]["point"])
        self.assertEqual(row["paired_vs_none"]["frequency"]["ci95"], [None, None])
        self.assertEqual(row["paired_vs_none"][SUBSTITUTE_ID]["point"], 0.1)
        self.assertEqual(row["closed_diagnostics"]["paired_vs_none"]["frequency"]["point"], 0.03333)
        self.assertIn("train 修改导致漏收 4/12", row["withheld_reason"])
        html = public_comparison_html({
            "support_size": 3, "candidate_count": 3, "budget": 40, "test_users": 4,
            "arms": {"paper_igr_unique": {
                **row,
                "open_absolute_point_change_vs_none": 0.1,
                "open_published": True,
                "test_modification_rate": 0.25,
            }},
        })
        self.assertIn("未发布", html)
        self.assertNotIn("0.033", html)
        self.assertNotIn("1.000", html)

    def test_incomplete_seeds_are_not_a_full_mean(self):
        summary = seed_summary([None, 0.2], [False, True])
        self.assertFalse(summary["comparison_published"])
        self.assertIsNone(summary["mean"])
        self.assertEqual(summary["planned_seeds"], 2)
        self.assertEqual(summary["valid_seeds"], 1)
        self.assertEqual(summary["diagnostic_valid_subset_mean"], 0.2)
        complete = seed_summary([0.1, 0.3], [True, True])
        self.assertTrue(complete["comparison_published"])
        self.assertAlmostEqual(complete["mean"], 0.2)

    def test_incomplete_candidates_do_not_publish_closed_gain(self):
        scenario = sample_mechanism("zipf", size=300, seed=1)
        originals = scenario["train"] + scenario["validation"] + scenario["test"]
        report = evaluate_scenario(
            scenario, budget=20, candidates=originals, with_markov=False,
            modes=("none", "modern_blocklist", "paper_igr_unique"),
        )
        html = public_comparison_html(report)
        self.assertIn("未发布", html)
        for name in ("modern_blocklist", "paper_igr_unique"):
            arm = report["arms"][name]
            if arm["headline_published"]:
                continue
            self.assertIsNone(arm["absolute_point_change_vs_none"])
            self.assertIsNone(arm["head_users"]["frequency_absolute_point_change"])
            self.assertIsNone(arm["paired_vs_none"]["frequency"]["point"])
            self.assertIsNotNone(arm["withheld_reason"])
        self.assertTrue(any(not report["arms"][name]["headline_published"] for name in ("modern_blocklist", "paper_igr_unique")))


if __name__ == "__main__":
    unittest.main()
