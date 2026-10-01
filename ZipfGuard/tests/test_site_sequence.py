"""A supplied sequence chooses the rule for every cohort, including the first."""
import tempfile
import unittest

from core.registration import registration_from_counts
from experiments.dynamic_config import load_dynamic_config, validate_dynamic_config
from experiments.dynamic_pipeline import run_dynamic_pipeline
from web.dynamic_presentation import render_dynamic_html
from tools.audit_top15_sequence_space import audit
from experiments.site_sequence_space import cost_feasible_space
from policy.site_catalog import site_catalog


class SiteSequenceTests(unittest.TestCase):
    def test_search_space_count_does_not_claim_evaluation(self):
        result = audit(10)
        self.assertEqual(result['listed_structural_paths'], 15**10)
        self.assertEqual(result['executable_structural_paths'], 11**10)
        self.assertEqual(len(result['excluded_labels']), 4)
        self.assertIn('count_only', result['status'])

    def test_cost_filter_counts_paths_without_claiming_a1(self):
        catalog = site_catalog()
        controls = {}
        for site in catalog['sites']:
            if site['basis'] not in ('partial_hard', 'recommended_scenario') or site['id'] == 'instagram':
                continue
            high_cost = site['id'] in ('x', 'tiktok', 'yahoo', 'linkedin', 'github')
            controls['site_' + site['id']] = {'timeline': [
                {'response': {'modification_rate': .9 if high_cost else .5,
                              'pending_users': 0}} for _ in range(10)]}
        frozen = {'sites': [dict(site, basis='unknown', rule={})
                            if site['id'] == 'instagram' else site
                            for site in catalog['sites']]}
        result = {'site_catalog': frozen, 'controls': controls,
                  'cohorts': [{} for _ in range(10)],
                  'metadata': {'run_id': 'test'}}
        audit = cost_feasible_space(result)
        self.assertEqual(audit['hard_label_paths'], 5**10)
        self.assertEqual(audit['all_label_paths'], 6**10)
        self.assertEqual(audit['distinct_all_rule_paths'], 3**10)
        self.assertEqual(audit['inferred_equal_rule_controls']['site_instagram'],
                         'site_amazon')
        self.assertIn('A1 paths not evaluated', audit['status'])

    def test_explicit_site_sequence_is_evaluated_without_online_selection(self):
        cfg = load_dynamic_config()
        cfg['data'].update(users=30, development=60, cohort_size=10)
        cfg['monte_carlo']['samples'] = 1000
        sequence = ['site_instagram', 'site_google', 'site_amazon']
        cfg['controller']['sequence'] = sequence
        dataset = registration_from_counts(
            {'hello123': 60, 'world456': 50, 'Hello123': 40,
             'summer2026!': 30, 'purple77': 30},
            users=30, development=60, seed=42, cohort_size=10)
        with tempfile.TemporaryDirectory() as directory:
            result = run_dynamic_pipeline(cfg, dataset=dataset, output_dir=directory)
        self.assertEqual([row['policy']['name'] for row in result['cohorts']], sequence)
        self.assertEqual([row['status'] for row in result['decisions']],
                         ['scheduled', 'scheduled'])
        html = render_dynamic_html(result)
        self.assertIn('15^3 = 3,375', html)
        self.assertIn('尚未搜索完整路径空间', html)

    def test_unknown_or_unapproved_rules_are_rejected(self):
        cfg = load_dynamic_config()
        cfg['data'].update(users=30, development=60, cohort_size=10)
        for name in ('site_facebook', 'site_reddit'):
            cfg['controller']['sequence'] = ['site_google', name, 'site_amazon']
            with self.assertRaisesRegex(ValueError, '策略序列无效'):
                validate_dynamic_config(cfg)


if __name__ == '__main__':
    unittest.main()
