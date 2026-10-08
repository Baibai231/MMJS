"""Public, encodable Yahoo! JAPAN requirements; no hidden checks inferred."""
from dataclasses import dataclass
import string

SOURCE_URL = 'https://support.yahoo-net.jp/SccLogin/s/article/H000004639'
ALLOWED = frozenset(string.ascii_letters + string.digits + string.punctuation.replace('<', '') + ' ')


@dataclass(frozen=True)
class YahooJapanRule:
    name: str = 'yahoo-japan-public-15-32'
    min_length: int = 15
    max_length: int = 32

    def accepts(self, word):
        return (self.min_length <= len(word) <= self.max_length
                and all(c in ALLOWED for c in word) and '&{' not in word)

    def summary(self):
        return {'name': self.name, 'min_length': self.min_length, 'max_length': self.max_length,
                'allowed_characters': ''.join(sorted(ALLOWED)), 'forbidden_combinations': ['&{'],
                'source_url': SOURCE_URL, 'verified_on': '2026-10-07',
                'limitations': '公开列出的字符和 &{ 禁止组合；未公开的其他组合或内部检测未复刻'}

    def complete(self, word, rng):
        # Same visible-rule completion premise as the mandatory Google start.
        clean = ''.join(c for c in word if c in ALLOWED).replace('&{', '&_')[:self.max_length]
        while len(clean) < self.min_length:
            clean += rng.choice(string.ascii_lowercase+string.digits)
        assert self.accepts(clean)
        return clean
