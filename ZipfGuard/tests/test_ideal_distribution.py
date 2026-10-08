from collections import Counter
import copy
import gzip
import json
import math
from pathlib import Path
import random
import tempfile
import unittest

import numpy as np

from core.ideal_distribution import (distance_from_area, ideal_log_area,
                                     ideal_frequency_curve, attach_ideal_analysis, IDEAL_LABEL)
from core.intervention_distribution import log_cdf_area, fitted_ideal_distance, fitted_log_cdf_area
from core.intervention_state import Population
from core.intervention_risk import InterventionRisk
from experiments.intervention_config import load_intervention_config
from experiments.intervention_site_controls import run_yahoo_control
from policy.yahoo_japan import YahooJapanRule, ALLOWED


class IdealDistanceTests(unittest.TestCase):
    def test_closed_form_matches_independent_CDF_integral(self):
        for frequencies in ([10], [6, 3, 1], [5, 2, 1, 1, 1], [1]*10):
            n = sum(frequencies)
            empirical_x = np.log(np.arange(1, len(frequencies)+1))/math.log(n)
            ideal_x = np.log(np.arange(1, n+1))/math.log(n)
            # Integrate the step CDF absolute difference at every support boundary,
            # independently from the analytic area/moment implementation.
            grid = np.unique(np.r_[empirical_x, ideal_x])
            empirical_cdf = np.array([sum(f for f, x in zip(frequencies, empirical_x) if x <= t)/n
                                      for t in grid[:-1]])
            ideal_cdf = np.array([sum(x <= t for x in ideal_x)/n for t in grid[:-1]])
            actual = float(np.sum(np.abs(empirical_cdf-ideal_cdf)*np.diff(grid)))
            self.assertAlmostEqual(distance_from_area(log_cdf_area(frequencies), n), actual, places=13)

    def test_fixed_support_zero_only_for_singletons_and_monotone_gain(self):
        self.assertAlmostEqual(distance_from_area(log_cdf_area([1]*100), 100), 0, places=13)
        self.assertGreater(distance_from_area(log_cdf_area([100]), 100), .5)
        before, after = [50, 30, 20], [50, 29, 20, 1]
        d1 = distance_from_area(log_cdf_area(before), 100)
        d2 = distance_from_area(log_cdf_area(after), 100)
        self.assertLess(d2, d1)
        self.assertAlmostEqual(d1-d2, log_cdf_area(before)-log_cdf_area(after))
        self.assertEqual(ideal_frequency_curve(100), [(1, 1), (100, 1)])
        self.assertEqual(distance_from_area(log_cdf_area([1]), 1), 0)
        with self.assertRaises(ValueError):
            distance_from_area(0, 100)

    def test_fitted_score_preserves_common_seed_gains(self):
        counts = Counter(a=12, b=7, c=4, d=2, e=1)
        raw = fitted_log_cdf_area(counts, 42)
        distance = fitted_ideal_distance(counts, 42)
        self.assertAlmostEqual(distance['score'], raw['score']-ideal_log_area(26))
        self.assertEqual(distance, fitted_ideal_distance(counts, 42))
        self.assertEqual(distance['parameters'], raw['parameters'])

    def test_historical_extension_preserves_actions_and_attack_values(self):
        from web.intervention_presentation import chart_specs, render_intervention_html, export_intervention_figures
        path = Path(__file__).resolve().parents[1]/'published/intervention/ef77ab5e267c09c5/report.json.gz'
        if not path.is_file():
            self.skipTest('Historical published fixture not available')
        with gzip.open(path, 'rt', encoding='utf-8') as handle:
            report = json.load(handle)
        old = copy.deepcopy(report['google_round_zipf'])
        yahoo = copy.deepcopy(report['google_round_zipf']['arms']['google_hold'])
        yahoo['label'] = 'Yahoo! JAPAN 强策略（全站一次）'
        yahoo['target_audit'] = dict(required_modifications=90000, required_rate=.9,
                                    explicit_completions=100, noncompliant_remaining=0)
        yahoo['premise'] = 'Test premise'
        report['site_controls'] = {'yahoo_japan': yahoo}
        fit = copy.deepcopy(next(row for row in report['distribution_fits']['results'] if row['key'] == 'google_hold'))
        fit['key'], fit['label'] = 'yahoo_japan', yahoo['label']
        report['distribution_fits']['results'].append(fit)
        attach_ideal_analysis(report)
        current = report['google_round_zipf']
        for key, arm in old['arms'].items():
            self.assertEqual(arm['rounds'], current['arms'][key]['rounds'])
            self.assertEqual(arm['attacks'], current['arms'][key]['attacks'])
            self.assertTrue(all(s['ideal_distance']['fitted'] is not None for s in current['arms'][key]['trajectory']))
        specs = chart_specs(report)
        self.assertEqual(len(specs['attack_F'][1]), 6)
        self.assertEqual(len(specs['attack_A1'][1]), 5)
        self.assertNotIn('原始分布（无干预）', [label for label, _ in specs['attack_A1'][1]])
        self.assertEqual(len(specs['distribution_cost'][1]), 5)
        self.assertEqual(len(specs['final_distribution'][1]), 7)
        self.assertEqual(specs['final_distribution'][1][-1], (IDEAL_LABEL, [(1, 1), (100000, 1)]))
        self.assertEqual(len(specs['google_round_zipf'][1]), 2+current['experimental']['rounds_completed'])
        html = render_intervention_html(report)
        self.assertIn('Wasserstein-1', html)
        self.assertIn('Panaretos', html)
        with tempfile.TemporaryDirectory() as tmp:
            export_intervention_figures(report, Path(tmp))
            svg = (Path(tmp)/'final_distribution.svg').read_text(encoding='utf8')
            self.assertIn(IDEAL_LABEL, svg)
            self.assertIn('#333333', svg)


