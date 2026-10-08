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
from core.intervention_risk import (CombinedInterventionRisk, InterventionRisk,
                                    reference_mutation_ranks, evaluate_mutations)
from experiments.intervention_config import load_intervention_config, validate_intervention_config
from experiments.intervention_pipeline import (run_arm, run_intervention_pipeline, reference_replay,
                                                google_round_zipf_experiment)
from policy.intervention_response import InterventionResponder
from policy.local_actions import Action, Group, LocalRule, MultiAction, generate_actions, capacities
from policy.open_policy import Rule
from policy.intervention_controller import (predict_action, select_action,
                                            select_random_action, random_account_order, plan_once)
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
        c['controller']['execution_policy'] = 'fixed-F-strict-batch-v1'
        c['response']['mode'] = 'finite-response-v1'
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

    def test_response_collects_rule_compliant_proposals_before_population_guard(self):
        cfg = self.cfg(3)
        cfg['response'].update(nonresponse=0., max_attempts=1)
        responder = InterventionResponder({'abc': 3}, cfg['response'], rank_model=self.risk())
        population = Population(['abc']*3)
        with patch('policy.intervention_response._propose', side_effect=['abc!', 'outside', 'abc']):
            rows = responder.respond(population, self.action(range(3), Rule('longer', min_length=4)), 1, 'guard')
        self.assertEqual([row['status'] for row in rows],
                         ['changed', 'changed', 'failed_to_comply'])
        self.assertEqual(rows[0]['new'], 'abc!')
        self.assertEqual(rows[1]['new'], 'outside')
        self.assertTrue(responder.stronger('abc', rows[0]['new']))
        self.assertFalse(responder.stronger('abc', 'outside'))

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

    def test_google_migration_is_separate_from_adaptive_budget(self):
        cfg = self.cfg(100)
        p = Population(['abc'] * 100)
        p.apply([{'index': i, 'old': 'abc', 'new': 'abcdefgh',
                  'status': 'changed', 'edit_cost': 1.} for i in range(100)], phase='google')
        self.assertEqual(p.ledger()['google_affected'], 100)
        self.assertEqual(p.ledger()['adaptive_affected'], 0)
        self.assertEqual(capacities(p, cfg), 10)
        p.apply([{'index': i, 'old': 'abcdefgh', 'new': 'abcdefgh!',
                  'status': 'changed', 'edit_cost': .1} for i in range(10)])
        self.assertEqual(p.ledger()['adaptive_affected'], 10)
        self.assertEqual(capacities(p, cfg), 10)
        self.assertEqual(p.ledger()['affected'], 100)
        self.assertEqual(p.ledger()['adaptive_notification_events'], 10)

    def test_transaction_validates_before_any_mutation(self):
        p = Population(['A', 'B'])
        rows = [{'index': 0, 'old': 'A', 'new': 'F', 'status': 'changed', 'edit_cost': 1.},
                {'index': 5, 'old': 'B', 'new': 'F', 'status': 'changed', 'edit_cost': 1.}]
        with self.assertRaises(ValueError):
            p.apply(rows)
        self.assertEqual(p.ledger()['affected'], 0)
        self.assertEqual(p.counts(), {'A': 1, 'B': 1})

    def test_distribution_gain_cannot_pass_via_new_model_blind_spots(self):
        cfg = self.cfg(100)
        p = Population(['abc']*100)
        class UnsupportedResponder:
            def respond(self, population, action, seed, stream):
                return [{'index': i, 'old': 'abc', 'new': 'outside', 'status': 'changed', 'edit_cost': 1.} for i in action.indices]
        risk = self.risk()
        with patch.object(risk, 'hit', side_effect=AssertionError('candidate queried attack risk')):
            pred = predict_action(p, self.action(range(10)), risk, UnsupportedResponder(), cfg, 1)
        self.assertNotIn('predicted_hit_gain', pred)
        self.assertGreater(pred['predicted_distribution_gain'], 0)
        self.assertFalse(pred['feasible'])
        self.assertGreater(pred['predicted_uncovered_rate_change'], 0)

    def test_composite_gain_matches_realized_objective_on_same_model(self):
        model = self.risk()
        before = Counter({'A': 60, 'B': 40})
        risk = CombinedInterventionRisk(model, model, baseline_counts=before,
                                        risk_weight=.8)
        rows = [{'old': 'A', 'new': 'F'} for _ in range(10)]
        after = before.copy()
        after['A'] -= 10
        after['F'] += 10
        _, _, gain, _ = risk.gain_components(rows, before)
        self.assertAlmostEqual(gain, risk.objective(before)-risk.objective(after))
        self.assertEqual(risk.loss('outside'), .5)

    def test_dynamic_considers_random_action_under_same_state(self):
        population = Population(['abcdefgh']*20)
        risk = CombinedInterventionRisk(self.risk(), self.risk(),
                                        baseline_counts=population.counts())
        action = self.action((0, 1))
        prediction = {'score': .1, 'feasible': True, 'predicted_hhi_change': -.01,
                      'predicted_empirical_cdf_gain': .1}
        with patch('policy.intervention_controller.generate_actions', return_value=[]), patch(
                'policy.intervention_controller.select_random_action',
                return_value=((action, prediction), [])), patch(
                'policy.intervention_controller.predict_action', return_value=prediction):
            winner, audit = select_action(population, risk, object(), self.cfg(20), 1,
                                          validation=False)
        self.assertIs(winner[0], action)
        self.assertEqual(audit[0]['score'], .1)
        with patch('policy.intervention_controller.generate_actions', return_value=[]), patch(
                'policy.intervention_controller.select_random_action',
                return_value=((action, prediction), [])), patch(
                'policy.intervention_controller.predict_action', return_value=prediction):
            winner, audit = select_action(population, risk, object(), self.cfg(20), 1,
                                          target_size=3, validation=False)
        self.assertIs(winner[0], action)
        self.assertEqual(len(audit), 1)

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
        large = self.action(range(10))
        small_prediction = {'feasible': True, 'predicted_empirical_cdf_gain': .003,
                            'predicted_hhi_change': -.0001, 'score': .003}
        large_prediction = {'feasible': True, 'predicted_empirical_cdf_gain': .02,
                            'predicted_hhi_change': -.0002, 'score': .02}
        def fake_predict(_population, action, *_args):
            if isinstance(action, MultiAction):
                return {'feasible': True, 'predicted_empirical_cdf_gain': .015,
                        'predicted_hhi_change': -.00015, 'score': .015}
            return small_prediction if action is small else large_prediction
        with patch('policy.intervention_controller.generate_actions', return_value=[small, large]), patch(
                'policy.intervention_controller.predict_action',
                side_effect=fake_predict):
            winner, audit = select_action(population, self.risk(), object(), self.cfg(100), 1,
                                          validation=False)
        self.assertIs(winner[0], large)
        self.assertEqual([row['score'] for row in audit[:2]], [.003, .02])
        self.assertTrue(all(len(set(row[0].indices)) == len(row[0].indices)
                            for row in [(winner[0], winner[1])]))

    def test_tiny_mean_gain_is_retained_and_each_response_is_fitted(self):
        class VariableResponder:
            def respond(self, population, action, seed, stream):
                new = 'abc' if stream.endswith('-1') else 'abc!'
                return [{'index': 0, 'old': 'abc', 'new': new}]
        cfg = self.cfg(100)
        cfg['controller']['min_positive_trial_fraction'] = 1.
        with patch('policy.intervention_controller.fitted_ideal_distance',
                   side_effect=[{'score': .49}, {'score': .5}, {'score': .495}]) as fit:
            prediction = predict_action(Population(['abc']*100), self.action([0]),
                                        self.risk(), VariableResponder(), cfg, 1,
                                        baseline_fit={'score': .5})
        self.assertGreater(prediction['predicted_distribution_gain'], 0)
        self.assertEqual(prediction['predicted_gain_range'][0], 0)
        self.assertTrue(prediction['feasible'])
        self.assertEqual(fit.call_count, 3)
        self.assertAlmostEqual(prediction['predicted_fitted_log_area_gain'], .005)
        self.assertEqual(prediction['fitted_gain_range'][0], 0)

    def test_empirical_disagreement_is_reported_without_overriding_fitted_goal(self):
        class ConcentratingResponder:
            def respond(self, population, action, seed, stream):
                return [{'index': 6, 'old': 'abc', 'new': 'abc!', 'status': 'changed'}]

        population = Population(['abc!']*6 + ['abc']*4)
        with patch('policy.intervention_controller.fitted_ideal_distance',
                   return_value={'score': .49}):
            prediction = predict_action(population, self.action([6]), self.risk(),
                                        ConcentratingResponder(), self.cfg(10), 1,
                                        baseline_fit={'score': .5})
        self.assertLess(prediction['predicted_empirical_log_area_gain'], 0)
        self.assertGreater(prediction['predicted_fitted_log_area_gain'], 0)
        self.assertTrue(prediction['feasible'])

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

        def choose(current, evaluator, response, config, round_id, **kwargs):
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
        self.assertGreaterEqual(fit.call_count, 2)
        self.assertTrue(comparison['common_google_start_verified'])
        self.assertEqual(set(comparison['arms']),
                         {'google_hold', 'google_random', 'google_frozen', 'google_dynamic'})
        for key in ('google_random', 'google_frozen', 'google_dynamic'):
            self.assertEqual(comparison['arms'][key]['trajectory'][0]['state_sha256'],
                             comparison['control']['state_sha256'])
        schedule = [row['action']['selected'] for row in comparison['arms']['google_dynamic']['rounds']]
        self.assertEqual(comparison['comparison_budget']['dynamic_round_notification_schedule'], schedule)
        self.assertEqual(comparison['comparison_budget']['planned_round_notification_schedule'], [2]*10)
        self.assertLessEqual(len(comparison['arms']['google_random']['rounds']), 10)
        frozen_sizes = [row['action']['selected'] for row in comparison['arms']['google_frozen']['rounds']]
        self.assertTrue(all(size <= 2 for size in frozen_sizes))
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
        self.assertEqual(comparison['arms']['google_dynamic']['final']['ledger']['adaptive_affected_rate'], .1)
        arms = comparison['arms']
        self.assertEqual(arms['google_hold']['final'], arms['google_dynamic']['trajectory'][0])
        self.assertEqual(arms['google_dynamic']['final']['ledger']['affected_rate'], .7)
        report = {'google_round_zipf': comparison, 'baseline': fixed['trajectory'][0], 'config': cfg,
                  'baseline_mutations': evaluate_mutations(population.counts(),
                      reference_mutation_ranks({'abc': 100}, 100), cfg['budgets'])}
        specs = chart_specs(report)
        for name in ('coverage_F', 'attack_F', 'attack_A1', 'final_distribution'):
            series = specs[name][1]
            expected = LABELS[1:] if name == 'attack_A1' else LABELS
            self.assertEqual([label for label, _ in series], [label for _, label in expected])
            self.assertEqual(len(series), len(expected))
        self.assertNotIn('risk_cost', specs)
        self.assertNotIn('guarded_cost', specs)
        self.assertNotIn('attack_mutations', specs)
        self.assertEqual(specs['coverage_F'][1][1][1][-1][0], 0.)
        self.assertEqual(specs['coverage_F'][1][4][1][-1][0], .1)
        self.assertEqual(specs['final_distribution'][1][4][1],
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

    def test_random_group_is_drawn_before_rule_selection_and_compliant_users_keep_passwords(self):
        cfg = self.cfg(100)
        cfg['controller']['policy_floor_minimum_length'] = 8
        population = Population(['abcdefgh']*50 + ['abc12345']*50)
        responder = InterventionResponder({'abcdefgh': 50, 'abc12345': 50},
                                          {**cfg['response'], 'nonresponse': 0.})
        with patch('policy.intervention_controller.predict_action',
                   side_effect=lambda p, a, *args, **kwargs: {
                       'predicted_guarded_gain': (sum(x.rule.fragment.number for x in a.components)
                                                  if isinstance(a, MultiAction) else a.rule.fragment.number) / 1000,
                       'predicted_hhi_change': 0., 'score': (sum(x.rule.fragment.number for x in a.components)
                                                            if isinstance(a, MultiAction) else a.rule.fragment.number) / 1000,
                       'feasible': True, 'rejection_reasons': []}):
            winner, audit = select_random_action(population, self.risk(), responder, cfg, 1)
        action = winner[0]
        self.assertGreater(len(audit), 1)
        self.assertEqual(audit[-1]['action']['selected'], len(winner[0].indices))
        self.assertIsInstance(action, MultiAction)
        self.assertEqual(set(action.indices), set(random_account_order(
            population, list(range(100)), cfg['seed'], 1)[:10]))
        self.assertEqual(len(action.indices), len(set(action.indices)))
        self.assertTrue(all(part.group.kind == 'random' and part.rule.base.min_length >= 8
                            for part in action.components))
        # A randomly notified account already meeting a chosen rule pays the
        # notification cost but is not forced to change its password.
        accepted = Action(Group('random', '', '随机抽取账户'),
                          LocalRule(Rule('length-8', min_length=8)), 'length-8', (0,), 100)
        rows = responder.respond(population, accepted, cfg['seed'], 'execution-1')
        self.assertEqual(rows[0]['status'], 'already_compliant')
        population.apply(rows)
        self.assertEqual(population.ledger()['affected'], 1)
        self.assertEqual(population.ledger()['changed'], 0)

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
            self.assertEqual(set(report['arms']),
                             {'google_hold', 'google_random', 'google_frozen', 'google_dynamic'})
            self.assertNotIn('legacy_arms', report)
            html = render_intervention_html(report)
            self.assertIn('plot-distinct', html)
            self.assertIn('input type="checkbox"', html)
            self.assertIn('Google 固定对照与动态调整 10 轮的 Zipf 分布', html)
            self.assertIn('10³', html)
            self.assertIn('未运行', html)
            self.assertNotIn('"abc"', html)
            svg = (Path(directory)/'coverage_F.svg').read_text(encoding='utf-8')
            self.assertIn('aria-label="图例"', svg)
            self.assertIn('Google 基础策略', svg)
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
