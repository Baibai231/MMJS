import json
import tempfile
import unittest
from pathlib import Path

from ai.pcfg_adapter import split_closed_and_open
from core.counted_corpus import CorpusFormatError, ensure_not_lfs_pointer
from core.htpg_features import HTPGFeatureExtractor, lsd_structure
from core.htpg_fit import curvature_maximum, fit_pdf_zipf
from core.htpg_igr import IGRAccumulator
from core.metrics import evaluate_open_generation, evaluate_ranking
from core.rockyou import aggregate_rockyou_withcount
from policy.htpg_generator import password_digest, suggest_for_password


class OpenGenerationTests(unittest.TestCase):
    def test_two_outside_guesses_leave_target_at_rank_three(self):
        stream = ("outside-a", "outside-b", "target")
        guesses, positions = split_closed_and_open(stream, {"target"})
        self.assertEqual(guesses, ("target",))
        self.assertEqual(positions, (3,))
        opened = evaluate_open_generation(stream, ["target"], budgets=(1, 3))
        by_budget = {point["budget"]: point for point in opened["points"]}
        self.assertEqual(by_budget[1]["cracked"], 0)
        self.assertEqual(by_budget[3]["cracked"], 1)
        closed = evaluate_ranking(["target"], ["target"], budgets=(1,))
        self.assertEqual(closed["points"][0]["cracked"], 1)
        preserved = evaluate_ranking(guesses, ["target"], budgets=(1, 3), positions=positions)
        preserved_points = {point["budget"]: point for point in preserved["points"]}
        self.assertEqual(preserved_points[1]["cracked"], 0)
        self.assertEqual(preserved_points[3]["cracked"], 1)


