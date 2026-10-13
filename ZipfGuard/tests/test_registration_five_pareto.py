import unittest
from copy import deepcopy
from tools.run_registration_five_pareto import online_with_edits, make_mechanism
from experiments.registration_comparison import StaticMechanism
from experiments.registration_variable_scope import UnlimitedPreferences
from policy.registration_joint import JointRule
from policy.registration_comparators import simulate_online
from policy.open_policy import edit_distance


class IntegrationTests(unittest.TestCase):
    def test_online_cost_measurement_preserves_stochastic_acceptance_and_counts(self):
        records = [(i, w) for i, w in enumerate(['short', 'abcdefgh', 'abcd1234']*12)]
        response = UnlimitedPreferences(['abcdefgh', 'abcd1234', 'short'])
        base = JointRule(common=frozenset({'abcdefgh'}))
        bootstrap = simulate_online(records[:9], StaticMechanism(base), response, 11, capture_proposals=True)
        for arm in ('schechter_strong', 'malone'):
            mechanism = make_mechanism(arm, base, bootstrap, {'schechter_rate': 1e-5})
            reference = simulate_online(records[9:], deepcopy(mechanism), response, 11)
            actual = online_with_edits(records[9:], deepcopy(mechanism), response, 11)
            self.assertEqual(actual['counts'], reference['counts'])
            self.assertEqual(actual['attempts'], reference['attempts'])
            self.assertEqual(actual['users'], reference['users'])
            expected = sum(edit_distance(w,t) for (_,w),t in zip(records[9:],actual['users']))/len(records[9:])
            self.assertEqual(actual['summary']['mean_edit_distance'], expected)

    def test_static_and_online_bootstrap_share_user_proposals_and_edit_measurement(self):
        records = [(i,w) for i,w in enumerate(['short','abcdefgh','abcd1234']*10)]
        response = UnlimitedPreferences(['abcdefgh','abcd1234','short'])
        rule = JointRule(shapes=frozenset({'L8'}))
        static = response.simulate(records,rule,11,distances=True)
        online = online_with_edits(records,StaticMechanism(rule),response,11,True)
        for field in ('counts','attempts','users'):
            self.assertEqual(static[field],online[field])
        for field in ('mean_edit_distance','mean_extra_submissions','modification_rate'):
            self.assertEqual(static['summary'][field],online['summary'][field])


if __name__ == '__main__':
    unittest.main()
