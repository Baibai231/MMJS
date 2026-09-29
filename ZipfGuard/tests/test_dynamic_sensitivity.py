"""Ordering stress tests preserve the sampled users and development roles."""
from __future__ import annotations

import unittest
from collections import Counter

from core.registration import registration_from_counts
from tools.run_dynamic_sensitivity import reorder


class DynamicSensitivityTests(unittest.TestCase):
    def test_reordering_changes_only_registration_timeline(self):
        source = registration_from_counts(
            {'weak': 30, 'long-unique-one': 10, 'long-unique-two': 10},
            users=20, development=20, seed=42, cohort_size=5)
        first = reorder(source, 'weak_first', 42)
        last = reorder(source, 'weak_last', 42)
        original = Counter(word for cohort in source['cohorts'] for word in cohort)
        for case in (first, last):
            self.assertEqual(Counter(word for cohort in case['cohorts']
                                     for word in cohort), original)
            self.assertEqual(case['development'], source['development'])
            self.assertEqual([len(c) for c in case['cohorts']], [5] * 4)
        self.assertNotEqual(first['metadata']['registration_order_sha256'],
                            last['metadata']['registration_order_sha256'])
        self.assertGreaterEqual(
            source['development']['train'][first['cohorts'][0][0]],
            source['development']['train'][last['cohorts'][0][0]])


if __name__ == '__main__':
    unittest.main()