class CountedCorpusTests(unittest.TestCase):
    def test_lfs_pointer_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rockyou-withcount.txt"
            path.write_text(
                "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 1\n",
                encoding="utf-8",
            )
            with self.assertRaises(CorpusFormatError):
                ensure_not_lfs_pointer(path)

    def test_frequency_file_keeps_counts_and_drops_plaintext(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "counts.txt"
            path.write_bytes(b" 5 secret-token\n3 other-token\n1 tail-token\n")
            payload = aggregate_rockyou_withcount(path, top_k=2)
        rendered = json.dumps(payload)
        self.assertNotIn("secret-token", rendered)
        self.assertEqual(payload["items"][0]["count"], 5)
        self.assertEqual(payload["metadata"]["observed_frequency_total"], 9)
        self.assertEqual(payload["metadata"]["retained_frequency_total"], 8)
        self.assertAlmostEqual(payload["metadata"]["truncated_mass"], 1 / 9)


class HTPGMethodTests(unittest.TestCase):
    def test_paper_lsd_example_and_curvature_formula(self):
        self.assertEqual(lsd_structure("123456!AA"), "D6S1L2")
        x0 = curvature_maximum(829712.58, 0.913745)
        self.assertGreater(x0, 1171)
        self.assertLess(x0, 1173)

    def test_fit_uses_floor_of_curvature_and_is_not_a_fixed_rank(self):
        steep = fit_pdf_zipf([100, 40, 20, 10, 6, 4, 1])
        flat = fit_pdf_zipf([9, 8, 7, 6, 5, 4])
        self.assertEqual(steep["cutoff_rank"], int(steep["curvature_x0"]))
        self.assertNotEqual(steep["cutoff_rank"], flat["cutoff_rank"])
        self.assertNotEqual(steep["cutoff_rank"], 1171)
        self.assertEqual(round(0.913754, 3), 0.914)

    def test_igr_perfect_split_and_zero_iv(self):
        accumulator = IGRAccumulator()
        rows = [
            ({"capital": True}, True),
            ({"capital": True}, True),
            ({"capital": False}, False),
            ({"capital": False}, False),
        ]
        shared = {
            "lsd_structure": "L3", "length": 3, "date": False, "keyboard": False,
            "specplace": "none", "lowercase": False, "word_type": False, "lastname": False,
        }
        for features, is_head in rows:
            accumulator.add({**shared, **features}, is_head=is_head, frequency=2)
        report = accumulator.scores()
        by_name = {row["feature"]: row for row in report["features"]}
        self.assertAlmostEqual(by_name["capital"]["igr_unique"], 1)
        self.assertIsNone(by_name["length"]["igr_unique"])
        self.assertEqual(by_name["length"]["status_unique"], "iv_zero")
        self.assertEqual(report["primary_weighting"], "unique_equal")
        self.assertEqual(by_name["capital"]["rank_unique"], 1)

    def test_suggestions_only_for_head_and_follow_igr(self):
        extractor = HTPGFeatureExtractor(["joy"], ["smith"])
        accumulator = IGRAccumulator()
        labeled = [("abc", True), ("abd", True), ("abcdefghij", False), ("abcdefghiK", False)]
        for password, is_head in labeled:
            accumulator.add(
                extractor.extract(password).to_dict(), is_head=is_head, frequency=1,
            )
        report = accumulator.scores()
        head = {password_digest(password) for password, is_head in labeled if is_head}
        head_result = suggest_for_password(
            "abc", head_digests=head, igr_report=report, extractor=extractor,
        )
        tail_result = suggest_for_password(
            "abcdefghij", head_digests=head, igr_report=report, extractor=extractor,
        )
        unseen = suggest_for_password(
            "river-otter-lotus", head_digests=head, igr_report=report, extractor=extractor,
        )
        self.assertTrue(head_result["in_head_set"])
        self.assertTrue(any(item["feature"] == "length" for item in head_result["suggestions"]))
        self.assertEqual(tail_result["suggestions"], [])
        self.assertEqual(unseen["reason"], "tail_or_unseen")
        self.assertNotIn("abc", json.dumps(tail_result))

    def test_capital_direction_follows_the_tail(self):
        extractor = HTPGFeatureExtractor(["joy"], ["smith"])
        accumulator = IGRAccumulator()
        labeled = [("abc", True), ("abd", True), ("Abc", False), ("Abd", False)]
        for password, is_head in labeled:
            accumulator.add(extractor.extract(password).to_dict(), is_head=is_head, frequency=1)
        report = accumulator.scores()
        head = {password_digest(password) for password, is_head in labeled if is_head}
        result = suggest_for_password("abc", head_digests=head, igr_report=report, extractor=extractor)
        actions = {item["feature"]: item["action"] for item in result["suggestions"]}
        self.assertEqual(actions["capital"], "use_capital")
        opposite = IGRAccumulator()
        flipped = [("Abc", True), ("Abd", True), ("abc", False), ("abd", False)]
        for password, is_head in flipped:
            opposite.add(extractor.extract(password).to_dict(), is_head=is_head, frequency=1)
        opposite_head = {password_digest(password) for password, is_head in flipped if is_head}
        avoided = suggest_for_password(
            "Abc", head_digests=opposite_head, igr_report=opposite.scores(), extractor=extractor,
        )
        avoided_actions = {item["feature"]: item["action"] for item in avoided["suggestions"]}
        self.assertEqual(avoided_actions["capital"], "avoid_capital")

    def test_paper_tie_rule_includes_equal_distance_and_blocks_shorter_length(self):
        extractor = HTPGFeatureExtractor(["joy"], ["smith"])
        report = {"features": [
            {
                "feature": "length", "status_unique": "ok", "rank_unique": 1, "igr_unique": 0.4,
                "head_summary_unique": {"mean": 4}, "tail_summary_unique": {"mean": 8},
            },
            {
                "feature": "lsd_structure", "status_unique": "ok", "rank_unique": 2, "igr_unique": 0.2,
                "head_summary_unique": {"mode": "L3"}, "tail_summary_unique": {"mode": "L8"},
            },
        ]}
        head = {password_digest("abcdef")}
        strict = suggest_for_password(
            "abcdef", head_digests=head, igr_report=report, extractor=extractor, tie_rule="experimental_strict",
        )
        paper = suggest_for_password(
            "abcdef", head_digests=head, igr_report=report, extractor=extractor, tie_rule="paper_compatible",
        )
        self.assertEqual(strict["suggestions"], [])
        self.assertEqual({item["feature"] for item in paper["suggestions"]}, {"length", "lsd_structure"})
        shorter = {"features": [{
            "feature": "length", "status_unique": "ok", "rank_unique": 1, "igr_unique": 0.4,
            "head_summary_unique": {"mean": 10}, "tail_summary_unique": {"mean": 4},
        }]}
        blocked = suggest_for_password(
            "abcdefghij", head_digests={password_digest("abcdefghij")}, igr_report=shorter,
            extractor=extractor, tie_rule="paper_compatible",
        )
        self.assertEqual(blocked["suggestions"], [])

    def test_directed_edits_meet_their_targets(self):
        from experiments.suggestion_compare import edit_meets_target, execute_suggestion
        extractor = HTPGFeatureExtractor(["joy"], ["smith"])
        broken = [
            ("abc123", "abc123!", "lsd_structure", "change_lsd_toward_tail_mode", "tail mode L8"),
            ("123abc", "123abc", "capital", "use_capital", None),
            ("abc2024xyz", "abc2024xyz", "date", "avoid_date", None),
        ]
        for before, after, feature, action, target in broken:
            self.assertFalse(edit_meets_target(before, after, action, target, extractor, feature))
        lsd = execute_suggestion("abc123", "lsd_structure", "change_lsd_toward_tail_mode", "tail mode L8", extractor)
        capital = execute_suggestion("123abc", "capital", "use_capital", None, extractor)
        dated = execute_suggestion("abc2024xyz", "date", "avoid_date", None, extractor)
        self.assertEqual(lsd["status"], "success")
        self.assertEqual(extractor.extract(lsd["password"]).lsd_structure, "L8")
        self.assertEqual(capital["status"], "success")
        self.assertTrue(extractor.extract(capital["password"]).capital)
        self.assertNotEqual(capital["password"], "123abc")
        self.assertEqual(dated["status"], "success")
        self.assertFalse(extractor.extract(dated["password"]).date)
        empty = execute_suggestion("2024", "date", "avoid_date", None, extractor)
        self.assertEqual(empty["status"], "unable")
        self.assertEqual(empty["password"], "2024")
        surname = execute_suggestion("absmith", "lastname", "avoid_lastname", None, extractor)
        plain = execute_suggestion("ab!", "specplace", "move_special_placement_toward_tail_mode", "tail mode none", extractor)
        self.assertEqual(surname["status"], "success")
        self.assertFalse(extractor.extract(surname["password"]).lastname)
        self.assertEqual(plain["status"], "success")
        self.assertEqual(extractor.extract(plain["password"]).specplace, "none")
        separated = execute_suggestion("abc2024-01-15xyz", "date", "avoid_date", None, extractor)
        digits = execute_suggestion("ab19990101cd", "date", "avoid_date", None, extractor)
        middle = execute_suggestion("ab", "specplace", "move_special_placement_toward_tail_mode", "tail mode middle", extractor)
        repeated = execute_suggestion("absmithsmithcd", "lastname", "avoid_lastname", None, extractor)
        only_surname = execute_suggestion("smithsmith", "lastname", "avoid_lastname", None, extractor)
        walked = execute_suggestion("xxqwerxx", "keyboard", "avoid_keyboard_walk", None, extractor)
        for result, feature, predicate in (
            (separated, "date", lambda vector: vector.date is False),
            (digits, "date", lambda vector: vector.date is False),
            (middle, "specplace", lambda vector: vector.specplace == "middle"),
            (repeated, "lastname", lambda vector: vector.lastname is False),
            (walked, "keyboard", lambda vector: vector.keyboard is False),
        ):
            self.assertEqual(result["status"], "success", feature)
            self.assertTrue(predicate(extractor.extract(result["password"])), feature)
        self.assertEqual(only_surname["status"], "unable")
        self.assertEqual(only_surname["password"], "smithsmith")

    def test_later_edit_that_breaks_an_earlier_target_is_conflict(self):
        from experiments.suggestion_compare import EXECUTION_STATUSES, execute_suggestion
        self.assertEqual(EXECUTION_STATUSES, ("success", "already_satisfied", "unable", "conflict"))
        extractor = HTPGFeatureExtractor(["joy"], ["smith"])
        first = execute_suggestion("abc123", "lsd_structure", "change_lsd_toward_tail_mode", "tail mode L8", extractor)
        held = (("lsd_structure", "change_lsd_toward_tail_mode", "tail mode L8"),)
        shortened = execute_suggestion(first["password"], "length", "decrease_length", "tail mean 4", extractor, prior=held)
        self.assertEqual(shortened["status"], "conflict")
        self.assertEqual(shortened["password"], first["password"])
        self.assertEqual(extractor.extract(shortened["password"]).lsd_structure, "L8")
        self.assertEqual(shortened["broken_features"], ["lsd_structure"])
        kept = execute_suggestion(first["password"], "capital", "use_capital", None, extractor, prior=held)
        self.assertEqual(kept["status"], "success")
        self.assertTrue(extractor.extract(kept["password"]).capital)
        self.assertEqual(extractor.extract(kept["password"]).lsd_structure, "L8")

    def test_length_response_is_shared_and_refusal_keeps_the_user(self):
        from experiments.robustness_protocol import _apply_named
        from experiments.suggestion_compare import shared_length_response
        passwords = ["abc"] * 20
        user_ids = [f"user-{index}" for index in range(20)]
        model = {"head_digests": set()}
        deterministic = _apply_named(
            passwords, user_ids, [], model, "modern_blocklist", response_mode="deterministic",
        )
        expected = [
            shared_length_response(password, 14, user_id=user_id, response_mode="deterministic")
            for password, user_id in zip(passwords, user_ids)
        ]
        self.assertEqual(deterministic, expected)
        self.assertEqual(set(deterministic), {"abc" + ("x" * 11)})
        diversified = _apply_named(
            passwords, user_ids, [], model, "modern_blocklist", response_mode="diversified",
        )
        self.assertGreater(len(set(diversified)), 1)
        self.assertLessEqual(len(set(diversified)), 5)
        self.assertTrue(all(len(password) == 14 for password in diversified))
        refused = _apply_named(
            passwords, user_ids, [], model, "modern_blocklist", adoption_rate=0,
        )
        self.assertEqual(refused, passwords)
        self.assertEqual(len(refused), len(passwords))

    def test_execution_trace_uses_only_named_statuses(self):
        from experiments.suggestion_compare import EXECUTION_STATUSES, summarize_execution
        counts = summarize_execution([
            {"status": status} for status in (*EXECUTION_STATUSES, "not_adopted")
        ])
        self.assertEqual(set(counts), {*EXECUTION_STATUSES, "not_adopted"})
        self.assertTrue(all(value == 1 for value in counts.values()))

    def test_unable_does_not_count_as_adoption(self):
        from experiments.suggestion_compare import _modification_rate, apply_edit, execute_suggestion
        extractor = HTPGFeatureExtractor(["joy"], ["smith"])
        unable = execute_suggestion("2024", "date", "avoid_date", None, extractor)
        self.assertEqual(unable["status"], "unable")
        self.assertEqual(unable["password"], "2024")
        self.assertEqual(_modification_rate(["2024", "abc"], [unable["password"], "abc"]), 0.0)
        with self.assertRaises(ValueError):
            apply_edit("123abc", "capital", action="use_capital")
        with self.assertRaises(ValueError):
            apply_edit("abc123", "lsd_structure", action="change_lsd_toward_tail_mode", target="tail mode L8")
        with self.assertRaises(ValueError):
            apply_edit("abc2024xyz", "date", action="avoid_date")

    def test_equal_head_and_tail_emits_no_capital_suggestion(self):
        extractor = HTPGFeatureExtractor(["joy"], ["smith"])
        accumulator = IGRAccumulator()
        labeled = [("abc", True), ("abd", True), ("abe", False), ("abf", False)]
        for password, is_head in labeled:
            accumulator.add(extractor.extract(password).to_dict(), is_head=is_head, frequency=1)
        report = accumulator.scores()
        head = {password_digest("abc"), password_digest("abd")}
        result = suggest_for_password("abc", head_digests=head, igr_report=report, extractor=extractor)
        self.assertNotIn("capital", {item["feature"] for item in result["suggestions"]})


if __name__ == "__main__":
    unittest.main()
