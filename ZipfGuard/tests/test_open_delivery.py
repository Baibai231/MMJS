import copy
import itertools
import json
import unittest
import tempfile
import sys
import subprocess
from pathlib import Path
from unittest.mock import patch
from core.corpus import dataset_from_counts
from core.open_attack import OpenNgram,consume
from experiments.open_config import validate_open_config
from experiments.open_pipeline import run_open_pipeline
from policy.open_policy import Rule,discover_rules
from test_open_protocol import fixture,small_config
from tools.run_open_study import build_plan
from web.presentation import report_html,plot
from web.open_presentation import render_open_markdown


class OpenDeliveryTests(unittest.TestCase):
    def test_ngram_astar_matches_exhaustive_probability_and_policy_support(self):
        model=OpenNgram({'aB2':5,'Ba2':3,'222':1},3,.1)
        limits={'timeout_seconds':10,'max_frontier':10000,'max_expansions':10000}
        cfg={'min_length':1,'max_length':4,'required_classes':3}
        actual=list(model.generate(cfg,limits));rule=Rule('classes',classes=3)
        expected={''.join(x) for length in range(1,5) for x in itertools.product('aB2',repeat=length) if rule.accepts(''.join(x))}
        self.assertEqual(set(actual),expected)
        losses=[model.nll({w:1})*(len(w)+1) for w in actual]
        self.assertTrue(all(a<=b+1e-10 for a,b in zip(losses,losses[1:])))

    def test_unicode_digit_class_pruning_preserves_eligible_candidates(self):
        model=OpenNgram({'a²':4},2,.1)
        cfg={'min_length':2,'max_length':2,'required_classes':3}
        limits={'timeout_seconds':5,'max_frontier':1000,'max_expansions':1000}
        self.assertIn('a²',list(model.generate(cfg,limits)))

    def test_nested_config_and_empty_names_are_rejected_clearly(self):
        for mutate in (lambda c:c['generation'].update(typo=1),lambda c:c['search']['requirements'][0].update(name=''),
                       lambda c:c['data'].update(encoding='not-an-encoding'),lambda c:c.update(budgets='10')):
            cfg=small_config();mutate(cfg)
            with self.assertRaises(ValueError):validate_open_config(cfg)

    def test_matched_discovery_ablation_has_equal_candidate_count(self):
        cfg=small_config();a,ma=discover_rules(fixture(),cfg)
        cfg['discovery']['mode']='none';b,mb=discover_rules(fixture(),cfg)
        self.assertEqual(len(a),len(b));self.assertEqual(len(ma['selected_features']),len(mb['selected_features']))
        self.assertEqual([r.summary() for r in a if r.origin=='common'],[r.summary() for r in b if r.origin=='common'])

    def test_incomplete_attack_cannot_be_recommended(self):
        cfg=small_config();dataset=dataset_from_counts(fixture())
        def incomplete(*args):return [consume('frequency',['outside'],budget=20,raw_limit=1,timeout_seconds=1)]
        with patch('experiments.open_pipeline.AttackEngine.attack',side_effect=incomplete):
            result=run_open_pipeline(cfg,dataset=dataset)
        self.assertTrue(all(r['status']=='no_feasible_policy' for r in result['recommendations']))
        self.assertTrue(all(r['risk'] is None for r in result['validation_candidates']))

    def test_test_constraint_failure_overrides_improvement_label(self):
        cfg=small_config();req=copy.deepcopy(cfg['search']['requirements'][0]);req['max_risk']=0
        forced=[{'requirement':req,'status':'selected','policy_name':'baseline','comparator_name':'baseline',
                 'validation_risk':0,'validation_cost':0}]
        with patch('experiments.open_pipeline.choose',return_value=forced):
            result=run_open_pipeline(cfg,dataset=dataset_from_counts(fixture()))
        self.assertEqual(result['recommendations'][0]['status'],'test_constraints_not_met')
        self.assertFalse(result['recommendations'][0]['test_requirements_met'])

    def test_shared_rendering_escapes_labels_and_preserves_protocol(self):
        cfg=small_config();cfg['search']['requirements'][0]['name']='<script>alert(1)</script>|x'
        result=run_open_pipeline(cfg,dataset=dataset_from_counts(fixture()))
        page=report_html(result);markdown=render_open_markdown(result)
        self.assertNotIn('<script>alert(1)</script>',page)
        self.assertIn('&lt;script&gt;',page);self.assertIn('open-minauto-v1',markdown)
        self.assertIn('\\|x',markdown)
        self.assertIn('没有满足约束的策略',page) if all(r['status']=='no_feasible_policy' for r in result['recommendations']) else None

    def test_plot_does_not_connect_across_incomplete_budget(self):
        page=plot([('run',[(1,.1),(2,None),(3,.5)])],xlabel='K',ylabel='risk')
        self.assertEqual(page.count('<polyline'),2);self.assertEqual(page.count('<circle'),2)
        self.assertNotIn('None',page)

    def test_study_freezes_requested_conditions_and_does_not_mutate_input(self):
        cfg=small_config();original=copy.deepcopy(cfg)
        plan=build_plan(cfg,[1,2],['R0','R2'],[1,5],['distribution','none'])
        self.assertEqual(len(plan['jobs']),16);self.assertEqual(cfg,original)
        self.assertTrue(plan['frozen_before_data_access'])
        with self.assertRaises(ValueError):build_plan(cfg,[1],['R0'],[999],['distribution'])

    def test_pcfg_unicode_line_characters_do_not_split_candidates(self):
        from ai.pcfg_adapter import PCFGAttacker,PCFGConfig
        obj=PCFGAttacker(PCFGConfig.workspace_default(generation_limit=10))
        word='abc\u0085def'
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(obj,'_ensure_available'),patch.object(obj,'_prepare_backend',return_value=Path(tmp)), \
             patch.object(obj,'_train'),patch.object(obj,'_run',return_value=subprocess.CompletedProcess([],0,word+'\n','Starting to generate password guesses')):
            stream,meta=obj.generate_open(['training'])
        self.assertEqual(stream,(word,));self.assertEqual(meta['upstream_raw_count'],1)

    def test_pcfg_invalid_utf8_is_error_not_silent_replacement(self):
        from ai.pcfg_adapter import PCFGAttacker,PCFGConfig,PCFGGenerationError
        obj=PCFGAttacker(PCFGConfig.workspace_default())
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PCFGGenerationError):
                obj._run(Path(tmp),[sys.executable,'-c',"import sys;sys.stdout.buffer.write(bytes([255]))"],
                         error_type=PCFGGenerationError,operation='test')

    def test_streamlit_open_path_and_failure_clear_results(self):
        try:from streamlit.testing.v1 import AppTest
        except ImportError:self.skipTest('optional Streamlit missing')
        root=Path(__file__).resolve().parents[1]
        app=AppTest.from_file(str(root/'web/app.py'),default_timeout=60).run()
        cfg=small_config()
        next(x for x in app.text_area if x.label.startswith('完整配置')).set_value(json.dumps(cfg))
        with patch('experiments.open_pipeline.load_corpus',return_value=dataset_from_counts(fixture())):
            app.button[0].click().run()
        self.assertFalse(app.exception);self.assertEqual(app.session_state['result']['protocol_id'],'open-minauto-v1')
        with patch('experiments.open_pipeline.load_corpus',side_effect=ValueError('input unavailable')):
            app.button[0].click().run()
        self.assertFalse(app.exception);self.assertTrue(app.error)
        self.assertNotIn('result',app.session_state)

if __name__=='__main__':unittest.main()
