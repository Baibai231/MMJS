import copy
import unittest
from collections import Counter
from unittest.mock import patch

from ai.research_models import (DEFAULTS, ResearchModelUnavailable, generate,
                                source_status, validate_settings)
from core.open_attack import consume, evaluate_runs
from experiments.dynamic_config import load_dynamic_config, open_attack_config, validate_dynamic_config
from experiments.open_pipeline import AttackEngine, ModelFailure


class ResearchModelsTests(unittest.TestCase):
    def config(self, model='omen'):
        cfg = load_dynamic_config()
        cfg['attackers'] = {model: 'required'}
        cfg['research_models'] = copy.deepcopy(DEFAULTS)
        return validate_dynamic_config(cfg)

    def test_settings_propagate_to_attack_engine(self):
        cfg = self.config()
        attack = open_attack_config(cfg)
        self.assertEqual(attack['research_models'], cfg['research_models'])
        self.assertEqual(attack['attackers'], {'omen': 'required'})

    def test_new_model_requires_explicit_settings(self):
        cfg = self.config()
        del cfg['research_models']
        with self.assertRaises(ValueError):
            validate_dynamic_config(cfg)

    def test_invalid_architecture_is_rejected(self):
        cfg = copy.deepcopy(DEFAULTS)
        cfg['passgpt']['embedding'] = 65
        with self.assertRaises(ValueError):
            validate_settings(cfg)

    def test_passllm_source_and_missing_source_behavior(self):
        status = source_status('passllm')
        self.assertIn('source_available', status)
        self.assertIn('base_model_present', status)
        self.assertEqual(status['available'], all((status['source_available'],
                                                   status['base_model_present'],
                                                   status['pipeline_integrated'])))
        with patch('ai.research_models.source_status', return_value={'available': False}):
            with self.assertRaises(ResearchModelUnavailable):
                generate('passllm', Counter({'example': 1}), Counter(), DEFAULTS,
                         {'raw_limit': 100, 'timeout_seconds': 10}, 42)

    def test_sampling_limit_is_not_exhaustion(self):
        run = consume('passgpt', iter(['one', 'one', 'two']), budget=100,
                      raw_limit=1000, timeout_seconds=1, source_stop='raw_limit')
        self.assertFalse(run.complete_at(100))
        self.assertEqual(run.stats['duplicate_count'], 1)
        self.assertEqual(run.stats['stop_reason'], 'raw_limit')

    def test_model_support_does_not_remove_users_from_denominator(self):
        run = consume('omen', iter(['known']), budget=1, raw_limit=10,
                      timeout_seconds=1, parameters={'support': {'min_length': 3, 'max_length': 19}})
        evaluation = evaluate_runs([run], Counter({'known': 2, 'x'*25: 3}), [1])
        self.assertEqual(evaluation['minauto'][0]['rate'], .4)
        self.assertEqual(evaluation['models'][0]['support_coverage']['outside_declared_support_weight'], 3)
        self.assertFalse(evaluation['models'][0]['support_coverage']['denominator_reduced'])

    def test_a0_continues_frozen_source_beyond_f_budget(self):
        from experiments.dynamic_pipeline import _known_policy_attack
        from policy.open_policy import Rule
        frozen = consume('omen', iter(['tiny']), budget=1, raw_limit=10, timeout_seconds=1)
        rule = Rule('length8', min_length=8)
        calls = []
        def provider(actual_rule):
            calls.append(actual_rule)
            return [consume('omen', iter(['tiny', 'long-password']), budget=1,
                            raw_limit=10, timeout_seconds=1, accepts=actual_rule.accepts)]
        result, _ = _known_policy_attack([frozen], [rule], [Counter({'long-password': 3})],
                                          [1], stream_provider=provider)
        self.assertEqual(result['minauto'][0]['rate'], 1)
        self.assertEqual(calls, [rule])

    def test_policy_filter_and_model_failure_use_common_engine(self):
        import tempfile
        from policy.open_policy import Rule
        cfg = open_attack_config(self.config())
        cfg['budgets'] = [2]
        train = Counter({'train-only': 4})
        with tempfile.TemporaryDirectory() as tmp:
            engine = AttackEngine(cfg, tmp)
            with patch('ai.research_models.generate', return_value=(
                    iter(['short', 'longer-one', 'longer-one', 'longer-two']),
                    {'source_stop': 'raw_limit'})) as generator:
                result = engine.attack(train, Counter(), Rule('test', min_length=8), 'A0')[0]
                self.assertEqual(result.stats['charged_count'], 2)
                self.assertEqual(result.stats['policy_filtered_count'], 1)
                self.assertEqual(generator.call_args.args[1], train)
            engine.cache.clear()
            with patch('ai.research_models.generate', side_effect=ResearchModelUnavailable('missing')):
                with self.assertRaises(ModelFailure):
                    engine.attack(train, Counter(), None, 'F')


if __name__ == '__main__':
    unittest.main()
