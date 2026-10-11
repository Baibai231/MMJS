import unittest
import json
import tempfile
from pathlib import Path
from copy import deepcopy
import numpy as np
from core.intervention_state import Population
from experiments.contextual_config import validate_config
from experiments.contextual_pipeline import run_new
from policy.contextual_controller import LinearModel, choose, distance, features, candidates
from policy.intervention_response import IndividualThresholdUnavailable


class ToyRisk:
    budget = 1000000
    def passes_threshold(self, word, budget=None):
        return word.startswith('secure')
    def hit(self, word):
        return float(not self.passes_threshold(word))
    def detail(self, word):
        return {'guess_count': None if self.passes_threshold(word) else 1,
                'status': 'beyond_tested_budget' if self.passes_threshold(word) else 'observed'}


class ToyResponder:
    reference = {'weakpass':100}
    def __init__(self, unsafe=False, fail_execution=False):
        self.streams=[]
        self.unsafe=unsafe
        self.fail_execution=fail_execution
    def respond(self,pop,action,seed,stream):
        self.streams.append(stream)
        if self.fail_execution and ':execute:' in stream:
            raise IndividualThresholdUnavailable('test construction failure')
        rows=[]
        for i in action.indices:
            new='secureAb!987!'+str(i)+'Z'*20
            if self.unsafe:new='weakpass'
            rows.append(dict(index=i,old=pop.accounts[i].password,new=new,
                             status='changed',edit_cost=1.))
        return rows
    preview=respond


