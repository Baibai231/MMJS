import json
import tempfile
import unittest
from pathlib import Path

from experiments.external_maya_validation import _load_cache, _save_cache
from experiments.provenance import robustness_manifest
from experiments.research19_manifest import cache_key_from_parts, historical_report_status


class Research19IdentityTests(unittest.TestCase):
    def test_lexicon_or_code_change_changes_the_cache_key(self):
        base = dict(payload=b"counts", lexicon=b"joy", source=b"fit", config=b"{}", max_feature_types=7)
        original = cache_key_from_parts(**base)
        changed_lexicon = cache_key_from_parts(**{**base, "lexicon": b"joy-li"})
        changed_code = cache_key_from_parts(**{**base, "source": b"fit-v2"})
        self.assertNotEqual(original, changed_lexicon)
        self.assertNotEqual(original, changed_code)

    def test_saved_cache_misses_when_the_live_identity_differs(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "aggregate.json"
            _save_cache(path, "payload-sha", 7, {"status": "ok"})
            self.assertIsNotNone(_load_cache(path, "payload-sha", 7))
            cached = json.loads(path.read_text(encoding="utf-8"))
            cached["lexicon_sha256"] = "0" * 64
            cached["cache_key_sha256"] = "1" * 64
            path.write_text(json.dumps(cached), encoding="utf-8")
            self.assertIsNone(_load_cache(path, "payload-sha", 7))

    def test_old_protocol_is_not_a_current_method_result(self):
        status = historical_report_status("robustness-v2")
        self.assertTrue(status["historical"])
        self.assertFalse(status["may_display_as_current_method_result"])
        self.assertIn("历史", status["warning"])

    def test_manifest_covers_evaluation_code_and_the_lexicon(self):
        manifest = robustness_manifest(budget=17, seeds=[7], sizes=[300])
        self.assertIn("experiments/evaluation_validity.py", manifest["source_files"])
        self.assertIn("resources/htpg_reference_v1.json", manifest["source_files"])
        self.assertNotIn("stem00", json.dumps(manifest["source_files"]))


if __name__ == "__main__":
    unittest.main()
