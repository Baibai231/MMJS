import json
import tempfile
import unittest
from pathlib import Path

from tools.explore_catalog_size import coverage_order, describe_set, frontier, gap, read_rows


def row(key, collision, cost, history="off"):
    return {"profile_id": key, "collision_probability": collision,
            "modification_rate": cost, "profile": {
                "min_length": 8, "required_classes": 0,
                "development_top_blocklist": 0,
                "historical_hotspot_blocklist": history, "deny_features": []}}


class ExplorationTests(unittest.TestCase):
    def test_partial_append_is_ignored_but_duplicate_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shard_0.jsonl"
            path.write_bytes(b'{"profile_id":"P1"}\n{"profile_id":')
            rows, _ = read_rows(Path(tmp), "*.jsonl")
            self.assertEqual(set(rows), {"P1"})
            path.write_text('{"profile_id":"P1"}\n{"profile_id":"P1"}\n')
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                read_rows(Path(tmp), "*.jsonl")

    def test_coverage_requires_one_joint_representative(self):
        metrics = ["collision_probability", "modification_rate"]
        target = row("target", 1e-7, .2)
        choices = [row("a", 0, .8), row("b", 9e-7, .1)]
        # Neither the distribution winner nor the cost winner covers the target.
        self.assertGreater(min(gap(c, target, metrics) for c in choices), 1)

    def test_equal_scores_keep_distinct_history_templates(self):
        rows = [row("a", 0, .5), row("b", 0, .5, "on")]
        self.assertEqual(len(frontier(rows, ["collision_probability", "modification_rate"])), 2)
        ordered, _ = coverage_order(rows, ["collision_probability", "modification_rate"])
        self.assertEqual({r["profile_id"] for r in ordered}, {"a", "b"})

    def test_nested_coverage_is_monotone_and_full_pool_exact(self):
        metrics = ["collision_probability", "modification_rate"]
        rows = [row("a", 0, .8), row("b", 3e-7, .2), row("c", 1e-7, .5)]
        ordered, _ = coverage_order(rows, metrics)
        gaps = [describe_set(ordered[:k], rows, metrics)["max_gap_units"] for k in range(1, 4)]
        self.assertEqual(gaps, sorted(gaps, reverse=True))
        self.assertEqual(gaps[-1], 0)
        self.assertEqual([r["profile_id"] for r in coverage_order(rows[::-1], metrics)[0]],
                         [r["profile_id"] for r in ordered])


if __name__ == "__main__":
    unittest.main()