class ContextualTests(unittest.TestCase):
    def setup_case(self):
        value=validate_config({'intervention':{'data':{'users':100},'controller':{'max_rounds':2}},
                               'learning':{'shortlist':4,'reserved':1,'finalists':2,
                                           'screening_repeats':2,'validation_repeats':3,'legacy':False}})
        cfg,opt=value['intervention'],value['learning']
        cfg['controller']['policy_floor_minimum_length']=8
        return Population(['weakpass']*100),cfg,opt

    def test_config_rejects_bad_modes_and_counts_without_mutating_input(self):
        data={'learning':{'mode':'empirical'}}; old=deepcopy(data)
        validate_config(data);self.assertEqual(data,old)
        for invalid in ({'mode':'unsafe'},{'shortlist':0},{'ridge':float('nan')},
                        {'reserved':13},{'beta':-1},{'methods':['unknown']}):
            with self.assertRaises(ValueError):validate_config({'learning':invalid})

    def test_features_and_feedback_generalize_without_password_identity(self):
        pop,cfg,opt=self.setup_case();r=ToyResponder();risk=ToyRisk()
        aa=candidates(pop,risk,r,cfg,1);X=features(pop,aa,cfg,1)
        self.assertEqual(X.shape,(len(aa),35));self.assertTrue(np.isfinite(X).all())
        m=LinearModel();before=m.scores(X,0)
        m.update(X[0],1.)
        self.assertGreater(m.scores(X[:1],0)[0],before[0])
        clone=m.clone();clone.update(X[0],0.)
        self.assertEqual(m.observations,1);self.assertEqual(clone.observations,2)

    def test_preview_does_not_mutate_or_train_and_streams_are_disjoint(self):
        pop,cfg,opt=self.setup_case();m=LinearModel();r=ToyResponder()
        before=pop.account_fingerprint()
        winner,audit=choose(pop,ToyRisk(),r,cfg,opt,m,'bandit',1)
        self.assertIsNotNone(winner)
        self.assertEqual(pop.account_fingerprint(),before);self.assertEqual(m.observations,0)
        self.assertLessEqual(audit['simulation_calls'],4*2+2*3)
        self.assertTrue(any(':screen:' in s for s in r.streams))
        self.assertTrue(any(':validate:' in s for s in r.streams))
        self.assertFalse(any(':execute:' in s for s in r.streams))

    def test_unsafe_response_never_becomes_action(self):
        pop,cfg,opt=self.setup_case()
        winner,audit=choose(pop,ToyRisk(),ToyResponder(unsafe=True),cfg,opt,LinearModel(),'bandit',1)
        self.assertIsNone(winner);self.assertTrue(audit['failures'])
        self.assertEqual(pop.ledger()['adaptive_affected'],0)

    def test_execution_failure_charges_no_notifications_or_feedback(self):
        pop,cfg,opt=self.setup_case();m=LinearModel()
        summary,counts,_=run_new('bandit',pop,None,ToyRisk(),ToyResponder(fail_execution=True),
                                 cfg,opt,m,progress=lambda _:None)
        self.assertEqual(summary['ledger']['adaptive_affected'],0)
        self.assertEqual(summary['stop_reason'],'execution_construction_failed')
        self.assertEqual(m.observations,0);self.assertEqual(counts,pop.counts())

    def test_budget_gate_and_exact_fixed_risk_invariant(self):
        pop,cfg,opt=self.setup_case();m=LinearModel()
        summary,counts,_=run_new('bandit',pop,None,ToyRisk(),ToyResponder(),cfg,opt,m,progress=lambda _:None)
        self.assertEqual(sum(counts.values()),100)
        notified=summary['ledger']['adaptive_affected']
        self.assertLessEqual(notified,4);self.assertGreater(notified,0)
        self.assertEqual(summary['ledger']['adaptive_notification_events'],notified)
        self.assertEqual(m.observations,len(summary['trajectory'])-1)
        self.assertAlmostEqual(sum(n*ToyRisk().hit(w) for w,n in counts.items()),100-notified)
        self.assertLess(summary['final_distance'],summary['initial_distance'])
        self.assertEqual(pop.ledger()['adaptive_affected'],0)

    def test_strict_mode_refuses_uncertified_small_gain(self):
        pop,cfg,opt=self.setup_case();opt['mode']='strict'
        summary,_,_=run_new('bandit',pop,None,ToyRisk(),ToyResponder(),cfg,opt,LinearModel(),progress=lambda _:None)
        self.assertEqual(summary['ledger']['adaptive_affected'],0)
        self.assertEqual(summary['stop_reason'],'insufficient_evidence')

    def test_zero_distance_is_explicit_stop(self):
        _,cfg,opt=self.setup_case();pop=Population(['unique'+str(i) for i in range(100)])
        summary,_,_=run_new('bandit',pop,None,ToyRisk(),ToyResponder(),cfg,opt,LinearModel(),progress=lambda _:None)
        self.assertLessEqual(distance(pop.counts()),1e-12)
        self.assertEqual(summary['stop_reason'],'zero_initial_distance')

    def test_pipeline_writes_separate_report_and_refuses_overwrite(self):
        from tests.test_dual_attack import index
        from experiments.contextual_pipeline import run_contextual
        cfg={'intervention':{'data':{'users':100},'budgets':[1,3],'risk_budget':3,
                             'attack_models':{'threshold':3},'controller':{'max_rounds':1},
                             'evaluation':{'adaptive':False}},
             'learning':{'legacy':False,'methods':['bandit'],'warmup_rounds':0,
                         'shortlist':2,'reserved':1,'finalists':1,'validation_repeats':2}}
        data={'cohorts':[['weakpass']*100],
              'development':{'train':{'weakpass':60,'sharedxx':40},
                             'tuning':{'sharedxx':100}},'metadata':{'source':'unit fixture'}}
        root=Path(__file__).resolve().parents[1]/'tmp'/'contextual_tests'
        root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            report=run_contextual(cfg,directory,progress=lambda _:None,dataset=data,index=index())
            self.assertEqual(report['status'],'complete')
            saved=json.loads((Path(directory)/'report.json').read_text())
            self.assertIsNone(saved['arms']['bandit']['A1'])
            self.assertEqual(saved['arms']['bandit']['initial_distance'],saved['start_distance'])
            self.assertNotIn('weakpass',(Path(directory)/'report.json').read_text())
            with self.assertRaises(FileExistsError):
                run_contextual(cfg,directory,progress=lambda _:None,dataset=data,index=index())


if __name__=='__main__':unittest.main()