class YahooControlTests(unittest.TestCase):
    def test_public_visible_requirements_and_completion(self):
        rule = YahooJapanRule()
        self.assertTrue(rule.accepts('a'*15))  # No invented composition requirement.
        self.assertTrue(rule.accepts('a'*14+' '))
        self.assertTrue(rule.accepts('a'*32))
        for password in ('a'*14, 'a'*33, 'a'*14+'<', 'a'*13+'&{', 'a'*14+'中'):
            self.assertFalse(rule.accepts(password))
            self.assertTrue(rule.accepts(rule.complete(password, random.Random(42))))
        self.assertNotIn('<', ALLOWED)

    def test_whole_site_migration_is_not_local_budget_or_F_guard(self):
        class Index:
            n, seed = 1000, 42
            class GrammarMeta:
                metadata = {'source': 'constant toy ranks'}
            grammar = GrammarMeta()
            def query(self, word, rule=None):
                return {'guess_count': 1, 'status': 'estimated'}
        cfg = load_intervention_config()
        cfg['evaluation']['adaptive'] = False
        cfg['response']['nonresponse'] = 1  # Whole-site completed-control premise overrides it.
        cfg['controller']['total_fraction'] = .01
        cfg['response']['max_attempts'] = 1
        population = Population(['abc']*20+['a'*15]*5)
        control = run_yahoo_control(population, ['abc']*20, InterventionRisk(Index(), [1, 10], 10), cfg)
        self.assertEqual(control['target_audit']['required_modifications'], 20)
        self.assertEqual(control['target_audit']['noncompliant_remaining'], 0)
        self.assertEqual(control['final']['ledger']['changed'], 20)
        self.assertEqual(control['final']['ledger']['affected_rate'], .8)
        self.assertEqual(control['final']['risk']['minauto'][0]['rate'], 1)
        self.assertEqual(population.ledger()['changed'], 0)
        self.assertEqual(control['starting_state_sha256'], population.fingerprint())


if __name__ == '__main__':
    unittest.main()
