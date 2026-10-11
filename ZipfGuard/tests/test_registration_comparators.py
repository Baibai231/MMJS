from collections import Counter
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch
from policy.registration_joint import Preferences
from policy.registration_comparators import (CompositionRule, PopularityOracle, MaloneMaher,
                                             simulate_online, exact_rule)


class ComparatorTests(unittest.TestCase):
    def test_github_disjunction_and_composition_preserved(self):
        base = CompositionRule(github=True)
        for word, accepted in [('abcdefgh', False), ('abcdefg1', True), ('ABCDEFG1', False),
                               ('A'*15, True), ('12345678', False), ('abcdef1', False)]:
            self.assertEqual(base.accepts(word), accepted)
        rebuilt = exact_rule(base, Counter({'abcdefg1': 3}), 1)
        self.assertTrue(rebuilt.github)
        self.assertFalse(rebuilt.accepts('ABCDEFG1'))
        self.assertFalse(rebuilt.accepts('abcdefg1'))

    def test_popularity_read_is_pure_and_only_completed_update(self):
        p = PopularityOracle(CompositionRule(), .1, Counter({'abcdefgh': 3, 'otherpass': 17}))
        self.assertFalse(p.decide('abcdefgh'))
        self.assertFalse(p.decide('abcdefgh'))
        self.assertEqual(p.total, 20)
        self.assertTrue(p.decide('novelpass'))
        p.commit(None)
        self.assertEqual(p.total, 20)
        p.commit('novelpass')
        self.assertEqual(p.total, 21)

    def test_malone_new_string_update_and_invalid_proposal(self):
        p = MaloneMaher(CompositionRule(), {'abcdefgh': 5})
        p.begin('user', 11)
        self.assertTrue(p.decide('newpass1'))
        self.assertEqual(p.counts['newpass1'], 1)
        self.assertFalse(p.decide('short'))
        self.assertEqual(p.counts['short'], 1)
        self.assertEqual(p.proposals_seen, 7)
        self.assertEqual(p.reference, 'abcdefgh')

    def test_malone_reference_same_word_postincrement(self):
        p = MaloneMaher(CompositionRule(), {'abcdefgh': 5})
        p.begin('user', 11)
        self.assertTrue(p.decide('abcdefgh'))
        self.assertEqual(p.counts['abcdefgh'], 6)

    def test_online_no_summary_requery(self):
        p = MaloneMaher(CompositionRule())
        records = [('one', 'abcdefgh'), ('two', 'ijklmnop')]
        result = simulate_online(records, p, Preferences(['abcdefgh']), 11)
        self.assertEqual(result['summary']['completed_users'], 2)
        self.assertEqual(p.proposals_seen, 2)
        self.assertEqual(sum(p.counts.values()), 2)

    def test_attack_budget_is_total_and_duplicate_rank_charged(self):
        from experiments.registration_comparison import score_counts
        prefixes = {'pcfg': {'ranks': {'abcdefgh': 3}}, 'omen': {'ranks': {'ijklmnop': 2}}}
        counts = Counter({'abcdefgh': 2, 'ijklmnop': 1})
        self.assertEqual(score_counts(counts, prefixes, [4, 6], 3), [1/3, 1])

    def test_risk_selective_cap_counts_all_but_rejects_only_predicted_hits(self):
        from experiments.registration_risk_cap import RiskSelectiveOracle
        prefixes = {'pcfg': {'ranks': {'abcdefgh': 1}}, 'omen': {'ranks': {}}}
        p = RiskSelectiveOracle(CompositionRule(), .01, prefixes, 100,
                                {'abcdefgh': 5, 'ijklmnop': 15})
        self.assertFalse(p.decide('abcdefgh'))
        self.assertTrue(p.decide('ijklmnop'))
        self.assertEqual(p.total, 20)
        p.commit('ijklmnop')
        self.assertEqual(p.total, 21)
        self.assertEqual(p.counts['ijklmnop'], 16)
        self.assertTrue(p.decide('newpass1'))

    def test_joint_support_does_not_confuse_pcfg_outside_with_both_outside(self):
        from experiments.registration_risk_cap import joint_outside_fraction
        grammar = unittest.mock.Mock()
        grammar.probability.return_value = 0
        prefixes = {'omen': {'metadata': {'alphabet': 'abc123', 'min_length': 3, 'max_length': 19}}}
        self.assertEqual(joint_outside_fraction(Counter({'abc123': 2}), grammar, prefixes, 2), 0)
        self.assertEqual(joint_outside_fraction(Counter({'x'*20: 2}), grammar, prefixes, 2), 1)

    def test_counterfactual_confirms_combined_reselection(self):
        from experiments.registration_comparison import select_counterfactual
        class Response:
            def proposals(self, original, identifier, seed):
                yield original
                yield 'safe_word1'
            def simulate(self, records, rule, seed):
                outputs, users, attempts = Counter(), [], []
                for i, w in records:
                    for a, p in enumerate(self.proposals(w, i, seed)):
                        if rule.accepts(p):
                            outputs[p] += 1
                            users.append(p)
                            attempts.append(a)
                            break
                n = len(records)
                return {'counts': outputs, 'users': users, 'attempts': attempts,
                        'summary': {'initial_users': n, 'pending_users': 0,
                        'modification_rate': sum(a > 0 for a in attempts)/n,
                        'mean_extra_submissions': sum(attempts)/n,
                        'more_than_three_fraction': 0}}
        grammar = unittest.mock.Mock()
        grammar.probability.return_value = 1
        cfg = {'preview_users': 4, 'budgets': [100, 1000000], 'candidate_sizes': [100],
               'cf_min_gain': .0005, 'outside_increase': .02, 'small_budget_increase': .002,
               'cost_limits': {'literature': {'max_modification': 1, 'max_extra': 2,
                                              'max_more_than_three': .05}}}
        records = [(i, 'abcdefgh') for i in range(4)]
        data = {'roles': {'preview_select': records, 'preview_confirm': records}}
        prefixes = {'pcfg': {'ranks': {'abcdefgh': 1}}, 'omen': {'ranks': {}}}
        test_root = Path(__file__).resolve().parents[1]/'tmp/registration_comparator_tests'
        test_root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=test_root) as folder, patch('experiments.registration_comparison.preview_records', return_value=records):
            base = CompositionRule()
            chosen, decision = select_counterfactual(base, base, Counter(), Counter({'abcdefgh': 4}),
                [], data, Response(), prefixes, grammar, cfg, 'literature', 11, 1, 4, Path(folder)/'audit.json')
            self.assertFalse(chosen.accepts('abcdefgh'))
            self.assertTrue(chosen.accepts('safe_word1'))
            self.assertTrue(decision['status'].startswith('confirmed_'))


if __name__ == '__main__':
    unittest.main()
