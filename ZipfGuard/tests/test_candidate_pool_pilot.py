"""The pilot must never publish a Top10 from incomplete or duplicate shards."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.summarize_candidate_pool_pilot import load_complete
from web.candidate_pool import candidate_pool_asset, candidate_pool_snapshot


class CandidatePoolCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.audit = {'structural_paths': 2023, 'feasible_complete_paths': 2,
                      'cost_excluded_complete_paths': 2021}
        self.write('registration_audit.json', self.audit)
        self.paths = [['S01'] * 10, ['S01'] * 9 + ['S07']]
        self.write_rows('registered_paths.jsonl', [
            {'path': path, 'registered_users': 100000} for path in self.paths])

    def write(self, name, data):
        (self.directory / name).write_text(json.dumps(data), encoding='utf-8')

    def write_rows(self, name, rows):
        (self.directory / name).write_text(
            ''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')

    def attack(self, index, *, complete=True):
        return {
            'path': self.paths[index],
            'A1_minauto': [{'budget': budget, 'rate': .1 if complete else None,
                            'complete': complete, 'target_weight': 100000}
                           for budget in [100, 1000, 10000, 100000, 1000000]],
            'models': [{'name': name, 'stop_reason': 'reached_budget' if complete else 'timeout'}
                       for name in ['frequency', 'dictionary-rules', 'character-ngram']],
        }

    def test_resume_and_disjoint_shards_form_complete_population(self):
        self.write_rows('attacked_paths.jsonl', [self.attack(0)])
        self.write_rows('attacked_paths_shard_01.jsonl', [self.attack(1)])
        audit, rows, incomplete = load_complete(self.directory)
        self.assertEqual((len(rows), incomplete), (2, 0))
        self.assertEqual(audit, self.audit)

    def test_missing_path_cannot_publish(self):
        self.write_rows('attacked_paths.jsonl', [self.attack(0)])
        with self.assertRaisesRegex(RuntimeError, 'missing or duplicated'):
            load_complete(self.directory)
        self.assertEqual(candidate_pool_snapshot(self.directory)['status'], 'evaluating')

    def test_timeout_cannot_publish_even_when_all_paths_have_records(self):
        self.write_rows('attacked_paths.jsonl', [self.attack(0), self.attack(1, complete=False)])
        with self.assertRaisesRegex(RuntimeError, 'Incomplete attacks remain'):
            load_complete(self.directory)
        self.assertEqual(candidate_pool_snapshot(self.directory)['evaluated_paths'], 1)

    def test_duplicate_checkpoint_is_rejected(self):
        self.write_rows('attacked_paths.jsonl', [self.attack(0), self.attack(1)])
        self.write_rows('attacked_paths_shard_00.jsonl', [self.attack(0)])
        with self.assertRaisesRegex(RuntimeError, 'missing or duplicated'):
            load_complete(self.directory)
        self.assertEqual(candidate_pool_snapshot(self.directory)['status'], 'invalid')

    def test_duplicate_registered_path_is_rejected(self):
        self.write_rows('registered_paths.jsonl', [{'path': self.paths[0]}] * 2)
        self.write_rows('attacked_paths.jsonl', [self.attack(0)])
        with self.assertRaisesRegex(RuntimeError, 'Registered path count'):
            load_complete(self.directory)

    def test_in_progress_write_does_not_break_progress(self):
        self.write_rows('attacked_paths.jsonl', [self.attack(0)])
        (self.directory / 'attacked_paths_shard_00.jsonl').write_text('{"path":', encoding='utf-8')
        self.assertEqual(candidate_pool_snapshot(self.directory)['evaluated_paths'], 1)

    def test_changed_population_or_models_is_rejected(self):
        first = self.attack(0)
        first['A1_minauto'][-1]['target_weight'] = 99999
        self.write_rows('attacked_paths.jsonl', [first, self.attack(1)])
        with self.assertRaisesRegex(RuntimeError, 'target population changed'):
            load_complete(self.directory)

    def test_only_public_assets_can_be_served(self):
        self.write('top10_summary.json', {})
        for name in ['../manifest.json', 'registered_paths.jsonl', 'attacked_paths.jsonl']:
            with self.assertRaises(FileNotFoundError):
                candidate_pool_asset(name, self.directory)


if __name__ == '__main__':
    unittest.main()
