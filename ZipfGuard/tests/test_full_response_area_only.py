import copy
import unittest
from unittest.mock import patch

from core.intervention_acceptance import AREA_ONLY_POLICY, apply_response_policy
from core.intervention_risk import InterventionRisk
from core.intervention_state import Population
from experiments.intervention_config import load_intervention_config, validate_intervention_config
from experiments.intervention_pipeline import run_arm
from policy.intervention_fragments import Fragment, LENGTHS
from policy.intervention_response import InterventionResponder
from policy.local_actions import Action, Group, LocalRule
from policy.open_policy import Rule
from tests.test_intervention_attack_area import Index, row


class FullResponseAreaOnlyTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_intervention_config()
        self.risk = InterventionRisk(Index(), [1, 10, 100], 100)

    def test_only_area_decides_even_when_uncovered_mass_increases(self):
        p = Population(['weakpass'])
        rows, audit = apply_response_policy(p, [row(0, 'weakpass', 'unknownxx')],
                                             self.risk, [1, 10, 100], AREA_ONLY_POLICY)
        self.assertTrue(audit['accepted'])
        self.assertEqual(audit['proposed_uncovered_rate_change'], 1.)
        self.assertTrue(audit['diagnostic_warnings'])
        p.apply(rows)
        self.assertEqual(p.ledger()['changed'], p.ledger()['adaptive_affected'])

    def test_rejected_plan_does_not_notify_or_modify(self):
        for new in ('weakpassX', 'hardpassX'):
            p = Population(['hardpass'])
            rows, audit = apply_response_policy(p, [row(0, 'hardpass', new)],
                                                 self.risk, [1, 10, 100], AREA_ONLY_POLICY)
            self.assertFalse(audit['accepted'])
            p.apply(rows)
            self.assertEqual(p.counts(), {'hardpass': 1})
            self.assertEqual(p.ledger()['adaptive_affected'], 0)

    def test_config_enforces_zero_dropout(self):
        value = copy.deepcopy(self.cfg)
        value['response']['nonresponse'] = .15
        with self.assertRaisesRegex(ValueError, '必须为 0'):
            validate_intervention_config(value)

    def test_all_eighteen_fragments_complete_after_proposal_failures(self):
        originals = {1:'short', 2:'short', 3:'short', 4:'12345678', 5:'ABCDEFGH',
                     6:'abcdefgh', 7:'abcdefgh', 8:'abcdefgh', 9:'abcdefgh',
                     10:'abababab', 11:'aaabbbcc', 12:'aaaaaaaa', 13:'qwerty12',
                     14:'abcd1234', 15:'password', 16:'gitlab12', 17:'password', 18:'password'}
        response = dict(self.cfg['response'], max_attempts=1)
        for n, old in originals.items():
            fragment = Fragment(n, frozenset({'password'}) if n in (15,17,18) else frozenset(),
                                ('gitlab','devops') if n == 16 else ())
            rule = LocalRule(Rule(str(n), min_length=max(8,LENGTHS.get(n,0))), fragment=fragment)
            action = Action(Group('all','','all'), rule, fragment.label, (0,), 1)
            responder = InterventionResponder({old: 1}, response)
            with self.subTest(fragment=n), patch('policy.intervention_response._propose', return_value=old):
                record = responder.respond(Population([old]), action, 42, 'full')[0]
                self.assertEqual(record['status'], 'changed')
                self.assertNotEqual(record['new'], old)
                self.assertTrue(rule.accepts(record['new']))
                self.assertTrue(record['explicit_completion'])

    def test_notification_of_compliant_account_still_changes_password(self):
        action = Action(Group('all','','all'), LocalRule(Rule('eight',min_length=8)), 'eight', (0,), 1)
        responder = InterventionResponder({'password': 1}, self.cfg['response'])
        with patch('policy.intervention_response._propose', return_value='password'):
            record = responder.respond(Population(['password']), action, 42, 'same')[0]
        self.assertEqual(record['status'], 'changed')
        self.assertNotEqual(record['old'], record['new'])

    def test_fast_preview_preserves_every_generated_password_and_score(self):
        from policy.intervention_controller import predict_action
        p = Population(['weakpass']*20)
        action = Action(Group('all','','all'), LocalRule(Rule('long',min_length=12)), 'long', tuple(range(20)),20)
        responder = InterventionResponder({'weakpass':20,'hardpass':5},self.cfg['response'])
        full = responder.respond(p,action,42,'compare')
        fast = responder.preview(p,action,42,'compare')
        for a,b in zip(full,fast):
            self.assertEqual({k:v for k,v in a.items() if k!='edit_cost'},
                             {k:v for k,v in b.items() if k!='edit_cost'})
        first = predict_action(p,action,self.risk,responder,self.cfg,1)
        with patch.object(responder,'preview',side_effect=responder.respond):
            second = predict_action(p,action,self.risk,responder,self.cfg,1)
        self.assertEqual(first,second)

    def test_execution_keeps_no_zero_change_rounds(self):
        cfg = copy.deepcopy(self.cfg)
        cfg['data']['users'] = 2
        cfg['budgets'] = [1,10,100]
        cfg['risk_budget'] = 100
        cfg['controller'].update(round_fraction=.5,total_fraction=.5,policy_floor_minimum_length=8)
        p = Population(['hardpass']*2)
        action = Action(Group('all','','one'),LocalRule(Rule('eight',min_length=8)),'eight',(0,),2)
        class Responder:
            def respond(self, population, action, *args):
                return [row(i,population.accounts[i].password,'weakpassX') for i in action.indices]
        with patch('experiments.intervention_pipeline.select_action',return_value=((action,{}),[])):
            report,counts,_ = run_arm('google_dynamic',p,['hardpass']*2,self.risk,Responder(),cfg)
        self.assertEqual(report['stop_reason'],'execution_plan_rejected')
        self.assertEqual(report['rounds'],[])
        self.assertEqual(report['final']['ledger']['adaptive_affected'],0)
        self.assertEqual(counts,{'hardpass':2})
        self.assertEqual(len(report['unissued_proposals']),1)


if __name__ == '__main__':
    unittest.main()
