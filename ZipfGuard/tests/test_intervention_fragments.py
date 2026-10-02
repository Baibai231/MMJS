import unittest

from policy.intervention_fragments import Fragment, candidate_fragments


class TestInterventionFragments(unittest.TestCase):
    def test_every_document_fragment_has_a_distinct_predicate(self):
        examples = {
            1: ('abcdefghij', 'abcdefghi'),
            2: ('abcdefghijkl', 'abcdefghijk'),
            3: ('abcdefghijklmno', 'abcdefghijklmn'),
            4: ('a12345678', '123456789'),
            5: ('aB123456', 'AB123456'),
            6: ('Ab123456', 'ab123456'),
            7: ('abc12345', 'abcdefgh'),
            8: ('abcdefgh!', 'abcdefgh'),
            9: ('abc12345', 'abcdefgh'),
            10: ('aaabbbcd', 'aaabbbcc'),
            11: ('aabaabaa', 'aaabbbbb'),
            12: ('aaaaaaab', 'aaaaaaaa'),
            13: ('safepass', 'myqwerty8'),
            14: ('safe2739', 'safe1234'),
            15: ('randomphrase', 'apple'),
            16: ('safepass', 'mygitlab8'),
            17: ('safepass', 'hotpass8'),
            18: ('safepass', 'leaked8'),
        }
        for number, (accepted, rejected) in examples.items():
            list_word = {15: 'apple', 17: 'hotpass8', 18: 'leaked8'}.get(number)
            fragment = Fragment(number,
                                words=frozenset((list_word,)) if list_word else frozenset(),
                                terms=('gitlab',) if number == 16 else ())
            with self.subTest(number=number):
                self.assertTrue(fragment.accepts(accepted))
                self.assertFalse(fragment.accepts(rejected))

    def test_reference_lists_and_current_hot_words_are_separate(self):
        fragments = candidate_fragments(hot={'current8'},
                                        development={'leaked8': 5, 'apple': 2},
                                        predictable_terms=['gitlab'])
        self.assertEqual([f.number for f in fragments], list(range(1, 19)))
        by_number = {f.number: f for f in fragments}
        self.assertFalse(by_number[17].accepts('current8'))
        self.assertTrue(by_number[18].accepts('current8'))
        self.assertFalse(by_number[18].accepts('leaked8'))
        self.assertFalse(by_number[15].accepts('apple'))
        self.assertTrue(by_number[15].accepts('leaked8'))


if __name__ == '__main__':
    unittest.main()
