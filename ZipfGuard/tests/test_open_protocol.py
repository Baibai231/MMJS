import copy
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from core.corpus import parse_record, split_counts, dataset_from_counts, load_corpus
from core.open_attack import consume, evaluate_runs, OpenNgram
from experiments.open_config import load_open_config, validate_open_config
from experiments.open_pipeline import run_open_pipeline, paired_comparison, choose, AttackEngine, ModelFailure
from policy.open_policy import Rule, response_for, discover_rules


def fixture():
    return {'secretALPHA1!':80,'longSecretB2!':70,'otherWords2026':60,'tiny':100,'aaaa':80,'qwerty12':90}


def small_config():
    c=load_open_config();c['budgets']=[1,5,20];c['search']['risk_budget']=5
    c['data']['sample_size']=200;c['search']['lengths']=[8];c['search']['blocklist_size']=1
    c['bootstrap_repetitions']=20;c['attackers']={'frequency':'required','dictionary-rules':'required'}
    c['discovery']['max_features']=1
    return c


class OpenProtocolTests(unittest.TestCase):
    def test_count_parser_preserves_password_spaces_and_strict_errors(self):
        self.assertEqual(parse_record(b' 12  secret \r\n','password_with_count'),(' secret ',12))
        self.assertEqual(parse_record(b'Ab C\n','raw_occurrences'),('Ab C',1))
        for raw in (b'0 x\n',b'bad\n',b'2 \n',b'2 a\tb\n'):
            with self.assertRaises(ValueError):parse_record(raw,'password_with_count')

    def test_split_conserves_occurrences_reproducible(self):
        counts=fixture();splits=split_counts(counts,42)
        self.assertEqual(splits,split_counts(counts,42))
        merged=Counter()
        for c in splits.values():merged.update(c)
        self.assertEqual(merged,counts)

    def test_full_file_sampling_merges_duplicate_rows_without_top_cutoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'test.txt';p.write_bytes(b'100 first\n100 first\n300 last\n')
            d=load_corpus(p,sample_size=500)
        self.assertEqual(d['sample_occurrences'],500)
        allcounts=Counter()
        for c in d['splits'].values():allcounts.update(c)
        self.assertEqual(allcounts,{'first':200,'last':300})
        self.assertEqual(d['metadata']['source_occurrences'],500)
        self.assertFalse(d['metadata']['global_duplicates_audited'])

    def test_billing_counts_outside_targets_and_duplicates(self):
        r=consume('test',['outside','outside','other','target'],budget=20,raw_limit=20,timeout_seconds=1)
        self.assertEqual(r.ranks['target'],3);self.assertEqual(r.raw_ranks['target'],4)
        self.assertEqual(r.stats['duplicate_count'],1)

    def test_policy_filter_is_per_knowledge(self):
        r=consume('test',['x','longword'],budget=1,raw_limit=20,timeout_seconds=1,accepts=lambda w:len(w)>=8)
        self.assertEqual(r.ranks,{'longword':1});self.assertEqual(r.stats['policy_filtered_count'],1)
        frozen=consume('test',['x','longword'],budget=2,raw_limit=20,timeout_seconds=1)
        self.assertEqual(frozen.ranks['longword'],2)

    def test_minauto_union_and_frequency_weight_not_max(self):
        a=consume('a',['x','z'],budget=1,raw_limit=10,timeout_seconds=1)
        b=consume('b',['y','z'],budget=1,raw_limit=10,timeout_seconds=1)
        p=evaluate_runs([a,b],{'x':2,'y':3,'z':5},[1])['minauto'][0]
        self.assertEqual(p['rate'],.5)

    def test_truncated_stream_is_unknown_but_hits_known(self):
        a=consume('a',['x'],budget=10,raw_limit=1,timeout_seconds=1)
        p=evaluate_runs([a],{'x':2,'y':3},[10])['minauto'][0]
        self.assertIsNone(p['rate']);self.assertEqual(p['lower_bound'],.4);self.assertEqual(p['upper_bound'],1)
        p=evaluate_runs([a],{'x':2},[10])['minauto'][0]
        self.assertEqual(p['rate'],1)

    def test_exhausted_dictionary_is_evaluable_plateau(self):
        a=consume('a',['x'],budget=10,raw_limit=100,timeout_seconds=1)
        self.assertTrue(a.complete_at(100))
        self.assertEqual(evaluate_runs([a],{'y':1},[100])['minauto'][0]['rate'],0)

    def test_ngram_independently_generates_unseen_words_in_probability_order(self):
        m=OpenNgram({'ab':4,'ba':2},2,.1)
        cfg={'min_length':1,'max_length':3};lim={'timeout_seconds':5,'max_frontier':10000,'max_expansions':10000}
        seq=list(m.generate(cfg,lim))
        self.assertEqual(len(seq),14)
        losses=[m.nll({w:1})*(len(w)+1) for w in seq]
        self.assertTrue(all(a<=b+1e-10 for a,b in zip(losses,losses[1:])))
        self.assertIn('aaa',seq)

    def test_ngram_resource_limit_not_exhaustion(self):
        m=OpenNgram({'abcdef':10},3,.1)
        cfg={'min_length':1,'max_length':32};lim={'timeout_seconds':5,'max_frontier':1,'max_expansions':1}
        r=consume('m',m.generate(cfg,lim),budget=100,raw_limit=100,timeout_seconds=5)
        self.assertIn(r.stats['stop_reason'],('memory_limit','expansion_limit'))
        self.assertFalse(r.complete_at(100))

    def test_r0_conditioning_and_costs(self):
        cfg=small_config()['response'];r=response_for({'tiny':3,'longword':1},Rule('long',8),'R0',cfg,pool={'tiny':3},seed=1,split='test')
        self.assertEqual(r.final,{'longword':1});self.assertEqual(r.summary['cost'],.75)
        self.assertEqual(r.summary['mean_extra_attempts'],3);self.assertEqual(r.summary['completion_rate'],1)
        cfg['r0_max_total_attempts']=2;r=response_for({'tiny':3,'longword':1},Rule('long',8),'R0',cfg,pool={},seed=1,split='test')
        self.assertAlmostEqual(r.summary['completion_rate'],.4375)
        self.assertAlmostEqual(r.summary['mean_extra_attempts'],.75)

    def test_r0_empty_acceptance_not_safe_recommendation(self):
        c=small_config();r=response_for({'tiny':10},Rule('impossible',64),'R0',c['response'],pool={},seed=1,split='test')
        self.assertFalse(r.summary['feasible_response']);self.assertIsNone(r.summary['mean_extra_attempts'])

    def test_repair_failure_retains_all_initial_users(self):
        c=small_config();r=response_for({'tiny':10},Rule('impossible',64),'R1',c['response'],pool={'tiny':10},seed=1,split='test')
        self.assertEqual(len(r.records),10);self.assertEqual(r.summary['completion_rate'],0)
        self.assertEqual(r.summary['mean_extra_attempts'],5)

    def test_common_random_numbers_and_response_pool_are_train_only(self):
        c=small_config();kw=dict(pool={'trainingOnly':30},seed=1,split='test')
        a=response_for({'tiny':10},Rule('a',8),'R2',c['response'],**kw)
        b=response_for({'tiny':10},Rule('b',8),'R2',c['response'],**kw)
        self.assertEqual(a.records,b.records)

    def test_discovery_uses_train_only_and_all_baselines(self):
        rules,discovery=discover_rules(fixture(),small_config())
        self.assertTrue({'baseline','length-8','complex-8','block-8'} <= {r.name for r in rules})
        self.assertEqual(discovery['fit_split'],'train')
        self.assertTrue(all(set(r.blocklist)<=set(fixture()) for r in rules))

    def test_config_rejects_unknown_budget_or_knowledge(self):
        c=small_config();c['search']['risk_budget']=999
        with self.assertRaises(ValueError):validate_open_config(c)
        c=small_config();c['knowledge']=['A2']
        with self.assertRaises(ValueError):validate_open_config(c)

    def test_pipeline_selection_ignores_test_and_public_output_hides_passwords(self):
        c=small_config();d=dataset_from_counts(fixture(),42,{'dataset_id':'fixture','synthetic':True})
        first=run_open_pipeline(c,dataset=d)
        changed=copy.deepcopy(d);changed['splits']['test']={'POISON_TEST_ONLY_987':100}
        from core.corpus import counts_hash
        changed['split_hashes']['test']=counts_hash(changed['splits']['test'])
        second=run_open_pipeline(c,dataset=changed)
        self.assertEqual(first['selection_sha256'],second['selection_sha256'])
        # Timing varies; stream identities and selected risks must not.
        for a,b in zip(first['validation_candidates'],second['validation_candidates']):
            self.assertEqual((a['risk'],a['cost'],a['policy']),(b['risk'],b['cost'],b['policy']))
            self.assertEqual([x['run']['stream_sha256'] for x in a['evaluation']['models']],
                             [x['run']['stream_sha256'] for x in b['evaluation']['models']])
        raw=json.dumps(second,ensure_ascii=False)
        for word in [*fixture(),'POISON_TEST_ONLY_987']:self.assertNotIn(json.dumps(word),raw)
        self.assertEqual(second['protocol_id'],'open-minauto-v1')

    def test_optional_failure_restarts_entire_comparison(self):
        c=small_config();c['attackers']['pcfg']='optional';d=dataset_from_counts(fixture())
        with patch('ai.pcfg_adapter.PCFGAttacker.generate_open',side_effect=RuntimeError('private password')):
            result=run_open_pipeline(c,dataset=d)
        self.assertNotIn('pcfg',result['metadata']['participating_attackers'])
        self.assertEqual(len(result['metadata']['attacker_failures']),1)
        self.assertNotIn('private password',json.dumps(result))
        c['attackers']['pcfg']='required'
        with patch('ai.pcfg_adapter.PCFGAttacker.generate_open',side_effect=RuntimeError('failure')):
            with self.assertRaisesRegex(RuntimeError,'必选'):run_open_pipeline(c,dataset=d)

    def test_no_feasible_is_explicit(self):
        c=small_config();rows=[{'evaluable':False}]
        result=choose(rows,c['search']['requirements'],3)
        self.assertTrue(all(r['status']=='no_feasible_policy' for r in result))

    def test_paired_identical_policy_has_zero_difference(self):
        c=small_config();r=response_for(fixture(),Rule('baseline'),'R0',c['response'],pool=fixture(),seed=1,split='test')
        a=consume('a',list(fixture()),budget=3,raw_limit=100,timeout_seconds=1)
        comparison=paired_comparison(r,r,[a],[a],3,40,1)
        for row in comparison['differences'].values():self.assertEqual((row['estimate'],row['lower'],row['upper']),(0,0,0))

if __name__=='__main__':unittest.main()
