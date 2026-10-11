import unittest
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from core.htpg_features import HTPGFeatureExtractor
from core.intervention_state import Population
from experiments.htpg_conditional import replay_reference_round, run_conditional
from web.htpg_paper_interface import latest_conditional, latest_figure, render_audit
from web.server import Handler


class ConditionalExperimentTests(unittest.TestCase):
    def test_missing_suggestions_keep_their_branch_and_conserve_mass(self):
        profile = Path(__file__).resolve().parents[1]/'resources'/'htpg_reference_v1.json'
        extractor = HTPGFeatureExtractor.from_profile(profile)
        initial = Counter({'pass1234': 80, 'hello123': 20, 'abc2020!': 8,
                           **{f'abc{i:03}xx': 1 for i in range(100)}})
        final, rounds = run_conditional(initial, rounds=2, extractor=extractor)
        self.assertEqual(sum(final.values()), sum(initial.values()))
        self.assertEqual(len(rounds), 2)
        self.assertTrue(any(row['suggestions_per_head_type'].get(0, 0) > 0 for row in rounds))
        self.assertTrue(all(row['transition']['two_alternative_policies'] for row in rounds))
        self.assertNotEqual(rounds[0]['fit']['alpha'], rounds[1]['fit']['alpha'])

    def test_independent_reference_replay_is_repeatable_and_keeps_accounts(self):
        words = ['head']*1000 + ['tail']*100
        a, b = Population(words, 'reference'), Population(words, 'reference')
        children = {'head': ('head-A', 'head-B')}
        first = replay_reference_round(a, children, seed=42, round_number=1)
        second = replay_reference_round(b, children, seed=42, round_number=1)
        self.assertEqual(first, second)
        self.assertEqual(a.account_fingerprint(), b.account_fingerprint())
        self.assertEqual(first['eligible_accounts'], 1000)
        self.assertEqual(sum(first['branches'].values()), 1000)
        self.assertEqual(a.counts()['tail'], 100)
        self.assertEqual(sum(a.counts().values()), 1100)
        self.assertGreater(first['branches']['first'], 0)
        self.assertGreater(first['branches']['second'], 0)

    def test_published_conditional_result_has_verifiable_ui_label(self):
        result = latest_conditional()
        if result is None:
            self.skipTest('full conditional run has not been published')
        self.assertFalse(result['full_author_reproduction_verified'])
        self.assertIn('HTPG 正文算法＋补全假设', render_audit(document=True))
        handler = object.__new__(Handler)
        handler.path = '/api/intervention/htpg/conditional'
        sent = []
        handler._send = lambda body, content_type='application/json; charset=utf-8', status=200: sent.append((status, body))
        handler.do_GET()
        self.assertEqual(sent[0][0], 200)
        self.assertIn(result['run_id'].encode(), sent[0][1])

    def test_both_figures_contain_three_comparable_curves(self):
        if latest_conditional() is None:
            self.skipTest('full conditional run has not been published')
        for name in ('conditional_distribution.svg', 'conditional_attack.svg'):
            raw = latest_figure(name)
            self.assertIsNotNone(raw)
            root = ET.fromstring(raw)
            expected = (6 if name == 'conditional_attack.svg' and
                        'A1_curves' in latest_conditional() else 3)
            self.assertEqual(len(root.findall('.//{http://www.w3.org/2000/svg}polyline')), expected)
            handler = object.__new__(Handler)
            handler.path = '/api/intervention/htpg/'+name
            sent = []
            handler._send = lambda body, content_type='application/json; charset=utf-8', status=200: sent.append((status, content_type, body))
            handler.do_GET()
            self.assertEqual(sent[0], (200, 'image/svg+xml; charset=utf-8', raw))


if __name__ == '__main__':
    unittest.main()
