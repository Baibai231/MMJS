import unittest
from collections import Counter
from unittest.mock import patch

from core.intervention_state import distribution_summary
from experiments.cdf_fit_benchmark import intervention_round_parameter_diagnostics
from web.round_parameter_presentation import round_parameter_svg


class TestRoundParameters(unittest.TestCase):
    def test_actual_rounds_use_shared_google_start_and_existing_endpoint_fits(self):
        counts = [Counter({'a': 15, 'b': 10, 'c': 5, 'd': 5}),
                  Counter({'a': 14, 'b': 10, 'c': 6, 'd': 5}),
                  Counter({'a': 13, 'b': 10, 'c': 7, 'd': 5})]
        trajectory = [{'round': i, 'distribution': distribution_summary(row),
                       'ledger': {'affected_rate': .2+i*.01}}
                      for i, row in enumerate(counts)]
        def fitted(c, s):
            return {'sampling_fit': {'parameters': {'c': c, 's': s},
                                     'training_max_cdf_error': .01,
                                     'replication': {'mean_max_cdf_error': .02}}}
        report = {'config': {'seed': 42},
                  'google_round_zipf': {'common_google_start_verified': True,
                                        'arms': {'google_dynamic': {'trajectory': trajectory}}},
                  'distribution_fits': {'results': [
                      {'key': 'google_hold', 'status': 'complete', 'fit': fitted(.005, .3)},
                      {'key': 'google_dynamic', 'status': 'complete', 'fit': fitted(.004, .35)}]}}
        with patch('core.distribution_analysis.sampling_diagnostics',
                   return_value=fitted(.0045, .32)) as fit:
            result = intervention_round_parameter_diagnostics(report)
        self.assertEqual(fit.call_count, 1)
        self.assertEqual([row['round'] for row in result['rows']], [0, 1, 2])
        self.assertEqual([row['parameters']['c'] for row in result['rows']],
                         [.005, .0045, .004])
        self.assertEqual(result['rows'][0]['unique_passwords'], 4)
        svg = round_parameter_svg(result['rows'], requested_rounds=10)
        self.assertIn('class="parameter-c"', svg)
        self.assertIn('class="parameter-s"', svg)
        self.assertIn('stroke-dasharray="8 5"', svg)
        self.assertIn('0 · Google 起点', svg)
        self.assertIn('2 · 调整后', svg)
        self.assertNotIn('10 · 调整后', svg)


if __name__ == '__main__':
    unittest.main()
