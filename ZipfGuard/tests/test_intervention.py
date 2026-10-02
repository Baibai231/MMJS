import copy
import hashlib
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from ai.pcfg_monte_carlo import Grammar, MonteCarloIndex
from core.intervention_state import Population
from core.intervention_risk import InterventionRisk, reference_mutation_ranks, evaluate_mutations
from experiments.intervention_config import load_intervention_config, validate_intervention_config
from experiments.intervention_pipeline import (run_arm, run_intervention_pipeline, reference_replay,
                                                google_round_zipf_experiment)
from policy.intervention_response import InterventionResponder
from policy.local_actions import Action, Group, LocalRule, generate_actions, capacities
from policy.open_policy import Rule
from policy.intervention_controller import predict_action, select_action, plan_once
from web.intervention_presentation import render_intervention_html, chart_specs, LABELS


class ExactIndex:
    n, seed = 1000, 42
    class GrammarMeta:
        metadata = {'training_sha256': 'test', 'source': 'exact toy rank test'}
    grammar = GrammarMeta()

    def query(self, word, rule=None):
        ranks = {'A': 1, 'B': 2, 'C': 3, 'D': 8, 'E': 9, 'F': 10, 'H': 11, 'J': 12,
                 'abc': 1, 'abc!': 100, 'def': 2, 'def!': 101}
        value = ranks.get(word)
        return {'guess_count': value, 'status': 'estimated' if value is not None else 'outside_model_support'}


