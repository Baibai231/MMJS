import json
import tempfile
import unittest
from pathlib import Path

from core.attackers import FrequencyAttacker
from experiments.htpg_iteration import iterate
from experiments.response_sensitivity import sensitivity
from experiments.robustness_protocol import evaluate_scenario, sample_mechanism, support_strings


class RoadmapRemainderTests(unittest.TestCase):
    def test_summary_hides_guesses_unless_requested(self):
        ranking = FrequencyAttacker().fit_select_rank(
            ["cedar2024", "cedar2024", "maple123"],
            ["maple123"],
            ["cedar2024", "maple123"],
        )
        hidden = ranking.summary()
        self.assertNotIn("cedar2024", json.dumps(hidden))
        self.assertNotIn("top_guesses", hidden)
        self.assertTrue(hidden["top_guesses_withheld"])
        shown = ranking.summary(reveal_guesses=True)
        self.assertIn("cedar2024", shown["top_guesses"])

    def test_iteration_keeps_every_user(self):
        report = iterate(size=300, seed=3, rounds=(1, 2), budget=20)
        rendered = json.dumps(report)
        self.assertNotIn("stem00", rendered)
        self.assertTrue(report["user_count_constant"])
        self.assertEqual(report["users_deleted_for_refusal"], 0)
        self.assertFalse(report["measured_adoption"])
        self.assertEqual([row["round"] for row in report["rounds"]], [1, 2])
        self.assertTrue(all(row["users"] == 300 for row in report["rounds"]))

    def test_response_rates_are_assumptions_and_nist_arms_exist(self):
        report = sensitivity(size=300, seed=3, budget=20)
        self.assertFalse(report["measured_memory"])
        self.assertFalse(report["rows"]["optimistic"]["measured_user_study"])
        self.assertGreaterEqual(report["rows"]["optimistic"]["initial_acceptance_rate"], report["rows"]["conservative"]["initial_acceptance_rate"])
        self.assertLessEqual(report["rows"]["optimistic"]["mean_attempts"], 2)
        self.assertGreater(report["rows"]["optimistic"]["adoption_assumption"], report["rows"]["conservative"]["adoption_assumption"])
        self.assertNotIn("80.23", json.dumps(report["rows"]))
        compared = evaluate_scenario(sample_mechanism("zipf", size=300, seed=3), budget=20, with_markov=False)
        self.assertIn("nist_single_factor_15", compared["arms"])
        self.assertIn("nist_mfa_8", compared["arms"])
        self.assertEqual(compared["threat"]["online_login_budget"], None)
        self.assertTrue(compared["threat"]["budget_is_not_login_attempts"])
        paired = compared["arms"]["budget_cost"]["paired_vs_none"]["frequency"]
        self.assertEqual(paired["n"], compared["test_users"])
        self.assertLessEqual(paired["ci95"][0], paired["point"])
        engineering = compared["engineering"]
        self.assertGreaterEqual(engineering["scan_seconds"], 0)
        self.assertGreaterEqual(engineering["recommendation_seconds"], 0)
        self.assertGreaterEqual(engineering["attack_seconds"], 0)
        self.assertEqual(compared["arms"]["paper_igr_unique"]["weighting"], "unique")
        self.assertEqual(compared["arms"]["budget_cost"]["weighting"], "frequency")
        self.assertIn("outside_candidate_rate", compared["arms"]["paper_igr_unique"])
        self.assertLessEqual(compared["head_test_users"], compared["test_users"])
        self.assertIn("frequency_absolute_point_change", compared["arms"]["budget_cost"]["head_users"])
        self.assertNotEqual(
            compared["arms"]["budget_cost"]["all_users"]["denominator"],
            compared["arms"]["budget_cost"]["head_users"]["denominator"],
        )

    def test_shifted_draw_and_per_password_arm(self):
        scenario = sample_mechanism("shifted", size=300, seed=4)
        self.assertEqual(scenario["train_exponent"], 0.45)
        self.assertEqual(scenario["test_exponent"], 1.35)
        self.assertEqual(len(scenario["train"]) + len(scenario["validation"]) + len(scenario["test"]), 300)
        report = evaluate_scenario(
            scenario, budget=20, with_markov=False, modes=("none", "budget_cost", "per_password"),
        )
        self.assertFalse(report["main_budget_decided_by_576"])
        self.assertIn("per_password", report["arms"])
        self.assertNotIn("stem00", json.dumps(report))
        head = support_strings("head_shift")[:24]
        shifted_head = sample_mechanism("head_shift", size=300, seed=4)
        self.assertGreater(shifted_head["unknown_test_count"], 0)
        self.assertTrue(set(shifted_head["test"]).isdisjoint(head))

    def test_public_table_keeps_closed_open_and_head_apart(self):
        from experiments.robustness_protocol import public_comparison_html
        report = {
            "support_size": 652, "candidate_count": 1000, "budget": 40, "test_users": 60,
            "arms": {"budget_cost": {
                "absolute_point_change_vs_none": 0.1,
                "open_absolute_point_change_vs_none": 0.02,
                "head_users": {"frequency_absolute_point_change": 0.4},
                "test_modification_rate": 0.2,
            }},
        }
        html = public_comparison_html(report)
        self.assertIn("全体封闭", html)
        self.assertIn("全体开放", html)
        self.assertIn("头部频次", html)
        self.assertIn("挑最高的一行会夸大收益", html)
        self.assertNotIn("stem00", html)

    def test_robustness_manifest_hashes_sources_without_passwords(self):
        from experiments.provenance import robustness_manifest
        manifest = robustness_manifest()
        rendered = json.dumps(manifest)
        self.assertEqual(len(manifest["source_sha256"]), 64)
        self.assertIn("experiments/robustness_protocol.py", manifest["source_files"])
        self.assertIn("experiments/suggestion_compare.py", manifest["source_files"])
        self.assertEqual(manifest["budget"], 40)
        self.assertEqual(manifest["passllm"], "not_participating")
        self.assertNotIn("stem00", rendered)
        self.assertNotIn("password", rendered)

    def test_grammar_ceiling_marks_budgets_that_exhaust_the_old_space(self):
        from experiments.pipeline import grammar_ceiling
        ceiling = grammar_ceiling([100, 1000], 576)
        self.assertEqual(ceiling["budgets_at_or_above_grammar_size"], [1000])
        self.assertTrue(ceiling["saturated_by_grammar_ceiling"])
        self.assertIn("不能当作防御结论", ceiling["saturation_note"])
        self.assertFalse(grammar_ceiling([40], 652)["saturated_by_grammar_ceiling"])

    def test_corpus_overlap_reports_hash_not_plaintext(self):
        import io
        import contextlib
        from tools.scan_corpus_overlap import artifact_strings, scan_corpus
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "report.json").write_text(
                json.dumps({"note": "cedar-token", "feature": "length"}), encoding="utf-8",
            )
            corpus = root / "counts.txt"
            corpus.write_bytes(b"3 cedar-token\n1 other-token\n")
            hits = scan_corpus(corpus, artifact_strings(root))
        rendered = "\n".join(hits)
        self.assertIn("report.json", rendered)
        self.assertNotIn("cedar-token", rendered)
        self.assertTrue(rendered.startswith("report.json") or "sha256=" in rendered)


if __name__ == "__main__":
    unittest.main()
