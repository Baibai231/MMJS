"""The dashboard ranks only evaluated website scenarios and labels recommendations."""
import unittest

from policy.site_catalog import site_catalog
from web.policy_rankings import ranked, ranking_rows, render_policy_rankings


class PolicyRankingTests(unittest.TestCase):
    def test_three_rankings_use_distinct_metrics_and_do_not_pad(self):
        sites = [r for r in site_catalog()['sites']
                 if r['basis'] in ('partial_hard', 'recommended_scenario')][:10]
        controls, attacks = {}, {}
        for index, site in enumerate(sites):
            key = 'site_' + site['id']
            controls[key] = {'final': {'collision_probability': (11-index) / 1_000_000},
                             'modified_users': 1000 * index}
            attacks[key] = {'A1': {'minauto': [{'budget': 10**6, 'rate': index / 20,
                                              'complete': False, 'estimated': True,
                                              'outside_model_support_weight': 0}]}}
        result = {'dataset': {'registration_occurrences': 10000},
                  'config': {'budgets': [10**6]}, 'controls': controls,
                  'attacks': {'by_strategy': attacks},
                  'cohorts': [{'policy': {'name': 'site_google'}}], 'decisions': []}
        rows = ranking_rows(result)
        self.assertEqual(len(rows), 10)
        self.assertNotEqual(ranked(rows, 'distribution')[0]['key'],
                            ranked(rows, 'guessing')[0]['key'])
        self.assertEqual(ranked(rows, 'guessing')[0]['key'], 'site_google')
        self.assertEqual(ranked(rows, 'modification')[0]['key'], 'site_google')
        html = render_policy_rankings(result)
        self.assertEqual(html.count('下载此图 SVG'), 3)
        self.assertIn('建议采纳场景', html)
        self.assertIn('当前逐批控制器为何保持首批预设', html)
        del controls['site_google']
        self.assertEqual(len(ranking_rows(result)), 9)
        self.assertIn('前 9', render_policy_rankings(result))


if __name__ == '__main__':
    unittest.main()
