import hashlib
import json
import pickle
import tempfile
import unittest
from pathlib import Path

from core.htpg_features import HTPGFeatureExtractor
from core.occurrence_frequency import load_occurrence_counter
from experiments.external_maya_validation import _load_cache, _one_site, _save_cache
from experiments.provenance import robustness_manifest
from experiments.research19_manifest import ANALYSIS_SOURCES, cache_key_from_parts, historical_report_status


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
        self.assertIn("core/counted_corpus.py", ANALYSIS_SOURCES)
        self.assertIn("experiments/research19_manifest.py", ANALYSIS_SOURCES)

    def test_trailing_line_ending_is_a_terminator_and_internal_controls_stay_out(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rows.pkl"
            path.write_bytes(pickle.dumps(["ab", "ab\n", "ab", "a\nb\n", "a\tb\n"]))
            meta, counts = load_occurrence_counter(path)
        self.assertEqual(counts["ab"], 3)
        self.assertNotIn("a\nb", counts)
        self.assertNotIn("a\tb", counts)
        self.assertEqual(meta["trailing_lf_removed"], 3)
        self.assertEqual(meta["rows_without_record_terminator"], 2)
        self.assertEqual(meta["rows_excluded_internal_controls"], 2)
        self.assertEqual(meta["preprocess_version"], "maya-pickle-record-terminator-v1")

    def test_seven_small_maya_sites_keep_trailing_line_endings_as_terminators(self):
        expected = {
            "hak5": (2984, 0),
            "hotmail": (9813, 0),
            "myspace": (41545, 0),
            "faithwriters": (9709, 0),
            "singles": (16248, 0),
            "twitter": (39518, 31),
            "phpbb": (255420, 0),
        }
        root = Path(__file__).resolve().parents[1]
        for name, (rows, internal) in expected.items():
            payload = next((root / "local_datasets" / "maya" / name / "extracted").rglob("*.pickle"))
            meta, counts = load_occurrence_counter(payload)
            self.assertEqual(meta["rows_read"], rows, name)
            self.assertEqual(meta["trailing_lf_removed"], rows, name)
            self.assertEqual(meta["rows_excluded_internal_controls"], internal, name)
            self.assertGreaterEqual(meta["occurrence_total"], rows - internal, name)
            self.assertGreater(meta["unique_types"], 2, name)
            counts.clear()

    def test_real_site_cache_misses_when_the_lexicon_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            extracted = root / "hak5" / "extracted"
            extracted.mkdir(parents=True)
            payload = extracted / "hak5.pickle"
            rows = ["alpha"] * 10 + ["beta"] * 8 + ["gamma"] * 6 + ["delta"] * 5
            payload.write_bytes(pickle.dumps(rows))
            digest = hashlib.sha256(payload.read_bytes()).hexdigest()
            ready = {
                "payload_name": "hak5.pickle",
                "payload_sha256": digest,
                "payload_bytes": payload.stat().st_size,
                "archive_sha256": "0" * 64,
                "archive_bytes": 1,
            }
            (root / "hak5" / "ready.json").write_text(json.dumps(ready), encoding="utf-8")

            def extractor(term: str) -> HTPGFeatureExtractor:
                path = root / f"{term}.json"
                path.write_text(json.dumps({
                    "schema_version": 1,
                    "profile_id": term,
                    "word_type": {"terms": [term]},
                    "lastname": {"terms": ["zzzz"]},
                }), encoding="utf-8")
                return HTPGFeatureExtractor.from_profile(path)

            first = _one_site(
                "hak5", root=root, extractor=extractor("alpha"), max_bytes=10**9, max_feature_types=100,
            )
            second = _one_site(
                "hak5", root=root, extractor=extractor("nomatch"), max_bytes=10**9, max_feature_types=100,
            )
            third = _one_site(
                "hak5", root=root, extractor=extractor("nomatch"), max_bytes=10**9, max_feature_types=100,
            )
        self.assertFalse(first["analysis_reused"])
        self.assertFalse(second["analysis_reused"])
        self.assertTrue(third["analysis_reused"])
        self.assertNotEqual(first["lexicon_sha256"], second["lexicon_sha256"])


if __name__ == "__main__":
    unittest.main()
