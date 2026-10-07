import unittest
from collections import Counter
from unittest.mock import patch

from core.intervention_attack_area import (area_change, curve_area, guard_batch,
                                         log_budget_weights, strength_diagnostics)
from core.intervention_risk import InterventionRisk
from core.intervention_state import Population
from experiments.intervention_config import load_intervention_config
from experiments.intervention_pipeline import run_arm, reference_replay
from policy.intervention_controller import predict_action
from policy.local_actions import Action, Group, LocalRule, MultiAction
from policy.open_policy import Rule


class Index:
    n, seed = 1000, 42
    class GrammarMeta:
        metadata = {'source': 'exact ranks for population-area tests'}
    grammar = GrammarMeta()
    ranks = {'weakpass': 1, 'hardpass': 100, 'hardpassX': 100, 'midpassXX': 10,
             'weakpassX': 1}

    def query(self, word, rule=None):
        rank = self.ranks.get(word)
        return {'guess_count': rank, 'status': 'estimated' if rank is not None else 'outside_model_support'}


def row(i, old, new):
    return dict(index=i, old=old, new=new, status='changed', edit_cost=.1)


class PopulationAreaTests(unittest.TestCase):
    def setUp(self):
        self.risk = InterventionRisk(Index(), [1, 10, 100], 100)
        self.cfg = load_intervention_config()
        self.cfg['data']['users'] = 2
        self.cfg['budgets'] = [1, 10, 100]
        self.cfg['risk_budget'] = 100
        self.cfg['controller'].update(round_fraction=1., total_fraction=1., batch_fractions=[1.],
                                     policy_floor_minimum_length=8)
        self.cfg['evaluation']['adaptive'] = False

    def test_area_is_normalized_on_log_budget_and_matches_curve(self):
        self.assertEqual(log_budget_weights((1, 10, 100)), (.25, .5, .25))
        weights = log_budget_weights((1, 10, 1000))
        self.assertAlmostEqual(weights[0], 1/6)
        self.assertAlmostEqual(weights[-1], 1/3)
        self.assertAlmostEqual(curve_area(self.risk.evaluate(Counter(weakpass=1, hardpass=1))), .625)

    def test_population_improves_even_when_one_person_weakens(self):
        population = Population(['weakpass', 'hardpass'])
        proposals = [row(0, 'weakpass', 'hardpassX'), row(1, 'hardpass', 'midpassXX')]
        outcomes, audit = guard_batch(population, proposals, self.risk, self.cfg['budgets'])
        self.assertTrue(audit['accepted'])
        self.assertAlmostEqual(audit['proposed_area_gain'], .125)
        self.assertEqual(strength_diagnostics(outcomes, self.risk),
                         dict(improved=1, weakened=1, equal=0, uncomparable=0))
        before = curve_area(self.risk.evaluate(population.counts()))
        population.apply(outcomes)
        after = curve_area(self.risk.evaluate(population.counts()))
        self.assertAlmostEqual(before-after, audit['realized_area_gain'])
        self.assertEqual(population.ledger()['changed'], 2)

    def test_increased_or_equal_area_rejects_edits_but_charges_notifications(self):
        for new in ('weakpassX', 'hardpassX'):
            population = Population(['hardpass'])
            proposals = [row(0, 'hardpass', new)]
            outcomes, audit = guard_batch(population, proposals, self.risk, self.cfg['budgets'])
            self.assertFalse(audit['accepted'])
            self.assertEqual(proposals[0]['new'], new)  # Preview data was not mutated.
            population.apply(outcomes)
            self.assertEqual(population.counts(), {'hardpass': 1})
            self.assertEqual(population.ledger()['adaptive_affected'], 1)
            self.assertEqual(population.ledger()['changed'], 0)

    def test_uncovered_mass_is_not_treated_as_free_improvement(self):
        population = Population(['weakpass'])
        outcomes, audit = guard_batch(population, [row(0, 'weakpass', 'unknownxx')],
                                     self.risk, self.cfg['budgets'])
        self.assertGreater(audit['proposed_area_gain'], 0)
        self.assertFalse(audit['accepted'])
        self.assertEqual(outcomes[0]['new'], 'weakpass')

    def test_prediction_keeps_distribution_score_and_uses_area_as_separate_gate(self):
        population = Population(['weakpass', 'hardpass'])
        action = Action(Group('all', '', 'both'), LocalRule(Rule('long', min_length=9)),
                        'longer', (0, 1), 2)
        class Responder:
            def respond(self, *args):
                return [row(0, 'weakpass', 'hardpassX'), row(1, 'hardpass', 'midpassXX')]
        with patch('policy.intervention_controller.fitted_log_cdf_area', return_value={'score': .4}):
            prediction = predict_action(population, action, self.risk, Responder(), self.cfg, 1,
                                        baseline_fit={'score': .5})
        self.assertTrue(prediction['feasible'])
        self.assertAlmostEqual(prediction['score'], .1)
        self.assertAlmostEqual(prediction['predicted_F_log_area_gain'], .125)

    def test_execution_gate_preserves_state_and_logs_rejected_batch(self):
        population = Population(['hardpass', 'hardpass'])
        self.cfg['controller'].update(round_fraction=.5, total_fraction=.5)
        action = Action(Group('all', '', 'one'), LocalRule(Rule('long', min_length=9)),
                        'longer', (0,), 2)
        class Responder:
            def respond(self, population, action, *args):
                return [row(i, population.accounts[i].password, 'weakpassX') for i in action.indices]
        with patch('experiments.intervention_pipeline.select_action', return_value=((action, {}), [])):
            report, counts, _ = run_arm('google_dynamic', population, ['hardpass']*2,
                                       self.risk, Responder(), self.cfg)
        self.assertEqual(counts, {'hardpass': 2})
        self.assertEqual(len(report['rounds']), 1)
        record = report['rounds'][0]
        self.assertFalse(record['aggregate_attack_guard']['accepted'])
        self.assertEqual(record['changed'], 0)
        self.assertEqual(record['strength_improved'], 0)
        self.assertEqual(report['final']['ledger']['adaptive_affected'], 1)

    def test_reference_combination_checks_whole_batch_not_individual_fragments(self):
        reference = Population(['weakpass', 'hardpass'])
        parts = tuple(Action(Group('hot', word, word), LocalRule(Rule('long', min_length=9)),
                             'longer', (i,), 1) for i, word in enumerate(['weakpass', 'hardpass']))
        class Responder:
            rank_model = self.risk
            def respond(self, population, action, *args):
                return [row(i, population.accounts[i].password,
                            'hardpassX' if population.accounts[i].password == 'weakpass' else 'midpassXX')
                        for i in action.indices]
        ledger = reference_replay(reference, MultiAction(parts), 2, Responder(), self.cfg, 1)
        self.assertTrue(ledger['aggregate_attack_guard']['accepted'])
        self.assertEqual(reference.counts(), {'hardpassX': 1, 'midpassXX': 1})


if __name__ == '__main__':
    unittest.main()
