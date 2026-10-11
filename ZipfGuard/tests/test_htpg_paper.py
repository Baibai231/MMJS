import json
import math
import random
import threading
import unittest
from collections import Counter
from fractions import Fraction
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from core.htpg_paper import (FEATURES, MissingPaperDefinition, append_paper_date,
    feature_sort, fit_and_classify, iterate, policies_generation,
    table_iv_length_required, transition_half_quarters)
from experiments.htpg_paper_pipeline import PaperInputs, run_paper_pipeline
from web.htpg_paper_interface import execute_contract, render_audit
from web.server import Handler


class PaperAlgorithmTests(unittest.TestCase):
    def test_exact_power_law_regression_and_independent_curvature_check(self):
        counts = [80/r**2 for r in range(1, 21)]
        fit = fit_and_classify(counts)
        self.assertAlmostEqual(fit['C'], 80, places=10)
        self.assertAlmostEqual(fit['alpha'], 2, places=10)
        self.assertEqual(fit['fit_types'], 5)
        self.assertAlmostEqual(fit['total_mass'], sum(counts), places=12)
        def curvature(x):
            first = -160/x**3
            second = 480/x**4
            return second/(1+first*first)**1.5
        peak = fit['curvature_x0']
        self.assertGreater(curvature(peak), curvature(peak*.99))
        self.assertGreater(curvature(peak), curvature(peak*1.01))
        self.assertAlmostEqual((curvature(peak+1e-4)-curvature(peak-1e-4))/2e-4, 0, places=8)
        self.assertEqual(fit['cutoff_rank'], math.floor(peak))
        self.assertEqual(fit['head_types']+fit['tail_types'], 20)

    def test_threshold_and_degenerate_fit_never_get_a_fallback(self):
        for counts in ([3, 2, 1], [9, 3, 1], [8, 8, 8]):
            with self.assertRaises(MissingPaperDefinition):
                fit_and_classify(counts)
        for counts in ([10, 20, 4], [True, 1], [10, 0], [math.inf, 4]):
            with self.assertRaises(ValueError):
                fit_and_classify(counts)
        # Two qualifying observations are sufficient for the stated regression.
        fit = fit_and_classify([20, 5, 3, 1])
        self.assertAlmostEqual(fit['alpha'], 2)

    def test_information_gain_ratio_and_undefined_zero_denominator(self):
        rows = [{'signal': x, 'constant': 7} for x in ('A', 'A', 'B', 'B')]
        result = feature_sort(rows, [True, True, False, False], weights=[1]*4,
                              feature_order=('signal', 'constant'))
        self.assertAlmostEqual(result['scores'][0]['information_gain_ratio'], 1)
        self.assertIsNone(result['scores'][1]['information_gain_ratio'])
        self.assertFalse(result['complete'])
        self.assertEqual(result['undefined_features'], ['constant'])
        self.assertEqual(result['ordered_features'], ['signal'])

    def test_weights_are_explicit_and_change_the_score(self):
        rows = [{'a': v} for v in ('x', 'x', 'y', 'y')]
        labels = [True, False, True, False]
        uniform = feature_sort(rows, labels, weights=[1]*4, feature_order=('a',))
        weighted = feature_sort(rows, labels, weights=[100, 1, 1, 100], feature_order=('a',))
        self.assertAlmostEqual(uniform['scores'][0]['information_gain_ratio'], 0)
        self.assertGreater(weighted['scores'][0]['information_gain_ratio'], .9)

    def test_missing_feature_and_empty_class_are_not_silently_skipped(self):
        with self.assertRaises(MissingPaperDefinition):
            feature_sort([{'a': 1}, {}], [True, False], weights=[1, 1], feature_order=('a',))
        with self.assertRaises(MissingPaperDefinition):
            feature_sort([{'a': 1}, {'a': 2}], [True, True], weights=[1, 1], feature_order=('a',))

    def test_algorithm_3_includes_equal_distances_and_equal_means(self):
        result = policies_generation(in_head=True, ordered_features=['length', 'capital'],
            pwd_features={'length': 6, 'capital': 0}, head_features={'length': 4, 'capital': .5},
            tail_features={'length': 8, 'capital': .5},
            policies={'length': 'length-policy', 'capital': 'capital-policy'}, m=2)
        self.assertEqual(result, ['length-policy', 'capital-policy'])

    def test_algorithm_3_does_not_backfill_or_visit_tail_features(self):
        result = policies_generation(in_head=True, ordered_features=['a', 'b', 'c'],
            pwd_features={'a': 10, 'b': 0}, head_features={'a': 0, 'b': 0},
            tail_features={'a': 10, 'b': 10}, policies={'b': 'b-policy'}, m=2)
        self.assertEqual(result, ['b-policy'])
        self.assertEqual(policies_generation(in_head=False, ordered_features=[],
            pwd_features={}, head_features={}, tail_features={}, policies={}, m=999), [])

    def test_missing_scalar_or_lowercase_policy_stops_generation(self):
        with self.assertRaises(MissingPaperDefinition) as caught:
            policies_generation(in_head=True, ordered_features=['lsd_structure'],
                pwd_features={'lsd_structure': 'L3'}, head_features={'lsd_structure': 'L3'},
                tail_features={'lsd_structure': 'L8'}, policies={'lsd_structure': 'policy'}, m=1)
        self.assertEqual(caught.exception.code, 'categorical_mean')
        with self.assertRaises(MissingPaperDefinition) as caught:
            policies_generation(in_head=True, ordered_features=['lowercase'],
                pwd_features={'lowercase': 0}, head_features={'lowercase': 0},
                tail_features={'lowercase': 1}, policies={}, m=1)
        self.assertEqual(caught.exception.code, 'missing_policy')

    def test_table_iv_length_guard_is_separate_from_algorithm_3(self):
        self.assertTrue(table_iv_length_required(6, 8))
        self.assertFalse(table_iv_length_required(8, 8))
        self.assertFalse(table_iv_length_required(10, 8))

    def test_paper_date_response_includes_endpoints_and_exact_append(self):
        class Endpoint:
            def randint(self, low, high):
                self.asserted = (low, high)
                return high
        rng = Endpoint()
        self.assertEqual(append_paper_date('abc123', rng), 'abc1232020')
        self.assertEqual(rng.asserted, (1980, 2020))
        values = {append_paper_date('x', random.Random(i)) for i in range(1000)}
        self.assertIn('x1980', values)
        self.assertIn('x2020', values)

    def test_transition_is_per_head_alternative_and_conserves_fractional_mass(self):
        after, audit = transition_half_quarters({'a': 5, 'b': 3, 'tail': 1},
                                                {'a': ('a1', 'a2'), 'b': ('b1', 'b2')})
        self.assertEqual(after['a'], Fraction(5, 2))
        self.assertEqual(after['a1'], Fraction(5, 4))
        self.assertEqual(after['b2'], Fraction(3, 4))
        self.assertEqual(after['tail'], 1)
        self.assertEqual(sum(after.values()), 9)
        self.assertEqual(audit['changed_mass'], 4)

    def test_child_collisions_are_merged_without_forcing_improvement(self):
        after, _ = transition_half_quarters({'a': 8, 'b': 4},
                                           {'a': ('b', 'b'), 'b': ('b', 'b')})
        self.assertEqual(after, {'a': 4, 'b': 8})
        self.assertEqual(sum(after.values()), 12)

    def test_iteration_rebuilds_head_set_and_allows_repeat_changes(self):
        original = Counter({'a': 80, 'b': 20, 'c': 8, **{f't{i:03}': 1 for i in range(100)}})
        calls = []
        def prepare(counts, fit, ordered):
            heads = ordered[:fit['cutoff_rank']]
            calls.append((Counter(counts), heads))
            return {w: (w+'X', w+'Y') for w in heads}
        final, history = iterate(original, rounds=2, prepare_round=prepare)
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0][0], calls[1][0])
        self.assertIn('a', calls[1][1])
        self.assertEqual(sum(final.values()), sum(original.values()))
        self.assertEqual(original['a'], 80)
        self.assertEqual(len(history), 2)

    def test_partial_round_is_rejected_instead_of_simplified(self):
        counts = {'a': 80, 'b': 20, 'c': 8, **{f't{i:03}': 1 for i in range(100)}}
        with self.assertRaises(MissingPaperDefinition):
            iterate(counts, rounds=1, prepare_round=lambda *_: {})

    def test_complete_supplied_inputs_run_through_project_evaluator(self):
        counts = Counter({'a': 80, 'b': 20, 'c': 8, **{f't{i:03}': 1 for i in range(100)}})
        ordered = sorted(counts, key=lambda w: (-counts[w], w))
        fit = fit_and_classify([counts[w] for w in ordered])
        heads = set(ordered[:fit['cutoff_rank']])
        def values(word):
            return {name: 0 if word in heads else 1 for name in FEATURES}
        inputs = PaperInputs(values, values,
            lambda *args: {name: name for name in FEATURES},
            lambda word, policies, number: (word+'X', word+'Y'), 'unique',
            {'scope': 'synthetic algorithm fixture, not author encodings'})
        class Evaluator:
            def evaluate(self, data):
                return {'rate': data.get('a', 0)/sum(data.values())}
        result = run_paper_pipeline(counts, inputs=inputs, rounds=1, evaluator=Evaluator())
        self.assertEqual(result['final']['population_mass'], sum(counts.values()))
        self.assertAlmostEqual(result['final_F_attack']['rate'], result['initial_F_attack']['rate']/2)
        self.assertFalse(result['attack_used_for_policy_decisions'])
        self.assertFalse(result['full_author_reproduction_verified'])
        self.assertEqual(result['final_counts']['a'], 40)

    def test_raw_pipeline_cannot_default_to_historical_heuristics(self):
        with self.assertRaises(MissingPaperDefinition) as caught:
            run_paper_pipeline({'a': 20, 'b': 5})
        self.assertEqual(caught.exception.code, 'paper_inputs_required')


