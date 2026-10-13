import unittest
from collections import Counter

from experiments.registration_exchange_pareto import PreviewReplay, pareto_frontier, diverse, generate_candidates, rule_summary
from experiments.registration_joint_pilot import GradedRule
from experiments.registration_variable_scope import UnlimitedPreferences
from policy.registration_joint import JointRule


def point(name, risk, attempts, edits):
    return {'roles':[name], 'family':'release', 'frozen_rate':risk,
            'preview':{'summary':{'mean_extra_submissions':attempts,'mean_edit_distance':edits}}}


class ParetoExchangeTests(unittest.TestCase):
    def test_cached_rule_summary_is_identical_to_original_evidence_summary(self):
        for rule in [JointRule(), GradedRule(soft_shapes=frozenset({'L8'}),
                                            guessed=frozenset({'abcdefgh','abc12345'})),
                     GradedRule(shapes=frozenset({'D8'}))]:
            self.assertEqual(rule_summary(rule),rule.summary())
            self.assertEqual(rule_summary(rule),rule.summary())

    def test_cached_replay_matches_uncached_policy_independent_response(self):
        records=[(i,w) for i,w in enumerate(['abcdefgh','abcd1234','abcd','12345678']*8)]
        response=UnlimitedPreferences(['abcdefgh','abcd1234','abcd','12345678'])
        replay=PreviewReplay(records,response,11)
        rules=[JointRule(shapes=frozenset({'L8'})),JointRule(),
               JointRule(shapes=frozenset({'L4D4','D8'})),JointRule(shapes=frozenset({'L8'}))]
        for rule in rules:
            actual=replay.simulate(rule)
            reference=response.simulate(records,rule,11,distances=True)
            self.assertEqual(actual,reference)

    def test_pareto_retains_edit_tradeoff_and_removes_dominated_point(self):
        low_risk=point('risk',.01,2,5)
        low_edits=point('edits',.03,1,3)
        dominated=point('dominated',.04,2,4)
        self.assertEqual(pareto_frontier([low_risk,low_edits,dominated]),[low_risk,low_edits])
        self.assertEqual(diverse([low_risk,low_edits,dominated],2),[low_risk,low_edits])

    def test_all_screened_rules_measure_edits_and_shortlist_passes_all_cost_gates(self):
        baseline=JointRule(common=frozenset({'forbidden'}),shapes=frozenset({'L8'}))
        response=UnlimitedPreferences(['abcdefgh','abcd1234'])
        cfg={'cost_saving_fraction':.05,'release_pool':3,'release_sizes':[1,3],
             'beam_width':2,'beam_depth':2,'exchange_seeds':2,'add_pool':3,
             'add_sizes':[1],'paired_add_sizes':[1],'edit_repair_min_support':2,'fit_shortlist':6}
        selected,screening=generate_candidates(baseline,baseline,Counter(),
            [(i,'abcdefgh') for i in range(30)],response,frozenset(),cfg,11,lambda _:None)
        self.assertTrue(all(x['cost']['mean_edit_distance'] is not None for x in screening))
        self.assertTrue(any(x['rule'].shapes < baseline.shapes for x in selected))
        self.assertTrue(all(not x['screening_failures'] for x in selected if x['family']!='anchor'))
        self.assertTrue(all(x['rule'].common==baseline.common for x in selected))
        self.assertLessEqual(len(selected),cfg['fit_shortlist'])


if __name__=='__main__':
    unittest.main()
