"""Cross-seed publication must reject incomplete million-guess evidence."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.summarize_dynamic_study import summarize


def _report(seed, ngram_count=1_000_000):
    point = {'budget': 1_000_000, 'complete': True,
             'all_models_completed': True, 'rate': 0.1}
    models = [{'run': {'model': name, 'charged_count': count,
                       'stop_reason': reason}} for name, count, reason in (
        ('frequency', 100, 'exhausted'),
        ('dictionary-rules', 1000, 'exhausted'),
        ('character-ngram', ngram_count, 'reached_budget'))]
    return {
        'dataset': {'seed': seed, 'registration_occurrences': 100_000,
                    'source_sha256': 'same-source'},
        'cohorts': [{'cohort_id': i + 1, 'start_user': i * 10_000 + 1,
                     'end_user': (i + 1) * 10_000, 'policy_changed': i == 0,
                     'response': {'completed_users': 9_800,
                                  'modified_users': 5_000,
                                  'modification_rate': 0.5,
                                  'completion_rate': 0.98,
                                  'p90_edit_distance_modified': 10}}
                    for i in range(10)],
        'final_distribution': {'users': 98_000,
                               'collision_probability': 0.000002},
        'baseline_completed_distribution': {'users': 98_000,
                                            'collision_probability': 0.0001},
        'user_strength': {'status_counts': {'exact': 100,
                                            'both_beyond_budget': 99_900}},
        'attacks': {
            'F': {'models': models},
            'A1': {'minauto': [point], 'models': models},
            'baseline_completed_F': {'minauto': [{**point, 'rate': 0.3}]},
            'guess_counts': {
                'baseline_completed_F': {'quantiles': {
                    '0.1': {'guess_count': 1000}}},
                'dynamic_A1': {'quantiles': {
                    '0.1': {'guess_count': 100_000}}},
            },
        },
    }


class DynamicSummaryTests(unittest.TestCase):
    def test_complete_runs_are_described_by_seed(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)
            for seed in (11, 23):
                location = source / f'seed_{seed}'
                location.mkdir()
                (location / 'report.json').write_text(
                    json.dumps(_report(seed)), encoding='utf-8')
            result = summarize(source, [11, 23], 1_000_000)
            self.assertEqual([row['seed'] for row in result['runs']], [11, 23])
            self.assertEqual(result['runs'][0]['cracked_difference_percentage_points'], -20)

    def test_short_ngram_stream_cannot_be_published_as_million_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)
            location = source / 'seed_11'
            location.mkdir()
            (location / 'report.json').write_text(
                json.dumps(_report(11, ngram_count=999_999)), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '未跑满'):
                summarize(source, [11], 1_000_000)


if __name__ == '__main__':
    unittest.main()