class PaperInterfaceTests(unittest.TestCase):
    def test_missing_definition_has_no_fake_zero_score(self):
        result, status = execute_contract({'operation': 'run'})
        self.assertEqual(status, 409)
        self.assertIsNone(result['paper_result'])
        self.assertFalse(result['paper_group_was_executed'])

    def test_real_http_fit_and_strict_status_inside_existing_server(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        conn = HTTPConnection('127.0.0.1', server.server_port)
        try:
            for route in ('/intervention/htpg', '/api/intervention/htpg/status'):
                conn.request('GET', route)
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertTrue(response.read())
            conn.request('POST', '/api/intervention/htpg/compute',
                         json.dumps({'operation': 'fit', 'frequencies': [20, 5, 3, 1]}),
                         {'Content-Type': 'application/json'})
            response = conn.getresponse()
            result = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertAlmostEqual(result['result']['alpha'], 2)
            conn.request('POST', '/api/intervention/htpg/compute',
                         json.dumps({'operation': 'run'}), {'Content-Type': 'application/json'})
            response = conn.getresponse()
            self.assertEqual(response.status, 409)
            self.assertIsNone(json.loads(response.read())['paper_result'])
            conn.request('POST', '/api/intervention/htpg/compute',
                         json.dumps({'operation': 'fit', 'frequencies': [3, 2, 1]}),
                         {'Content-Type': 'application/json'})
            response = conn.getresponse()
            self.assertEqual(response.status, 422)
            self.assertEqual(json.loads(response.read())['code'], 'insufficient_fit')
        finally:
            conn.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
