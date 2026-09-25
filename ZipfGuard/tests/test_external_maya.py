import json
import pickle
import tempfile
import unittest
import zipfile
from pathlib import Path

from core.counted_corpus import CorpusFormatError
from core.htpg_features import HTPGFeatureExtractor
from core.occurrence_frequency import frequencies_from_occurrences
from core.site_distribution import analyze_occurrence_file
from data.maya_fetch import _extract, reject_webpage, select_payload
from experiments.external_maya_validation import (
    _analyze_protocols,
    assert_export_safe,
    spearman,
)


class _Marker:
    pass


class ExternalMayaTests(unittest.TestCase):
    def test_pickle_sequence_is_counted_without_keeping_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rows.pkl"
            path.write_bytes(pickle.dumps(["cedar", "cedar", "cedar", "maple", "maple", "birch"]))
            payload = frequencies_from_occurrences(path)
        rendered = json.dumps({key: value for key, value in payload.items() if key != "frequencies"})
        self.assertNotIn("cedar", rendered)
        self.assertEqual(payload["format"], "maya_pickle_occurrence")
        self.assertEqual(payload["frequencies"], [3, 2, 1])

    def test_pickle_globals_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rows.pkl"
            path.write_bytes(pickle.dumps(_Marker()))
            with self.assertRaises(CorpusFormatError):
                frequencies_from_occurrences(path)

    def test_webpage_download_is_deleted(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "archive.bin"
            path.write_text("<!DOCTYPE html><html>denied</html>", encoding="utf-8")
            with self.assertRaises(CorpusFormatError):
                reject_webpage(path)
            self.assertFalse(path.exists())

    def test_zip_analysis_keeps_failure_and_drops_plaintext(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "rows.txt"
            source.write_text("cedar\ncedar\ncedar\nmaple\nmaple\nbirch\n", encoding="utf-8")
            archive = root / "archive.bin"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.write(source, "rows.txt")
                handle.writestr("readme.txt", "short")
            extracted = root / "extracted"
            extracted.mkdir()
            _extract(archive, extracted)
            payload = select_payload(extracted)
            result = _analyze_protocols(
                payload, HTPGFeatureExtractor(["joy"], ["smith"]), max_feature_types=400_000,
            )
        rendered = json.dumps(result)
        self.assertNotIn("cedar", rendered)
        self.assertNotIn("maple", rendered)
        self.assertEqual(result["status"], "paper_threshold_failed")
        self.assertTrue(result["sensitivity"]["not_paper_protocol"])
        self.assertEqual(result["sensitivity"]["audit"]["occurrence_total"], 6)
        for row in result["sensitivity"]["features"]["features"]:
            for key in ("head_mode_unique", "tail_mode_unique"):
                if key in row:
                    self.assertLessEqual(len(row[key]), 24)
                    self.assertNotIn("cedar", row[key])
        assert_export_safe({"sites": [result]})

    def test_feature_cap_skips_scores(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rows.txt"
            path.write_text("cedar\ncedar\nmaple\nmaple\nbirch\n", encoding="utf-8")
            result = analyze_occurrence_file(
                path, HTPGFeatureExtractor(["joy"], ["smith"]),
                min_frequency_exclusive=0, max_feature_types=2,
            )
        rendered = json.dumps(result)
        self.assertNotIn("cedar", rendered)
        self.assertEqual(result["feature_status"], "skipped_unique_cap")
        self.assertIsNone(result["features"])

    def test_spearman_and_export_guard(self):
        names = ["a", "b", "c"]
        self.assertEqual(spearman(names, names), 1)
        self.assertEqual(spearman(list(reversed(names)), names), -1)
        with self.assertRaises(RuntimeError):
            assert_export_safe({"frequencies": [1, 2, 3]})


if __name__ == "__main__":
    unittest.main()