class TestIntervention(unittest.TestCase):
    def cfg(self, n=1000):
        c = load_intervention_config()
        c['data']['users'] = n
        c['budgets'] = [1, 3, 1000]
        c['risk_budget'] = 3
        c['controller'].update(round_fraction=.1, total_fraction=.2, batch_fractions=[.1],
                               target_relative_reduction=1., max_hhi_increase=1., max_rounds=5)
        c['evaluation']['adaptive'] = False
        return c

    def risk(self):
        return InterventionRisk(ExactIndex(), [1, 3, 1000], 3)

    def action(self, ids, rule=None, eligible=None):
        return Action(Group('all', '', 'test'), LocalRule(rule or Rule('changed')),
                      'test', tuple(ids), eligible or len(ids))

    def test_hand_calculated_migration_coverage_and_repeat_rejection(self):
        p = Population(['A']*300+['B']*200+['C']*100+['D']*200+['E']*200)
        risk = self.risk()
        self.assertEqual(risk.evaluate(p.counts())['guarded_risk'], .6)
        p.apply([{'index': i, 'old': 'A', 'new': 'F' if i < 60 else 'H', 'status': 'changed', 'edit_cost': 1.} for i in range(100)])
        self.assertEqual(risk.evaluate(p.counts())['guarded_risk'], .5)
        p.apply([{'index': i, 'old': 'B', 'new': 'F' if i < 380 else 'J', 'status': 'changed', 'edit_cost': 1.} for i in range(300, 400)])
        self.assertEqual(risk.evaluate(p.counts())['guarded_risk'], .4)
        self.assertEqual(p.ledger()['affected_rate'], .2)
        self.assertEqual(p.counts()['F'], 140)
        self.assertEqual(sum(p.counts().values()), 1000)
        with self.assertRaises(ValueError):
            p.apply([{'index': 0, 'old': 'F', 'new': 'J', 'status': 'changed', 'edit_cost': 1.}])

    def test_no_response_and_failed_response_keep_old_mass(self):
        p = Population(['abc']*10)
        cfg = self.cfg(10)
        cfg['response']['nonresponse'] = 1.
        responder = InterventionResponder({'abc': 10}, cfg['response'])
        p.apply(responder.respond(p, self.action(range(2)), 1, 'execute'))
        self.assertEqual(p.counts(), {'abc': 10})
        self.assertEqual(p.ledger()['affected'], 2)
        self.assertEqual(p.ledger()['changed'], 0)
        cfg['response'].update(nonresponse=0., max_attempts=1)
        responder = InterventionResponder({'abc': 10}, cfg['response'])
        action = self.action([2], Rule('impossible', min_length=1000))
        p.apply(responder.respond(p, action, 1, 'execute'))
        self.assertEqual(p.ledger()['failed_to_comply'], 1)
        self.assertEqual(p.counts(), {'abc': 10})

    def test_partial_selection_and_integer_budget(self):
        cfg = self.cfg(103)
        cfg['controller'].update(round_fraction=.02, total_fraction=.05, batch_fractions=[.005, .01, .02])
        p = Population(['abc']*103)
        actions = generate_actions(p, self.risk(), cfg)
        self.assertTrue(actions)
        self.assertTrue(all(len(a.indices) <= 2 for a in actions))
        ids = actions[0].indices
        p.apply([{'index': i, 'old': 'abc', 'new': 'abc!', 'status': 'changed', 'edit_cost': .25} for i in ids])
        for a in generate_actions(p, self.risk(), cfg):
            self.assertFalse(set(ids).intersection(a.indices))
        self.assertEqual(p.counts()['abc'], 103-len(ids))

    def test_transaction_validates_before_any_mutation(self):
        p = Population(['A', 'B'])
        rows = [{'index': 0, 'old': 'A', 'new': 'F', 'status': 'changed', 'edit_cost': 1.},
                {'index': 5, 'old': 'B', 'new': 'F', 'status': 'changed', 'edit_cost': 1.}]
        with self.assertRaises(ValueError):
            p.apply(rows)
        self.assertEqual(p.ledger()['affected'], 0)
        self.assertEqual(p.counts(), {'A': 1, 'B': 1})

    def test_unknown_model_support_is_not_free_security_gain(self):
        cfg = self.cfg(100)
        p = Population(['abc']*100)
        class UnsupportedResponder:
            def respond(self, population, action, seed, stream):
                return [{'index': i, 'old': 'abc', 'new': 'outside', 'status': 'changed', 'edit_cost': 1.} for i in action.indices]
        pred = predict_action(p, self.action(range(10)), self.risk(), UnsupportedResponder(), cfg, 1)
        self.assertEqual(pred['predicted_hit_gain'], .1)
        self.assertEqual(pred['predicted_guarded_gain'], 0)
        self.assertFalse(pred['feasible'])

    def test_prediction_isolation_and_no_feasible_stop(self):
        cfg = self.cfg(100)
        cfg['response']['nonresponse'] = 1.
        responder = InterventionResponder({'abc': 100}, cfg['response'])
        p = Population(['abc']*100)
        before = copy.deepcopy(p.accounts)
        winner, audit = select_action(p, self.risk(), responder, cfg, 1)
        self.assertIsNone(winner)
        self.assertTrue(audit)
        self.assertEqual(p.accounts, before)
        result, counts, _ = run_arm('dynamic', p, ['abc']*100, self.risk(), responder, cfg)
        self.assertEqual(result['stop_reason'], 'no_feasible_positive_gain_action')
        self.assertTrue(result['terminal_candidate_audit'])
        self.assertEqual(counts, p.counts())
        self.assertEqual(len(result['trajectory']), 1)

    def test_selects_largest_sitewide_gain_not_smallest_group_efficiency(self):
        population = Population(['abc']*100)
        small = self.action(range(3))
        large = self.action(range(20))
        small_prediction = {'feasible': True, 'predicted_guarded_gain': .003,
                            'predicted_hhi_change': -.0001, 'score': .003}
        large_prediction = {'feasible': True, 'predicted_guarded_gain': .02,
                            'predicted_hhi_change': -.0002, 'score': .02}
        with patch('policy.intervention_controller.generate_actions', return_value=[small, large]), patch(
                'policy.intervention_controller.predict_action',
                side_effect=[small_prediction, large_prediction]):
            winner, audit = select_action(population, self.risk(), object(), self.cfg(100), 1)
        self.assertIs(winner[0], large)
        self.assertEqual([row['score'] for row in audit], [.003, .02])

    def test_rejects_tiny_candidate_when_one_prediction_has_no_gain(self):
        class VariableResponder:
            def respond(self, population, action, seed, stream):
                new = 'abc' if stream.endswith('-1') else 'abc!'
                return [{'index': 0, 'old': 'abc', 'new': new}]
        prediction = predict_action(Population(['abc']*100), self.action([0]),
                                    self.risk(), VariableResponder(), self.cfg(100), 1)
        self.assertGreater(prediction['predicted_guarded_gain'], 0)
        self.assertEqual(prediction['predicted_gain_range'][0], 0)
        self.assertFalse(prediction['feasible'])

    def test_feedback_recomputed_and_final_snapshot_retained(self):
        cfg = self.cfg(100)
        class Responder:
            def respond(self, p, a, seed, stream):
                return [{'index': i, 'old': p.accounts[i].password, 'new': p.accounts[i].password+'!',
                         'status': 'changed', 'edit_cost': .25} for i in a.indices]
        result, counts, _ = run_arm('dynamic', Population(['abc']*60+['def']*40),
                                    ['abc']*60+['def']*40, self.risk(), Responder(), cfg)
        self.assertEqual(result['stop_reason'], 'budget_exhausted')
        self.assertEqual(len(result['rounds']), 2)
        self.assertEqual(len(result['trajectory']), 3)
        self.assertEqual(result['rounds'][0]['after_state_sha256'], result['rounds'][1]['before_state_sha256'])
        self.assertEqual(result['final']['ledger']['affected'], 20)
        self.assertAlmostEqual(result['final']['risk']['guarded_risk'], .8)
        self.assertEqual(sum(counts.values()), 100)

    def test_google_control_and_dynamic_experiment_share_start_and_run_ten_rounds(self):
        cfg = self.cfg(100)
        cfg['controller'].update(round_fraction=.02, total_fraction=.2,
                                 batch_fractions=[.02], stagnation_patience=20)
        class Responder:
            def respond(self, p, a, seed, stream):
                return [{'index': i, 'old': p.accounts[i].password,
                         'new': p.accounts[i].password+'!', 'status': 'changed',
                         'edit_cost': .25} for i in a.indices]
        words = ['abc']*60 + ['longpass123']*40
        population = Population(words)
        responder = Responder()
        fixed, _, _ = run_arm('fixed_google', population, words,
                              self.risk(), responder, cfg)

        def choose(current, evaluator, response, config, round_id):
            ids = tuple(i for i, a in enumerate(current.accounts) if not a.notifications)[:1]
            action = self.action(ids, Rule('adaptive', min_length=12),
                                 eligible=sum(not a.notifications for a in current.accounts))
            prediction = {'predicted_guarded_gain': .01}
            return (action, prediction), [{'action': action.public(), 'score': 1,
                                           'feasible': True, 'rejection_reasons': []}]

        cfg['evaluation']['adaptive'] = True
        with patch('experiments.intervention_pipeline.select_action', side_effect=choose), patch(
                'experiments.intervention_pipeline.make_index', return_value=ExactIndex()) as fit:
            comparison = google_round_zipf_experiment(
                population, words, self.risk(), responder, cfg, fixed)
        self.assertEqual(fit.call_count, 2)
        self.assertTrue(comparison['common_google_start_verified'])
        self.assertEqual(comparison['control']['state_sha256'],
                         comparison['experimental']['start_state_sha256'])
        self.assertEqual(comparison['google_baseline']['target']['eligible'], 60)
        self.assertEqual(comparison['google_baseline']['target']['changed'], 60)
        self.assertEqual(comparison['google_baseline']['target']['failures'], 0)
        self.assertEqual(comparison['google_baseline']['target']['short_passwords_remaining'], 0)
        self.assertGreater(comparison['google_baseline']['target']['explicit_length_completions'], 0)
        self.assertNotEqual(comparison['control']['distribution'], fixed['final']['distribution'])
        self.assertEqual(comparison['experimental']['rounds_completed'], 10)
        self.assertEqual(len(comparison['experimental']['snapshots']), 10)
        for state in comparison['arms']['google_dynamic']['trajectory']:
            self.assertEqual(state['google_compliance']['short_password_accounts'], 0)
        for row in comparison['arms']['google_dynamic']['rounds']:
            self.assertGreaterEqual(row['action']['rule_definition']['min_length'], 8)
            self.assertEqual(row['reference_replay']['google_compliance']['short_password_accounts'], 0)
        self.assertEqual(comparison['experimental']['snapshots'][0]['round'], 1)
        self.assertEqual(comparison['experimental']['snapshots'][0]['action']['rule'], 'test')
        self.assertEqual(comparison['experimental']['snapshots'][-1]['affected_rate'], .7)
        arms = comparison['arms']
        self.assertEqual(arms['google_hold']['final'], arms['google_dynamic']['trajectory'][0])
        self.assertEqual(arms['google_dynamic']['final']['ledger']['affected_rate'], .7)
        report = {'google_round_zipf': comparison, 'baseline': fixed['trajectory'][0], 'config': cfg,
                  'baseline_mutations': evaluate_mutations(population.counts(),
                      reference_mutation_ranks({'abc': 100}, 100), cfg['budgets'])}
        specs = chart_specs(report)
        for name in ('risk_cost', 'guarded_cost', 'attack_F', 'attack_A1', 'attack_mutations', 'final_distribution'):
            series = specs[name][1]
            self.assertEqual([label for label, _ in series], [label for _, label in LABELS])
            self.assertEqual(len(series), 3)
        self.assertEqual(specs['risk_cost'][1][1][1][-1][0], .6)
        self.assertEqual(specs['risk_cost'][1][2][1][-1][0], .7)
        self.assertEqual(specs['final_distribution'][1][2][1],
                         comparison['experimental']['snapshots'][-1]['distribution']['full_rank_frequency'])
        self.assertEqual(len(specs['google_round_zipf'][1]), 11)

    def test_one_shot_disjoint_and_predictions_do_not_follow_outcomes(self):
        cfg = self.cfg(100)
        class Responder:
            def respond(self, p, a, seed, stream):
                return [{'index': i, 'old': p.accounts[i].password, 'new': p.accounts[i].password+'!',
                         'status': 'changed', 'edit_cost': .25} for i in a.indices]
        p = Population(['abc']*100)
        schedule = plan_once(p, self.risk(), Responder(), cfg)
        ids = [i for a, _, _ in schedule for i in a.indices]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 20)
        self.assertEqual(p.ledger()['affected'], 0)
        self.assertTrue(all(a.rule.fragment is not None and
                            1 <= a.rule.fragment.number <= 18 for a, _, _ in schedule))

    def test_google_dynamic_rejects_short_responses_before_applying(self):
        cfg = self.cfg(100)
        cfg['controller']['policy_floor_minimum_length'] = 8
        population = Population(['longpass123']*100)
        action = self.action([0], Rule('local', min_length=8), eligible=100)
        class BadResponder:
            def respond(self, p, a, seed, stream):
                return [{'index': 0, 'old': p.accounts[0].password, 'new': 'short',
                         'status': 'changed', 'edit_cost': 1.}]
        with patch('experiments.intervention_pipeline.select_action',
                   return_value=((action, {'predicted_guarded_gain': .1}), [])):
            with self.assertRaisesRegex(AssertionError, '动态响应'):
                run_arm('google_dynamic', population, ['longpass123']*100,
                        self.risk(), BadResponder(), cfg)
        self.assertEqual(population.ledger()['affected'], 0)

    def test_google_floor_is_inherited_by_all_local_candidates(self):
        cfg = self.cfg(100)
        cfg['controller']['policy_floor_minimum_length'] = 8
        population = Population(['12345678']*50+['aaaaaaaa']*50)
        risk = self.risk()
        with patch.object(risk, 'hit', return_value=1.):
            actions = generate_actions(population, risk, cfg)
        self.assertTrue(actions)
        self.assertTrue(all(action.rule.base.min_length >= 8 for action in actions))

    def test_reference_replay_targets_only_matching_independent_accounts(self):
        cfg = self.cfg(100)
        cfg['controller']['round_fraction'] = .2
        ref = Population(['abc']*50+['def']*50, 'reference')
        action = Action(Group('hot', 'abc', 'hidden'), LocalRule(Rule('block', blocklist=frozenset({'abc'}))), 'block', tuple(range(10)), 50)
        responder = InterventionResponder({'abc': 50, 'def': 50}, {**cfg['response'], 'nonresponse': 1.})
        replay = reference_replay(ref, action, 100, responder, cfg, 1)
        self.assertEqual(replay['selected_reference_accounts'], 10)
        self.assertEqual(ref.ledger()['affected'], 10)
        self.assertTrue(all(not a.notifications for a in ref.accounts[50:]))
        self.assertEqual(ref.counts(), {'abc': 50, 'def': 50})

    def test_reference_mutations_reveal_predictable_suffix_and_budget(self):
        ranks = reference_mutation_ranks({'abc': 100}, 100)
        ev = evaluate_mutations({'abc2026!': 5}, ranks, [1, 10, 100])
        self.assertEqual(ev['points'][0]['rate'], 0)
        self.assertEqual(ev['points'][1]['rate'], 1)
        self.assertEqual(ev['points'][2]['effective_guesses'], len(ranks))
        self.assertTrue(ev['points'][2]['exhausted'])

    def test_bad_configs_rejected(self):
        for change in ({'round_fraction': 0}, {'total_fraction': .01}, {'prediction_repeats': True}, {'max_rounds': 0}):
            cfg = self.cfg()
            cfg['controller'].update(change)
            with self.assertRaises(ValueError):
                validate_intervention_config(cfg)
        cfg = self.cfg()
        cfg['response']['weights'] = [float('nan'), .5, .5]
        with self.assertRaises(ValueError):
            validate_intervention_config(cfg)

    def test_saved_report_no_passwords_and_shared_interactive_legends(self):
        cfg = self.cfg(100)
        cfg['response']['nonresponse'] = 1.
        dataset = {'cohorts': [['abc']*100], 'development': {'train': Counter({'abc': 100})}, 'metadata': {'fixture': 'toy'}}
        with tempfile.TemporaryDirectory() as directory:
            report = run_intervention_pipeline(cfg, dataset=dataset, index=ExactIndex(), output_dir=directory)
            public = json.dumps(report, ensure_ascii=False)
            self.assertNotIn('"abc"', public)
            self.assertIn('google_round_zipf', report)
            self.assertEqual(set(report['arms']), {'google_hold', 'google_dynamic'})
            self.assertNotIn('legacy_arms', report)
            html = render_intervention_html(report)
            self.assertIn('plot-distinct', html)
            self.assertIn('input type="checkbox"', html)
            self.assertIn('Google 固定对照与动态调整 10 轮的 Zipf 分布', html)
            self.assertIn('10³', html)
            self.assertIn('未运行', html)
            self.assertNotIn('"abc"', html)
            svg = (Path(directory)/'risk_cost.svg').read_text(encoding='utf-8')
            self.assertIn('aria-label="图例"', svg)
            self.assertIn('Google 政策不变', svg)
            self.assertNotIn('初始一次规划', svg)
            self.assertTrue((Path(directory)/'report.json.sha256').is_file())
            self.assertEqual(hashlib.sha256((Path(directory)/'report.json').read_bytes()).hexdigest(),
                             (Path(directory)/'report.json.sha256').read_text().split()[0])

    def test_real_monte_carlo_adapter_and_weighted_risk(self):
        grammar = Grammar({'T': {'abc': .6, 'def': .4}}, [(['T'], 1.)])
        risk = InterventionRisk(MonteCarloIndex(grammar, 1000, 42), [1, 100], 100)
        ev = risk.evaluate({'abc': 6, 'outside': 4})
        self.assertEqual(ev['guarded_risk'], 1.)
        self.assertEqual(ev['minauto'][-1]['rate'], .6)
        self.assertEqual(ev['minauto'][-1]['outside_model_support_weight'], 4)


if __name__ == '__main__':
    unittest.main()
