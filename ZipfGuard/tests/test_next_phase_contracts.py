import json
import unittest
from pathlib import Path

from ai.omen_adapter import omen_status
from experiments.htpg_optimization import feature_programs, select_under_cost
from experiments.paper_scenarios import assert_not_renamed, reproduction_plan


ROOT = Path(__file__).resolve().parents[1]


class NextPhaseContractTests(unittest.TestCase):
    def test_omen_is_not_integrated_and_emits_no_guesses(self):
        status = omen_status()
        rendered = json.dumps(status)
        self.assertFalse(status["integrated"])
        self.assertEqual(status["guesses_emitted"], 0)
        self.assertFalse(status["markov_substitute_is_omen"])
        self.assertFalse(status["paper_budget_1e8_completed"])
        self.assertNotIn("password", rendered.lower())

    def test_paper_plan_stays_incomplete_and_rejects_renames(self):
        plan = reproduction_plan()
        self.assertTrue(plan["blocked"])
        self.assertEqual(plan["experiments"]["E4"]["cracked_rate"], None)
        self.assertIn("178", plan["missing_sites"])
        self.assertIn("CSDN", plan["missing_sites"])
        self.assertIn("RenRen", plan["missing_sites"])
        with self.assertRaises(ValueError):
            assert_not_renamed("178", "taobao")
        self.assertFalse(plan["plaintext_retained"])

    def test_cost_cap_can_reject_every_edit_and_keeps_the_record(self):
        rows = [
            {"features": [], "validation_risk": 0.40, "modification_rate": 0.0},
            {"features": ["length"], "validation_risk": 0.20, "modification_rate": 0.30},
            {"features": ["length", "lsd_structure"], "validation_risk": 0.10, "modification_rate": 0.50},
        ]
        chosen = select_under_cost(rows, 0.05)
        self.assertEqual(chosen["features"], [])
        self.assertEqual(chosen["validation_risk"], 0.40)
        self.assertFalse(chosen["hypothesis_established"])
        self.assertEqual(len(chosen["rejected"]), 2)
        unreachable = select_under_cost(rows[1:], 0.05)
        self.assertEqual(unreachable["status"], "unreachable")
        programs = feature_programs()
        self.assertIn((), programs)
        self.assertIn(("length",), programs)
        self.assertTrue(all(len(item) <= 2 for item in programs))

    def test_required_documents_exist(self):
        for relative in (
            "docs/PAPER_REPRODUCTION_MATRIX.md",
            "docs/PAPER_AMBIGUITIES.md",
            "docs/CORPUS_INVENTORY.md",
            "docs/references.bib",
            "docs/EXPERIMENT_PROTOCOL.md",
            "docs/PERFORMANCE_NOTES.md",
            "docs/EXTERNAL_VALIDATION_PLAN.md",
            "docs/PHASE_DECISION.md",
            "configs/paper_reproduction/sites.json",
            "configs/optimization/method.json",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)


if __name__ == "__main__":
    unittest.main()
