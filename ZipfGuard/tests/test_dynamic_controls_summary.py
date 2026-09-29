"""Guard against claiming complete cross-seed controls from partial runs."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.summarize_dynamic_controls import ARM_NAMES, summarize


def _report(seed, *, complete=True):
    point = {'budget': 1_000_000, 'complete': complete,
             'all_models_completed': complete, 'rate': 0.1}
    models = [{'run': {'model': name, 'charged_count': charged,
                       'stop_reason': reason}}
              for name, charged, reason in (
                  ('frequency', 100, 'exhausted'),
                  ('dictionary-rules', 1000, 'exhausted'),
                  ('character-ngram', 1_000_000, 'reached_budget'))]
    paired = {'budget': 1_000_000, 'status': 'complete',
              'paired_users': 98_000, 'difference': 0.02,
              'ci95_lower': 0.01, 'ci95_upper': 0.03}
    return {
        'dataset': {'seed': seed, 'registration_occurrences': 100_000,
                    'source_sha256': 'same-source'},
        'config': {'seed': seed, 'controls': {'run': True, 'attack': True}},
        'cohorts': [{'response': {'modified_users': 50_000}}],
        'final_distribution': {'collision_probability': 0.000002},
        'controls': {name: {'modified_users': 52_000,
                            'final': {'collision_probability': 0.000003}}
                     for name in ARM_NAMES},
        'attacks': {'A1': {'minauto': [point], 'models': models},
                    'controls': {name: {'minauto': [point], 'models': models}
                                 for name in ARM_NAMES},
                    'paired_dynamic_minus_control': {
                        name: [paired] for name in ARM_NAMES}},
    }


class DynamicControlsSummaryTests(unittest.TestCase):
    def test_complete_paired_differences_are_kept_per_seed(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = {}
            for seed in (11, 42):
                path = Path(folder) / f'{seed}.json'
                path.write_text(json.dumps(_report(seed)), encoding='utf-8')
                paths[seed] = path
            rows = summarize(paths)['runs']
            self.assertEqual([row['seed'] for row in rows], [11, 42])
            self.assertEqual(rows[0]['controls']['static_selected']
                             ['dynamic_minus_control_percentage_points'], 2)

    def test_incomplete_attack_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '11.json'
            path.write_text(json.dumps(_report(11, complete=False)),
                            encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '未完整完成'):
                summarize({11: path})


if __name__ == '__main__':
    unittest.main()
