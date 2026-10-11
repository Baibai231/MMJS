"""Behavioral validity checks for the new registration protocol."""
from collections import Counter
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from policy.registration_joint import JointRule, Preferences, structure, rebuild, candidates
from experiments.registration_joint_data import partition, stream
from experiments.registration_joint_pipeline import select_rule, cost_failures, risk

TEMP_ROOT = Path(__file__).resolve().parents[1]/'tmp'/'registration_joint_tests'
TEMP_ROOT.mkdir(parents=True, exist_ok=True)


class RegistrationJointTests(unittest.TestCase):
    def test_structure_preserves_class_order_and_length(self):
        self.assertEqual(structure('summer2026!'), 'L6D4S1')
        self.assertEqual(structure('Ab12@cd'), 'U1L1D2S1L2')
        self.assertNotEqual(structure('abcd1234'), structure('1234abcd'))

    def test_hold_freezes_members_even_when_parameters_match(self):
        base = JointRule()
        old = rebuild(base, Counter({'abcdefgh': 4, 'ijklmnop': 2}), 1, 0)
        new = candidates(base, Counter({'abcdefgh': 2, 'ijklmnop': 5}), old, [0,1], [0])
        self.assertTrue(new[0][1])
        self.assertEqual(new[0][0].exact, frozenset(['abcdefgh']))
        self.assertTrue(any(r.exact == frozenset(['ijklmnop']) for r, _ in new))
        self.assertEqual(len({r.signature() for r, _ in new}), len(new))

    def test_response_uses_shared_sequence_and_first_accepted(self):
        response = Preferences(['independent', 'secondpass'], limit=20)
        sequence = list(response.proposals('abcdefgh', 'user7', 42))
        blocked = JointRule(exact=frozenset(sequence[:2]))
        result = response.simulate([('user7','abcdefgh')], blocked, 42)
        expected = next(w for w in sequence if blocked.accepts(w))
        self.assertEqual(result['users'], [expected])
        self.assertEqual(list(response.proposals('abcdefgh','user7',42)), sequence)
        self.assertEqual(response.simulate([('user7','abcdefgh')], JointRule(),42)['users'], ['abcdefgh'])

    def test_pending_stays_in_denominator_and_patience_has_named_state(self):
        response = Preferences(['123'], mode='independent', limit=5)
        rule = JointRule(minimum=100)
        result = response.simulate([('u','abc')], rule,11)
        self.assertEqual(result['summary']['initial_users'],1)
        self.assertEqual(result['summary']['pending_users'],1)
        self.assertEqual(result['counts'],{})
        self.assertEqual(result['summary']['terminal_state'],'candidate_limit_pending')
        self.assertEqual(response.simulate([('u','abc')], rule,11,patience=3)['summary']['terminal_state'], 'patience_exhausted_simulated_exit')

    def test_global_occurrence_partition_and_resume(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as folder:
            source = Path(folder)/'corpus.txt'
            source.write_bytes(b'1000 abc\n1000 abcdefgh\n1000 otherpass\n')
            cfg = {'role_sizes': {'train':100,'preview_select':50,'preview_confirm':50,'validation':200,'test':200}, 'split_seed':42}
            path = Path(folder)/'partition.gz'
            result = partition(source,cfg,path,lambda _:None)
            identifiers = [i for rows in result['roles'].values() for i,_ in rows]
            self.assertEqual(len(identifiers),len(set(identifiers)))
            self.assertEqual(result,partition(source,cfg,path,lambda _:None))
            with self.assertRaises(ValueError):
                partition(source,{**cfg,'split_seed':23},path,lambda _:None)

    def test_synthetic_stream_uses_no_replacement_and_declared_proportions(self):
        records = [(i, 'abc' if i < 100 else 'abcdefgh') for i in range(200)]
        batches = stream(records,'abrupt',42,100,10,6)
        self.assertEqual([sum(len(w)>=6 for _,w in batch) for batch in batches], [2]*5+[8]*5)
        identifiers = [i for batch in batches for i,_ in batch]
        self.assertEqual(len(identifiers),len(set(identifiers)))

    def test_cost_infeasibility_is_recorded_without_attack_fitting(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as folder:
            response = Preferences(['abcdefgh'],limit=5)
            cfg = {'ks':[0,10], 'js':[0,5], 'recent_weight':.5, 'preview_users':10,
                   'max_modification':0,'max_extra':0,'max_more_than_three':0,
                   'selection_samples':100,'objective_budget':1000000}
            attacks = unittest.mock.Mock()
            rule, decision = select_rule(JointRule(),JointRule(),Counter({'abcdefgh':10}),Counter({'abcdefgh':10}),
                [[('past','abc')]],Counter({'abcdefgh':10}),[('train','abc')],[('train','abc')],
                [('select','abc')],[('confirm','abc')],response,attacks,{'ranks':{}},cfg,42,1,'joint',10,
                Path(folder)/'audit.json',lambda _:None)
            self.assertEqual(decision['status'],'constraint_conflict_base_fallback')
            attacks.index.assert_not_called()
            self.assertTrue((Path(folder)/'audit.json').exists())

    def test_training_reweight_never_introduces_target_strings(self):
        from experiments.registration_joint_pipeline import reweighted_train
        train = [(1,'aaaaaaaa'),(2,'bbbbbb12')]
        recent = [[(123,'secret99')]]
        output = reweighted_train(train,train,recent,42,1,.5)
        self.assertTrue(set(w for _,w in output)<=set(w for _,w in train))
        self.assertNotIn('secret99',[w for _,w in output])

    def test_incremental_cost_counts_changes_to_shared_base_result(self):
        from experiments.registration_joint_pipeline import attach_base_cost
        response = Preferences(['independent'],limit=10)
        records = [('u','abcdefgh'),('v','abc')]
        baseline = response.simulate(records,JointRule(),42)
        blocked = response.simulate(records,JointRule(exact=frozenset(['abcdefgh'])),42)
        attach_base_cost(blocked,baseline)
        self.assertEqual(blocked['summary']['incremental_modification_rate'],.5)
        self.assertGreater(blocked['summary']['incremental_mean_extra_submissions'],0)
        cfg = {'cost_mode':'incremental_base','max_modification':.2,'max_extra':1000,'max_more_than_three':1}
        self.assertIn('modification_rate',cost_failures(blocked['summary'],cfg))

    def test_unknown_rank_is_not_a_strength_certificate(self):
        index = unittest.mock.Mock()
        index.query.return_value={'status':'outside_model_support','guess_count':None}
        self.assertFalse(JointRule(strength=100,index=index).accepts('unknown123'))
        index.query.return_value={'status':'low_sample_support','rank_interval95':[1,1000000]}
        self.assertFalse(JointRule(strength=100,index=index).accepts('unknown123'))

    def test_confirmation_rejects_winner_without_trying_runner_up(self):
        import json
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as folder:
            cfg = {'ks':[0,10], 'js':[0], 'recent_weight':.5, 'preview_users':20,
                   'max_modification':1, 'max_extra':1000, 'max_more_than_three':1,
                   'selection_samples':100, 'confirmation_samples':1000, 'objective_budget':1000000,
                   'min_gain':.005, 'frozen_increase':.005, 'outside_increase':.02}
            attacks = Mock()
            attacks.index.side_effect = lambda counts, samples, purpose: {'hold':counts['abcdefgh']>1,'purpose':purpose}
            def mock_risk(index, counts, budgets, total=None):
                rate = (.5 if index['hold'] else .1) if index['purpose']=='selection' else (.2 if index['hold'] else .4)
                return {'rates':{'1000000':rate},'outside_model_support_fraction':0}
            response = Preferences(['abcdefgh','secondpass'],limit=10)
            path = Path(folder)/'audit.json'
            with patch('experiments.registration_joint_pipeline.risk',side_effect=mock_risk):
                rule, decision = select_rule(JointRule(),JointRule(),Counter({'abcdefgh':10}),Counter({'abcdefgh':10}),
                    [[('past','abcdefgh')]],Counter({'abcdefgh':1}),[(1,'abcdefgh'),(2,'abcdefgh')],
                    [(1,'abcdefgh'),(2,'abcdefgh')],[(3,'abcdefgh')],[(4,'abcdefgh')],response,attacks,{'ranks':{}},
                    cfg,42,1,'joint',10,path,lambda _:None)
            self.assertEqual(decision['status'],'hold_confirmation_failed')
            self.assertFalse(rule.exact)
            self.assertEqual(sum(c.args[2]=='confirmation' for c in attacks.index.call_args_list),2)
            audit = json.loads(path.read_text('utf-8'))
            self.assertIn('confirmation_insufficient_gain',audit['confirmation']['failures'])


if __name__ == '__main__':
    unittest.main()
