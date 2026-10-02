"""The 18 standalone password-rule fragments used by the intervention study.

The two corpus-backed lists use only the independent development sample. The
current-hot list is rebuilt from the current population at each decision.
"""
from dataclasses import dataclass
import re


LABELS = {
    1: '长度至少 10 字符', 2: '长度至少 12 字符', 3: '长度至少 15 字符',
    4: '至少一个英文字母', 5: '至少一个小写英文字母',
    6: '至少一个大写英文字母', 7: '至少一个数字',
    8: '至少一个数字或符号', 9: '大写、小写、数字、符号至少两类',
    10: '至少四个不同字符', 11: '禁止连续三个相同字符',
    12: '禁止整串为同一字符', 13: '禁止简单键盘序列',
    14: '禁止四位连续字母或数字顺序序列', 15: '禁止仅为开发词表中的字典词',
    16: '禁止包含固定可预测词', 17: '禁止当前热门完整口令',
    18: '禁止命中独立开发样本中的泄露口令',
}
LENGTHS = {1: 10, 2: 12, 3: 15}
KEYBOARD = re.compile(r'qwerty|asdf|zxcv|qazwsx|1qaz|2wsx', re.I)
TRIPLE = re.compile(r'(.)\1{2,}')


def sequence_four(word):
    lowered = word.lower()
    for i in range(len(lowered) - 3):
        part = lowered[i:i + 4]
        if not (part.isascii() and (part.isalpha() or part.isdigit())):
            continue
        values = [ord(c) for c in part]
        if all(values[j + 1] - values[j] == 1 for j in range(3)) or all(
                values[j + 1] - values[j] == -1 for j in range(3)):
            return True
    return False


@dataclass(frozen=True)
class Fragment:
    number: int
    words: frozenset = frozenset()
    terms: tuple = ()

    @property
    def label(self):
        return LABELS[self.number]

    def accepts(self, word):
        n = self.number
        if n in LENGTHS:
            return len(word) >= LENGTHS[n]
        if n == 4:
            return bool(re.search(r'[A-Za-z]', word))
        if n == 5:
            return bool(re.search(r'[a-z]', word))
        if n == 6:
            return bool(re.search(r'[A-Z]', word))
        if n == 7:
            return bool(re.search(r'[0-9]', word))
        if n == 8:
            return any(c.isascii() and c.isdigit() or not c.isalnum() and not c.isspace()
                       for c in word)
        if n == 9:
            return sum((bool(re.search(r'[A-Z]', word)), bool(re.search(r'[a-z]', word)),
                        bool(re.search(r'[0-9]', word)),
                        any(not c.isalnum() and not c.isspace() for c in word))) >= 2
        if n == 10:
            return len(set(word)) >= 4
        if n == 11:
            return not TRIPLE.search(word)
        if n == 12:
            return len(set(word)) > 1
        if n == 13:
            return not KEYBOARD.search(word)
        if n == 14:
            return not sequence_four(word)
        if n in (15, 17, 18):
            return word.lower() not in self.words if n == 15 else word not in self.words
        if n == 16:
            return not any(term in word.lower() for term in self.terms)
        raise ValueError(f'未知政策片段：{n}')

    def summary(self):
        return {'fragment_number': self.number, 'fragment_label': self.label,
                'list_size': len(self.words), 'terms': list(self.terms)}


def candidate_fragments(*, hot=(), development=None, predictable_terms=('gitlab', 'devops')):
    """Build the full 1-18 catalogue; unavailable lists remain empty, not fabricated."""
    development = development or {}
    reference = frozenset(development)
    dictionary = frozenset(w.lower() for w in reference
                           if re.fullmatch(r'[A-Za-z]{3,20}', w))
    terms = tuple(sorted(set(t.lower() for t in predictable_terms if t)))
    values = {15: dictionary, 17: frozenset(hot), 18: reference}
    return tuple(Fragment(n, values.get(n, frozenset()), terms if n == 16 else ())
                 for n in LABELS if n not in (15, 18) or development)
