import copy
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from ai.dual_attack import DualAttackIndex
from core.intervention_risk import InterventionRisk
from core.intervention_acceptance import INDIVIDUAL_POLICY, apply_response_policy
from core.intervention_attack_area import strength_diagnostics
from core.intervention_state import Population
from experiments.intervention_config import load_intervention_config, validate_intervention_config
from policy.intervention_response import InterventionResponder
from policy.intervention_controller import predict_action
from experiments.intervention_pipeline import run_arm, run_intervention_pipeline, reference_replay
from policy.local_actions import Action, Group, LocalRule
from policy.open_policy import Rule


class ToyGrammar:
    def __init__(self):
        self.metadata = {'training_sha256': 'toy'}

    def probability(self, word):
        return .1 if word in ('weakpass', 'sharedxx') else 0.


def index():
    return DualAttackIndex(ToyGrammar(), {
        'pcfg': ['weakpass', 'sharedxx', 'weakpass'],
        'markov': ['markovxx', 'sharedxx', 'anotherx']}, 3,
        {'markov': {'min_length': 3, 'max_length': 19, 'alphabet': 'abcdefghijklmnopqrstuvwxyz0123456789!'},
         'sources': {'scope': 'toy fixture'}})


class DualAttackTests(unittest.TestCase):
    def test_union_counts_overlap_once_and_charges_both_raw_prefixes(self):
        ev = index().evaluate(Counter({'weakpass': 3, 'markovxx': 2, 'sharedxx': 4, 'anotherx': 1, 'safezzzz': 5}), [1, 2, 3])
        curves = ev['attack_curves']
        self.assertEqual([p['hits'] for p in curves['union']['minauto']], [5, 9, 10])
        self.assertEqual([p['unique_guesses'] for p in curves['union']['minauto']], [2, 3, 4])
        self.assertEqual([p['charged_attempts'] for p in curves['union']['minauto']], [2, 4, 6])
        for j in range(3):
            self.assertGreaterEqual(curves['union']['minauto'][j]['hits'], max(curves[m]['minauto'][j]['hits'] for m in ('pcfg', 'markov')))
        self.assertEqual(curves['pcfg']['minauto'][-1]['unique_guesses'], 2)

    def test_markov_catches_a_password_outside_pcfg(self):
        model = index()
        self.assertEqual(model.grammar.probability('markovxx'), 0)
        self.assertEqual(model.query('markovxx')['guess_count'], 1)
        self.assertFalse(model.passes_threshold('markovxx', 3))

    def test_unhit_supported_and_outside_both_models_are_distinct(self):
        model = index()
        self.assertTrue(model.passes_threshold('safezzzz', 3))
        self.assertFalse(model.passes_threshold('中文之外', 3))
        self.assertEqual(model.query('safezzzz')['status'], 'beyond_tested_budget')
        with self.assertRaises(ValueError):
            model.passes_threshold('safezzzz', 4)

    def test_incomplete_prefix_fails_closed(self):
        with self.assertRaises(ValueError):
            DualAttackIndex(ToyGrammar(), {'pcfg': ['weakpass'], 'markov': ['markovxx']}, 3,
                            {'markov': {}})

    def test_one_failed_individual_rejects_whole_batch_without_notification(self):
        pop = Population(['weakpass', 'sharedxx'])
        rows = [{'index': i, 'old': a.password, 'new': new, 'status': 'changed', 'edit_cost': 1.}
                for i, (a, new) in enumerate(zip(pop.accounts, ['safezzzz', 'markovxx']))]
        out, audit = apply_response_policy(pop, rows, InterventionRisk(index(), [1, 3], 3), [1, 3], INDIVIDUAL_POLICY)
        self.assertEqual(audit['individual_passed'], 1)
        self.assertFalse(audit['accepted'])
        pop.apply(out)
        self.assertEqual(pop.ledger()['adaptive_affected'], 0)

    def test_censored_new_rank_can_prove_improvement_but_two_censored_cannot(self):
        risk = InterventionRisk(index(), [1, 3], 3)
        rows = [{'old': 'weakpass', 'new': 'safezzzz', 'status': 'changed'},
                {'old': 'safeyyyy', 'new': 'safezzzz', 'status': 'changed'}]
        self.assertEqual(strength_diagnostics(rows, risk), {'improved': 1, 'weakened': 0, 'equal': 0, 'uncomparable': 1})

    def test_response_rejects_omen_hit_and_preview_matches_execution(self):
        risk = InterventionRisk(index(), [1, 3], 3)
        pop = Population(['weakpass'])
        action = Action(Group('all', '', 'toy'), LocalRule(Rule('eight', min_length=8)), 'eight', (0,), 1)
        cfg = load_intervention_config()['response']
        responder = InterventionResponder(Counter({'weakpass': 10}), dict(cfg, security_threshold=3), risk)
        with patch('policy.intervention_response._propose', return_value='markovxx'):
            preview = responder.preview(pop, action, 42, 'paired')
            actual = responder.respond(pop, action, 42, 'paired')
        self.assertEqual(preview[0]['new'], actual[0]['new'])
        self.assertTrue(risk.passes_threshold(actual[0]['new']))
        self.assertGreater(actual[0]['strength_rejections'], 0)
        self.assertEqual(pop.ledger()['adaptive_affected'], 0)

    def test_gate_checks_all_prediction_repeats(self):
        cfg = load_intervention_config()
        cfg['budgets'], cfg['risk_budget'] = [1, 3], 3
        pop = Population(['weakpass']*10)
        action = Action(Group('all', '', 'toy'), LocalRule(Rule('eight', min_length=8)), 'eight', (0,), 10)
        class Responder:
            def respond(self, population, action, seed, stream):
                return [{'index': 0, 'old': 'weakpass', 'new': 'markovxx' if stream.endswith('-0') else 'safezzzz', 'status': 'changed'}]
        prediction = predict_action(pop, action, InterventionRisk(index(), [1, 3], 3), Responder(), cfg, 1)
        self.assertFalse(prediction['aggregate_guard_feasible'])

    def test_default_presets_use_actual_dual_attack_and_million_threshold(self):
        defaults = validate_intervention_config({})
        self.assertEqual(defaults['attack_models']['mode'], 'pcfg-omen-prefix')
        self.assertEqual(defaults['controller']['execution_policy'], INDIVIDUAL_POLICY)
        for preset in ('intervention_smoke', 'intervention_full'):
            cfg = load_intervention_config(preset)
            self.assertEqual(cfg['attack_models']['mode'], 'pcfg-omen-prefix')
            self.assertEqual(cfg['attack_models']['threshold'], 1000000)
            self.assertEqual(cfg['controller']['execution_policy'], INDIVIDUAL_POLICY)
        cfg = copy.deepcopy(cfg)
        cfg['attack_models']['mode'] = 'pcfg-mc'
        with self.assertRaises(ValueError):
            validate_intervention_config(cfg)

    def test_matched_controls_notify_exact_schedule_even_without_shape_gain(self):
        cfg = load_intervention_config()
        cfg['data']['users'] = 20
        cfg['budgets'], cfg['risk_budget'] = [1, 3], 3
        cfg['attack_models']['threshold'] = 3
        cfg['controller'].update(round_fraction=.2, total_fraction=.4,
                                 min_batch_fraction=.05, policy_floor_minimum_length=8,
                                 prediction_repeats=1, validation_repeats=1, validation_shortlist=1)
        pop = Population(['safezzzz']*20)
        risk = InterventionRisk(index(), [1, 3], 3)
        responder = InterventionResponder(Counter({'safezzzz':20}), dict(cfg['response'], security_threshold=3), risk)
        # Shape ties are allowed for budget-matched controls, unlike dynamic
        # selection. No notification is silently dropped to create improvement.
        with patch('policy.intervention_controller.log_cdf_area', return_value=0.), \
             patch('policy.intervention_controller.fitted_ideal_distance', return_value={'score': 0.}):
            for method in ('google_random', 'google_frozen'):
                arm, _, _ = run_arm(method, pop, ['safezzzz']*20, risk, responder, cfg,
                                    round_targets=[3, 2])
                self.assertEqual([r['action']['selected'] for r in arm['rounds']], [3, 2])
                self.assertEqual(arm['final']['ledger']['adaptive_affected'], 5)
                self.assertTrue(all(r['aggregate_attack_guard']['individual_passed'] == r['changed'] for r in arm['rounds']))

    def test_pipeline_exports_dual_curves_without_private_passwords(self):
        cfg = load_intervention_config()
        cfg['data']['users'] = 20
        cfg['budgets'], cfg['risk_budget'] = [1, 3], 3
        cfg['attack_models']['threshold'] = 3
        cfg['controller'].update(round_fraction=.1, total_fraction=.1,
                                 prediction_repeats=1, validation_repeats=1,
                                 validation_shortlist=1, lookahead_width=1)
        target = ['weakpass']*12 + ['markovxx']*8
        training = Counter({'sharedxx': 20, 'anotherx': 10})
        dataset = {'cohorts': [target], 'development': {'train': training},
                   'metadata': {'fixture': 'distinct-target-and-development'}}
        with tempfile.TemporaryDirectory() as directory, patch(
                'experiments.intervention_pipeline.make_index', side_effect=lambda *_: index()) as fit:
            report = run_intervention_pipeline(cfg, dataset=dataset, index=index(), output_dir=directory)
            self.assertTrue(fit.called)
            for call in fit.call_args_list:
                self.assertNotIn('weakpass', call.args[0])
                self.assertNotIn('markovxx', call.args[0])
            from tools.run_published_intervention import audit_report
            self.assertTrue(audit_report(report)['passed'])
            public = json.dumps(report)
            for word in target:
                self.assertNotIn('"'+word+'"', public)
            html = (Path(directory)/'report.html').read_text(encoding='utf-8')
            for name in ('attack_F', 'attack_A1', 'attack_markov_F', 'attack_markov_A1',
                         'attack_union_F', 'attack_union_A1', 'yahoo_attack_F', 'yahoo_attack_A1'):
                self.assertTrue((Path(directory)/(name+'.svg')).is_file(), name)
            for anchor in ('intervention-overview', 'intervention-rounds', 'yahoo_control',
                           'ideal_distribution_reference', 'distribution-fit', 'round_parameters'):
                self.assertIn('id="'+anchor+'"', html)
            self.assertIn('最多 2B', html)
            self.assertIn('class="plot-distinct"', html)
            self.assertNotIn('F 模型估计猜中比例', html)


if __name__ == '__main__':
    unittest.main()
