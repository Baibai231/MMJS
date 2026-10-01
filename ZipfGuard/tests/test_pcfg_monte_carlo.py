import itertools
import json
import math
import tempfile
import unittest
from pathlib import Path
from ai.pcfg_monte_carlo import Grammar, MonteCarloIndex, popularity
from core.monte_carlo_attack import MCRun, evaluate_mc
from policy.open_policy import Rule
from policy.site_catalog import site_catalog, site_rules
from policy.user_response import simulate_users, weighted_pool, phrase_vocabulary


class MonteCarloTests(unittest.TestCase):
    def grammar(self):
        # 'ab' has two derivations; using maximum probability would be wrong.
        return Grammar({'A': {'a': .7, 'b': .3}, 'B': {'b': .6, 'c': .4},
                        'X': {'ab': 1.}}, [(['A', 'B'], .8), (['X'], .2)])

    def test_probability_and_exact_rank_calibration(self):
        grammar = self.grammar()
        words = ['ab', 'ac', 'bb', 'bc']
        self.assertAlmostEqual(sum(grammar.probability(w) for w in words), 1)
        self.assertAlmostEqual(grammar.probability('ab'), .536)
        index = MonteCarloIndex(grammar, 100000, 7)
        ordered = sorted(words, key=lambda w: (-grammar.probability(w), w))
        for exact, word in enumerate(ordered, 1):
            self.assertAlmostEqual(index.query(word)['guess_count'], exact, delta=.08)
        self.assertEqual(index.query('unseen')['status'], 'outside_model_support')
        self.assertIsNone(index.query('unseen')['guess_count'])

    def test_conditioned_rank_and_whole_population_denominator(self):
        index = MonteCarloIndex(self.grammar(), 100000, 7)
        rule = Rule('exclude-ab', blocklist=frozenset({'ab'}))
        self.assertIsNone(index.query('ab', rule)['guess_count'])
        self.assertAlmostEqual(index.query('bc', rule)['guess_count'], 3, delta=.1)
        run = MCRun(index, 100)
        point = evaluate_mc([run], {'ab': 3, 'unseen': 2}, [100], total=10)['minauto'][0]
        self.assertEqual(point['rate'], .3)
        self.assertEqual(point['outside_model_support_weight'], 2)
        self.assertEqual(point['pending_weight'], 5)
        self.assertFalse(point['complete'])

    def test_ties_reuse_and_serialization(self):
        grammar = Grammar({'T': {'a': .5, 'b': .5}}, [(['T'], 1.)])
        index = MonteCarloIndex(grammar, 10000, 9)
        self.assertEqual(index.query('a')['status'], 'low_sample_support')
        self.assertAlmostEqual(index.query('b')['guess_count'], 2, delta=.05)
        original_samples = dict(index.sampled_counts)
        index.query('outside')
        self.assertEqual(original_samples, index.sampled_counts)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'index.json'
            index.save(path, {'a': 10, 'b': 10, 'c': 1})
            self.assertEqual(set(json.loads(path.read_text())['sampled_probabilities']),
                             set(index.sampled_counts))
            loaded, counts = MonteCarloIndex.load(path)
            self.assertEqual(loaded.query('b'), index.query('b'))
            self.assertEqual(popularity('b', counts)['rank'], 1)
            self.assertEqual(popularity('c', counts)['rank'], 3)
            self.assertIsNone(popularity('missing', counts)['rank'])

    def test_site_conditionals_and_catalog_status(self):
        self.assertEqual(len(site_catalog()['sites']), 15)
        rules = site_rules()
        self.assertEqual(len(rules), 6)
        github = rules['site_github']
        self.assertFalse(github.accepts('abcdefgh'))
        self.assertFalse(github.accepts('ABCDEFG1'))
        self.assertTrue(github.accepts('abcdefg1'))
        self.assertTrue(github.accepts('A'*15))
        self.assertFalse(rules['site_netflix'].accepts('x'*61))
        self.assertTrue(rules['site_instagram'].accepts('x'*6))
        self.assertFalse(rules['site_instagram'].accepts('x'*5))
        counts = {'abcdefgh': 3, 'a'*61: 2}
        for rule in rules.values():
            response = simulate_users(list(counts), rule, pool=weighted_pool(counts),
                                      vocabulary=phrase_vocabulary(counts), seed=1)
            self.assertEqual(response['summary']['pending_users'], 0)
            self.assertTrue(all(rule.accepts(w) for w in response['final']))

    def test_real_upstream_and_pipeline(self):
        from core.registration import registration_from_counts
        from experiments.dynamic_config import load_dynamic_config
        from experiments.dynamic_pipeline import run_dynamic_pipeline
        from web.dynamic_presentation import render_dynamic_html
        from tools.run_dynamic_study import export_figures
        cfg = load_dynamic_config()
        cfg['data'].update(users=30, development=60, cohort_size=10)
        cfg['monte_carlo']['samples'] = 1000
        counts = {'hello123': 60, 'world456': 50, 'Hello123': 40,
                  'summer2026!': 30, 'purple77': 30}
        dataset = registration_from_counts(counts, users=30, development=60, seed=42, cohort_size=10)
        with tempfile.TemporaryDirectory() as temp:
            report = run_dynamic_pipeline(cfg, dataset=dataset, output_dir=temp)
            self.assertEqual(report['metadata']['participating_attackers'], ['pcfg'])
            self.assertEqual(len(report['controls']), 6)
            for levels in report['attacks']['by_strategy'].values():
                self.assertEqual(set(levels), {'F', 'A0', 'A1'})
                for ev in levels.values():
                    rates = [p['rate'] for p in ev['minauto']]
                    self.assertEqual(rates, sorted(rates))
                    self.assertTrue(all(p['target_weight'] == 30 for p in ev['minauto']))
            html = render_dynamic_html(report)
            self.assertIn('10¹²', html)
            self.assertIn('蒙特卡洛估计', html)
            self.assertNotIn('hello123', html)
            json.dumps(report, allow_nan=False)
            export_figures(report, Path(temp))
            self.assertTrue((Path(temp) / 'attack_budget.svg').is_file())
            index, population = MonteCarloIndex.load(Path(temp) / 'pcfg_frozen_private.json')
            self.assertEqual(sum(population.values()), 30)


if __name__ == '__main__':
    unittest.main()
