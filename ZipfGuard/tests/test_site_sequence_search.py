"""Check the exact collision calculation used by the path screen."""
import unittest
from collections import Counter

from tools.search_site_sequences import (LENGTH, RULE_NAMES, _collision,
                                         _collision_stats, _combine, _path_row)


class SiteSequenceSearchTests(unittest.TestCase):
    def test_pairwise_collision_equals_full_population(self):
        prepared = {'validation': [], 'frozen_hits': []}
        for j in range(LENGTH):
            prepared['validation'].append({
                RULE_NAMES[0]: {'final': Counter({'shared': 2, f'g{j}': 1}),
                                'summary': {'modified_users': 1}},
                RULE_NAMES[1]: {'final': Counter({'shared': 1, f'a{j}': 2}),
                                'summary': {'modified_users': 2}},
                RULE_NAMES[2]: {'final': Counter({'shared': 1, f'n{j}': 2}),
                                'summary': {'modified_users': 3}},
            })
            prepared['frozen_hits'].append({name: j for name in RULE_NAMES})
        prepared['collision_stats'] = _collision_stats(prepared)
        for path in ((RULE_NAMES[0],) * LENGTH,
                     tuple(RULE_NAMES[j % 3] for j in range(LENGTH)),
                     (RULE_NAMES[2],) * LENGTH):
            row = _path_row(prepared, path)
            full = _combine(prepared, path, 'validation')
            self.assertAlmostEqual(row['collision'], _collision(full))
            self.assertEqual(row['validation_users'], sum(full.values()))


if __name__ == '__main__':
    unittest.main()
