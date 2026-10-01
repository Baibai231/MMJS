"""Cost parity, full-batch forecasting, causal prefix attacks and true hold ties."""
import tempfile
import unittest
from collections import Counter
from unittest.mock import patch

from core.uniformity import forecast_collision
from core.registration import registration_from_counts
from experiments.dynamic_config import load_dynamic_config
from experiments.dynamic_pipeline import run_dynamic_pipeline, _cost_audit
from policy.dynamic_catalog import initial_policy
from policy.dynamic_controller import select_next
from policy.user_response import weighted_pool, phrase_vocabulary
from web.registration_distribution import normalized_rank_series
from web.dynamic_presentation import render_dynamic_html
from web.dynamic_comparison import attack_series
from web.final_distribution import final_rank_series


class FairDynamicTests(unittest.TestCase):
    def test_full_batch_pair_forecast_uses_real_horizon(self):
        history, preview = Counter(a=3, b=1), Counter(a=2, b=2)
        self.assertAlmostEqual(forecast_collision(history, preview, 10), 76 / 182)
        self.assertNotEqual(forecast_collision(history, preview, 10),
                            forecast_collision(history, preview, 4))
        self.assertIsNone(forecast_collision(history, Counter(a=1), 10))

    def test_identical_candidate_preserves_hold_on_tie(self):
        cfg = load_dynamic_config()['controller']
        cfg['candidate_source'] = 'legacy'
        cfg['min_relative_collision_gain'] = 0
        counts = Counter({'abcdefgh1': 10, 'otherword1': 10})
        rule = initial_policy()
        with patch('policy.dynamic_controller.next_actions', return_value=[('hold', rule), ('same-result', rule)]):
            _, decision = select_next(rule, Counter(), development_train=counts,
                                      preview_words=list(counts.elements()),
                                      pool=weighted_pool(counts), vocabulary=phrase_vocabulary(counts),
                                      seed=42, config=cfg, next_batch_users=10000)
        self.assertEqual(decision['action'], 'hold')
        self.assertEqual(decision['predicted_batch_users'], 10000)

    def test_same_cost_limits_flag_controls_without_weakening_them(self):
        cfg = load_dynamic_config('dynamic_full')['controller']
        rows = [{'response': {'modification_rate': p, 'pending_users': 0},
                 'hold_modification_rate': .5} for p in (.5, .75)]
        audit = _cost_audit(rows, cfg)
        self.assertFalse(audit['eligible'])
        self.assertEqual(len(audit['observed'][1]['reasons']), 3)
        self.assertEqual(rows[1]['response']['modification_rate'], .75)

    def test_normalization_uses_shared_registered_population(self):
        summary = {'users': 8, 'rank_curve': [[1, .5]],
                   'full_rank_frequency': [[1, 4], [5, 1]]}
        curve, _ = normalized_rank_series([('pending-arm', summary)], 10)
        self.assertEqual(curve[0][1], [(0.1, 0.4), (0.5, 0.1)])

    def test_common_first_and_stage_attacks_preserve_prefix_causality(self):
        # Explicitly exercise the retained historical fixed-preset protocol.
        cfg = load_dynamic_config()
        cfg.pop('monte_carlo')
        cfg['attackers'] = {'frequency': 'required', 'dictionary-rules': 'required'}
        cfg['controller']['candidate_source'] = 'legacy'
        cfg['controls'].update(preset='length8', comparison_protocol='fixed-preset-three-attacks-v2')
        cfg['data'].update(users=60, development=90, cohort_size=20)
        cfg['budgets'] = [10, 100]
        data = registration_from_counts(Counter({'abcdefgh1': 70, 'otherword1': 70,
                                                  'short': 70, 'different2': 70}),
                                        users=60, development=90, seed=42, cohort_size=20)
        changed = {**data, 'cohorts': [*data['cohorts'][:2], ['futureOnly123!'] * 20]}
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            first = run_dynamic_pipeline(cfg, dataset=data, output_dir=a)
            second = run_dynamic_pipeline(cfg, dataset=changed, output_dir=b)
        self.assertEqual(set(first['controls']), {'fixed_preset'})
        for arm in first['controls'].values():
            self.assertTrue(all(p == first['cohorts'][0]['policy'] for p in arm['policy_schedule']))
            self.assertEqual(arm['policy_schedule'][0], first['cohorts'][0]['policy'])
            self.assertEqual(arm['timeline'][0]['cumulative'], first['cohorts'][0]['cumulative'])
            self.assertIn('cost_audit', arm)
        stages = first['attacks']['registration_stages_F']['stages']
        self.assertEqual(stages[:2], second['attacks']['registration_stages_F']['stages'][:2])
        self.assertEqual(stages[-1]['evaluations']['dynamic']['minauto'], first['attacks']['F']['minauto'])
        for stage in stages:
            for ev in stage['evaluations'].values():
                self.assertTrue(all(p['target_weight'] == stage['users'] for p in ev['minauto']))
        self.assertTrue(all(d['predicted_batch_users'] == 20 for d in first['decisions']))
        attacks = first['attacks']['by_strategy']
        self.assertEqual(set(attacks), {'dynamic', 'baseline', 'fixed_preset'})
        for levels in attacks.values():
            self.assertEqual(set(levels), {'F', 'A0', 'A1'})
            for ev in levels.values():
                self.assertTrue(all(p['target_weight'] == 60 for p in ev['minauto']))
        self.assertEqual(attacks['baseline']['F'], attacks['baseline']['A0'])
        self.assertEqual(attacks['baseline']['F'], attacks['baseline']['A1'])
        self.assertEqual(attacks['fixed_preset']['F']['minauto'],
                         stages[-1]['evaluations']['fixed_preset']['minauto'])
        self.assertEqual(attacks['fixed_preset']['A1'], first['attacks']['controls']['fixed_preset'])
        self.assertEqual(len(attack_series(first)), 9)
        self.assertEqual(len(final_rank_series(first)[0]), 3)
        html = render_dynamic_html(first)
        for removed in ('固定长度与字符类别', '开发集选定固定规则', '固定加严日程',
                        '同一批用户的原口令', '相同完成用户原口令'):
            self.assertNotIn(removed, html)


if __name__ == '__main__':
    unittest.main()
