"""Full distributions retain the tail and distinguish unavailable legacy data."""
import unittest
from collections import Counter

from core.uniformity import concentration
from web.registration_distribution import (rank_series, comparison_domains,
                                           render_registration_distribution,
                                           render_modification_cost)


class RegistrationDistributionTests(unittest.TestCase):
    def result(self):
        before = concentration(Counter({'a': 5, 'b': 3, 'c': 1, 'd': 1}), full_curve=True)
        after = concentration([str(i) for i in range(10)], full_curve=True)
        return {'cohorts': [
            {'cohort_id': 1, 'end_user': 10, 'baseline_cumulative': before,
             'cumulative': after, 'response': {'modification_rate': .5}}],
            'controls': {}, 'registration_summary': {'pending_users': 0}}

    def test_full_curve_keeps_all_integer_rank_frequencies(self):
        counts = Counter({'a': 5, 'b': 5, 'c': 3, 'd': 2, 'e': 2, 'f': 1, 'g': 1})
        summary = concentration(counts, curve_limit=2, full_curve=True)
        curve = summary.pop('full_rank_frequency')
        self.assertEqual(summary, concentration(counts, curve_limit=2))
        reconstructed = []
        for (r0, n0), (r1, n1) in zip(curve, curve[1:]):
            reconstructed.extend(n0 + (n1 - n0) * (r - r0) / (r1 - r0)
                                 for r in range(r0, r1))
        reconstructed.append(curve[-1][1])
        self.assertEqual(reconstructed, sorted(counts.values(), reverse=True))
        self.assertEqual(sum(reconstructed), summary['users'])
        self.assertNotIn('a', str(curve))

    def test_full_summary_covers_tail_and_legacy_is_labeled(self):
        result = self.result()
        summary = result['cohorts'][0]['cumulative']
        series, complete = rank_series([('dynamic', summary)])
        self.assertTrue(complete)
        self.assertEqual(series[0][1][-1], [10, 1])
        self.assertEqual(comparison_domains(result, {}), ((1, 10), (1, 10)))
        html = render_registration_distribution(result, {})
        self.assertNotIn('旧报告仅保存', html)
        summary.pop('full_rank_frequency')
        self.assertIn('旧报告仅保存', render_registration_distribution(result, {}))

    def test_pending_is_an_exception_notice_not_a_zero_curve(self):
        result = self.result()
        html = render_modification_cost(result)
        self.assertNotIn('待处理', html)
        self.assertEqual(html.count('type="checkbox"'), 2)
        result['registration_summary']['pending_users'] = 2
        html = render_modification_cost(result)
        self.assertIn('2 名用户达到计算上限仍待处理', html)
        self.assertEqual(html.count('type="checkbox"'), 2)


if __name__ == '__main__':
    unittest.main()
