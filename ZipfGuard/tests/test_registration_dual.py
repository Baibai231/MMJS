"""Budget accounting and mandatory dual-model behavior."""
import unittest
from collections import Counter
from unittest.mock import Mock, patch
from pathlib import Path
import json
import tempfile
from experiments.registration_dual_pipeline import ActualDualIndex, DualAttackCache, risk, frozen_union, select_rule
from policy.registration_joint import JointRule, Preferences


class RegistrationDualTests(unittest.TestCase):
    def index(self):
        grammar = Mock()
        grammar.probability.side_effect = lambda w: .1 if w == 'pcfgonly' else 0
        def prefix(ranks):
            return {'ranks': ranks, 'complete': True, 'raw_emissions': 10,
                    'metadata': {'alphabet': 'abc', 'min_length': 3, 'max_length': 19}}
        return ActualDualIndex(grammar, {'pcfg': prefix({'abc': 2, 'pcfgonly': 9}),
                                         'omen': prefix({'abc': 1, 'cab': 4})}, 10)

    def test_union_counts_accounts_once_and_omen_only_hits(self):
        out = risk(self.index(), Counter({'abc': 3, 'cab': 2, 'pcfgonly': 1}), [1, 4, 10])
        self.assertEqual(out['hits'], {'1': 3, '4': 5, '10': 6})
        self.assertEqual(out['union_budget_multiplier'], 2)
        self.assertFalse(out['estimated'])

    def test_unhit_is_not_outside_both_domains(self):
        out = risk(self.index(), Counter({'bbb': 2, 'pcfgonly': 1, 'SPACE ': 3}), [4])
        self.assertEqual(out['outside_both_models_weight'], 3)
        self.assertEqual(out['outside_model_support_fraction'], .5)

    def test_incomplete_prefix_cannot_be_zero_risk(self):
        index = self.index()
        index.prefixes['omen']['complete'] = False
        with self.assertRaises(ValueError):
            ActualDualIndex(index.grammar, index.prefixes, 10)
        with self.assertRaises(ValueError):
            risk(self.index(), Counter({'abc': 1}), [11])

    def test_confirmation_builds_both_models_at_full_budget(self):
        cache = DualAttackCache.__new__(DualAttackCache)
        from collections import OrderedDict
        cache.memory = OrderedDict()
        cache.cfg = {'objective_budget': 10, 'budgets': [1, 10]}
        index = self.index()
        cache.raw = Mock(side_effect=lambda counts, budget, model: index.prefixes[model])
        cache.grammar = Mock(return_value=(index.grammar, 'key'))
        cache.log = Mock()
        cache.index(Counter({'abc': 2}), 1, 'confirmation')
        self.assertEqual([call.args[1:] for call in cache.raw.call_args_list], [(10, 'pcfg'), (10, 'omen')])
        # Same training supports separate previews without a second model fit.
        cache.index(Counter({'abc': 2}), 100, 'selection')
        self.assertEqual(cache.raw.call_count, 2)
        self.assertEqual(frozen_union(index)['ranks']['cab'], 4)

    def test_controller_keeps_hold_when_dual_confirmation_reverses_gain(self):
        cfg = {'ks': [0, 10], 'js': [0], 'recent_weight': .5, 'preview_users': 20,
               'max_modification': 1, 'max_extra': 1000, 'max_more_than_three': 1,
               'objective_budget': 1000000, 'min_gain': .005, 'frozen_increase': .005,
               'outside_increase': .02}
        attacks = Mock()
        attacks.index.side_effect = lambda counts, purpose: {'hold': counts['abcdefgh'] > 1, 'purpose': purpose}
        def dual_risk(index, counts, budgets, total=None):
            rate = (.5 if index['hold'] else .1) if index['purpose'] == 'selection' else (.2 if index['hold'] else .4)
            return {'rates': {'1000000': rate}, 'outside_model_support_fraction': 0,
                    'models': ['pcfg', 'omen'], 'estimated': False}
        temp_root = Path(__file__).resolve().parents[1]/'tmp/registration_dual_tests'
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as folder:
            path = Path(folder)/'audit.json'
            with patch('experiments.registration_dual_pipeline.risk', side_effect=dual_risk):
                rule, decision = select_rule(JointRule(), JointRule(), Counter({'abcdefgh': 10}),
                    Counter({'abcdefgh': 10}), [[('past', 'abcdefgh')]], Counter({'abcdefgh': 1}),
                    [(1, 'abcdefgh'), (2, 'abcdefgh')], [(1, 'abcdefgh'), (2, 'abcdefgh')],
                    [(3, 'abcdefgh')], [(4, 'abcdefgh')], Preferences(['abcdefgh', 'secondpass'], limit=10),
                    attacks, {'ranks': {}}, cfg, 42, 1, 'joint', 10, path, lambda m: None)
            self.assertEqual(decision['status'], 'hold_confirmation_failed')
            self.assertFalse(rule.exact)
            calls = [c for c in attacks.index.call_args_list if c.kwargs['purpose'] == 'confirmation']
            self.assertEqual(len(calls), 2)
            audit = json.loads(path.read_text('utf-8'))
            self.assertIn('confirmation_insufficient_gain', audit['confirmation']['failures'])
            self.assertFalse(audit['confirmation']['winner']['preview_actual_dual']['estimated'])


if __name__ == '__main__':
    unittest.main()
