"""The path partition may merge only identical response outcomes."""
import unittest

from tools.exhaustive_site_sequence_a1 import LENGTH, RULE_NAMES, _group_space


class ExhaustiveSiteSequenceTests(unittest.TestCase):
    def test_equivalent_site_labels_preserve_all_rule_paths(self):
        signatures = {
            role: [{RULE_NAMES[0]: f'{role}-g-{j}',
                    RULE_NAMES[1]: f'{role}-a-{j}',
                    RULE_NAMES[2]: f'{role}-a-{j}'}
                   for j in range(LENGTH)]
            for role in ('train', 'validation')
        }
        groups, path_to_group = _group_space(signatures)
        self.assertEqual(len(path_to_group), 3**LENGTH)
        self.assertEqual(len(groups), 2**LENGTH)
        self.assertEqual(sum(row['members'] for row in groups.values()), 3**LENGTH)
        self.assertEqual(path_to_group['1' * LENGTH], path_to_group['2' * LENGTH])
        self.assertNotEqual(path_to_group['0' * LENGTH], path_to_group['1' * LENGTH])


if __name__ == '__main__':
    unittest.main()
