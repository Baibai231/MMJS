from collections import Counter
from copy import deepcopy
import gzip
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from core.intervention_state import Population
from core.intervention_risk import InterventionRisk
from experiments.intervention_config import validate_intervention_config
from experiments.intervention_hybrid import yahoo_indices, prepare_hybrid, dynamic_config, run_hybrid, METHOD
from policy.local_actions import capacities, generate_actions
from policy.local_actions import Action, Group
from policy.intervention_response import InterventionResponder
from policy.yahoo_japan import YahooJapanRule
from tests.test_dual_attack import index


class HybridTests(unittest.TestCase):
    def setUp(self):
        self.cfg = validate_intervention_config({})
        self.cfg['data']['users'] = 100
        self.cfg['budgets'], self.cfg['risk_budget'] = [1, 3], 3
        self.cfg['attack_models']['threshold'] = 3
        self.risk = InterventionRisk(index(), [1, 3], 3)
        self.words = ['weakpass']*40 + ['abc']*30 + ['sharedxx']*25 + ['a-valid-yahoo-password!']*5

    def test_selection_uses_exact_word_frequency_and_excludes_compliant(self):
        pop = Population(self.words)
        ids = yahoo_indices(pop, .3, 42)
        self.assertEqual(len(ids), 30)
        self.assertTrue(all(pop.accounts[i].password == 'weakpass' for i in ids))
        self.assertEqual(ids, yahoo_indices(pop, .3, 42))
        self.assertEqual(len(yahoo_indices(pop, 1, 42)), 95)

    def test_stage_costs_disjoint_and_only_remaining_users_are_eligible(self):
        pop = Population(self.words)
        audit, locked = prepare_hybrid(pop, self.words, self.risk, self.cfg, .3)
        self.assertEqual(audit['strong_modified'], 30)
        self.assertEqual(audit['baseline_modified'], 30)
        self.assertEqual(pop.ledger()['changed'], 60)
        self.assertTrue(all(YahooJapanRule().accepts(pop.accounts[i].password) for i in locked))
        cfg, limits = dynamic_config(self.cfg, 100, 30)
        self.assertEqual(limits['round_cap'], 1)
        self.assertEqual(limits['total_local_cap'], 14)
        self.assertEqual(capacities(pop, cfg), 1)
        cfg['controller']['policy_floor_minimum_length'] = 8
        actions = generate_actions(pop, self.risk, cfg, catalog_reference=Counter(self.words))
        self.assertTrue(actions)
        self.assertTrue(all(set(locked).isdisjoint(a.indices) for a in actions))

    def test_budget_offset_does_not_spend_remaining_local_budget_on_yahoo(self):
        pop = Population(['short']*1000)
        audit, _ = prepare_hybrid(pop, ['weakpass']*20, self.risk, self.cfg, .3)
        cfg, limits = dynamic_config(self.cfg, 1000, audit['strong_assigned'])
        self.assertEqual(limits['round_cap'], 14)
        self.assertEqual(limits['total_local_cap'], 140)
        self.assertEqual(capacities(pop, cfg), 14)
        self.assertEqual(round(1000*cfg['controller']['total_fraction']), 440)

    def test_yahoo_responses_are_paired_and_repeat_cost_is_deduplicated(self):
        pop = Population(self.words)
        selected = yahoo_indices(pop, .3, 42)
        rule = YahooJapanRule()
        responder = InterventionResponder(Counter(self.words), self.cfg['response'])
        subset = Action(Group('all', '', 'subset'), rule, 'Yahoo', selected, len(selected))
        full_ids = tuple(i for i, a in enumerate(pop.accounts) if not rule.accepts(a.password))
        full = Action(Group('all', '', 'all'), rule, 'Yahoo', full_ids, len(full_ids))
        one = {r['index']: r['new'] for r in responder.respond(pop, subset, 42, 'yahoo-japan-target')}
        all_rows = {r['index']: r['new'] for r in responder.respond(pop, full, 42, 'yahoo-japan-target')}
        self.assertEqual(one, {i: all_rows[i] for i in selected})
        prepare_hybrid(pop, self.words, self.risk, self.cfg, .3)
        repeated = next(i for i, a in enumerate(pop.accounts) if a.google_notifications)
        account = pop.accounts[repeated]
        pop.apply([dict(index=repeated, old=account.password, new=account.password+'!',
                        status='changed', edit_cost=0.)])
        self.assertEqual(pop.ledger()['changed'], 60)
        self.assertEqual(pop.ledger()['modification_events'], 61)

    def test_reference_selection_is_independent_and_dynamic_evaluates_all_users(self):
        self.cfg['evaluation']['adaptive'] = False
        target = Population(self.words)
        reference = ['abc']*50 + ['weakpass']*10
        def fake_run(method, population, reference_words, evaluator, responder, cfg, progress=None, **kw):
            self.assertEqual(population.total, 100)
            self.assertEqual(kw['reference_initial'].total, 60)
            self.assertEqual(population.ledger()['adaptive_affected'], 30)
            ref = kw['reference_initial']
            self.assertEqual(ref.ledger()['adaptive_affected'], 18)
            self.assertEqual(sum(a.password == 'abc' for a in ref.accounts), 0)
            from experiments.intervention_pipeline import snapshot
            state = snapshot(population, evaluator, 0)
            return {'final': state, 'trajectory': [state], 'rounds': [],
                    'adaptive_reference_ledger': ref.ledger(), 'stop_label': 'test'}, population.counts(), ref.counts()
        with patch('experiments.intervention_pipeline.run_arm', side_effect=fake_run):
            arm = run_hybrid(target, reference, self.risk, self.cfg)
        self.assertEqual(arm['target_audit']['local_modified'], 0)
        self.assertEqual(arm['hybrid_design']['metric_population'], 100)
        self.assertEqual(target.ledger()['changed'], 0)

    def test_yahoo_panels_append_hybrid_without_changing_main_curve_groups(self):
        from web.intervention_presentation import chart_specs, render_intervention_html
        root = Path(__file__).resolve().parents[1]
        with gzip.open(root/'published/intervention/3a83057341104cfc/report.json.gz', 'rt', encoding='utf-8') as f:
            report = json.load(f)
        original = chart_specs(report)
        hybrid = deepcopy(report['site_controls']['yahoo_japan'])
        hybrid['label'] = 'Yahoo! 30%＋动态'
        hybrid['hybrid_design'] = dict(requested_strong_fraction=.3, remaining_accounts=70000,
                                     round_cap=1400, total_local_cap=14000, maximum_rounds=10)
        hybrid['target_audit'] = dict(strong_modified=30000, baseline_modified=10000, local_modified=500,
            total_distinct_modified=40400, total_distinct_modified_rate=.404, modification_events=40500)
        report['site_controls'][METHOD] = hybrid
        specs = chart_specs(report)
        for key in ('attack_F', 'attack_A1', 'final_distribution', 'google_round_zipf'):
            self.assertEqual(original[key], specs[key])
        self.assertEqual(len(specs['yahoo_attack_F'][1]), 6)
        self.assertEqual(len(specs['yahoo_attack_A1'][1]), 6)
        self.assertEqual(len(specs['yahoo_distribution'][1]), 5)
        self.assertNotIn('y_domain', specs['yahoo_distribution'][2])
        html = render_intervention_html(report)
        self.assertIn('id="yahoo_distribution"', html)
        self.assertIn('累计修改的不同账户', html)


if __name__ == '__main__':
    unittest.main()
