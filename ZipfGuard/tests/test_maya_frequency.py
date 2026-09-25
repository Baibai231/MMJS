import json
import tempfile
import unittest
from pathlib import Path

from data.maya_catalog import dataset_card, dataset_names
from experiments.audit_maya_frequency import audit_file


class MayaFrequencyTests(unittest.TestCase):
    def test_duplicate_lines_become_frequencies_without_plaintext(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tiny.txt"
            path.write_text("cedar\ncedar\ncedar\nmaple\nmaple\nbirch\n", encoding="utf-8")
            payload = audit_file(path, dataset_name="hak5", min_frequency_exclusive=0)
        rendered = json.dumps(payload)
        self.assertNotIn("cedar", rendered)
        self.assertNotIn("maple", rendered)
        self.assertNotIn("birch", rendered)
        self.assertEqual(payload["audit"]["occurrence_total"], 6)
        self.assertEqual(payload["audit"]["unique_types"], 3)
        self.assertEqual(payload["audit"]["top1_count"], 3)
        self.assertEqual(payload["dataset"]["language"], "en")
        self.assertEqual(len(dataset_names()), 19)
        self.assertIn("rockyou", dataset_names())
        self.assertFalse(payload["method_weights_refit"])
        self.assertGreaterEqual(payload["fit"]["cutoff_rank"], 1)

    def test_unknown_dataset_name_is_rejected(self):
        with self.assertRaises(KeyError):
            dataset_card("not-a-maya-set")


if __name__ == "__main__":
    unittest.main()
