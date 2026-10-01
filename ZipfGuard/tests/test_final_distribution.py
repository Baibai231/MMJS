"""The final panel must show the entire matched cohort without fabricating a tail."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from core.uniformity import concentration
from web.final_distribution import final_rank_series, render_final_distribution
from web.presentation import plot


class FinalDistributionTests(unittest.TestCase):
    def fixture(self, folder):
        originals = ['alpha', 'alpha', 'alpha', 'beta', 'beta', 'gamma']
        finals = ['one', 'one', 'two', 'three', 'four', 'five']
        records = [{'original_password': a, 'final_password': b}
                   for a, b in zip(originals, finals)]
        records.append({'original_password': 'no-final-password', 'final_password': None})
        content = ''.join(json.dumps(row) + '\n' for row in records).encode()
        path = Path(folder) / 'outcomes.jsonl'
        path.write_bytes(content)
        return {'dataset': {'registration_occurrences': 7},
                'baseline_completed_distribution': concentration(originals, curve_limit=2),
                'final_distribution': concentration(finals, curve_limit=2),
                'user_strength': {'local_private_file': str(path),
                                  'private_file_sha256': hashlib.sha256(content).hexdigest()}}

    def test_full_tail_and_same_cohort_are_recovered_without_plaintext(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.fixture(folder)
            series, complete = final_rank_series(result)
            self.assertTrue(complete)
            self.assertEqual(series[0][1][-1], (3, 1))
            self.assertEqual(series[1][1][-1], (5, 1))
            html = render_final_distribution(result)
            self.assertEqual(html.count('type="checkbox"'), 2)
            self.assertIn('同一批 6 人', html)
            self.assertNotIn('策略前：全部用户', html)
            self.assertNotIn('alpha', html)
            self.assertNotIn('no-final-password', html)

    def test_missing_or_mismatched_private_data_does_not_invent_tail(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.fixture(folder)
            result['user_strength']['private_file_sha256'] = 'wrong'
            series, complete = final_rank_series(result)
            self.assertFalse(complete)
            self.assertEqual(len(series[1][1]), 2)
            self.assertIn('长尾数据暂不可用', render_final_distribution(result))

    def test_loglog_spacing_is_multiplicative_on_both_axes(self):
        html = plot([('before', [(1, 100), (10, 10), (100, 1)])],
                    xlabel='rank', ylabel='count', log=True, log_y=True,
                    distinguish=True, markers=False, x_format='count', y_format='count')
        svg = ElementTree.fromstring(html[html.index('<svg '):html.index('</svg>')+6])
        coords = [tuple(map(float, point.split(',')))
                  for point in svg.find('g/polyline').get('points').split()]
        self.assertAlmostEqual(coords[1][0] - coords[0][0], coords[2][0] - coords[1][0])
        self.assertAlmostEqual(coords[1][1] - coords[0][1], coords[2][1] - coords[1][1])
        self.assertEqual(len(svg.findall('g/circle')), 0)


if __name__ == '__main__':
    unittest.main()
