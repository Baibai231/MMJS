"""Registration-only rules and policy-independent paired preference sequences."""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
import hashlib
import random
import string

VERSION = 'registration-joint-paired-v1'


def stable_seed(*parts):
    return int.from_bytes(hashlib.sha256('|'.join(map(str, parts)).encode()).digest()[:8], 'big')


@lru_cache(maxsize=200000)
def structure(word):
    # Fixed ASCII character classes; printable non-ASCII is excluded by loader.
    result = []
    for c in word:
        kind = 'L' if c in string.ascii_lowercase else 'U' if c in string.ascii_uppercase else 'D' if c in string.digits else 'S'
        if result and result[-1][0] == kind:
            result[-1][1] += 1
        else:
            result.append([kind, 1])
    return ''.join(k + str(n) for k, n in result)


@dataclass(frozen=True)
class JointRule:
    minimum: int = 8
    common: frozenset = frozenset()
    exact: frozenset = frozenset()
    shapes: frozenset = frozenset()
    k: int = 0
    j: int = 0
    strength: int = 0
    index: object = field(default=None, compare=False, repr=False)

    def accepts(self, word):
        if len(word) < self.minimum or word in self.common or word in self.exact or structure(word) in self.shapes:
            return False
        if self.strength:
            d = self.index.query(word)
            # Unsupported and zero-predecessor estimates never certify strength.
            return d['status'] == 'estimated' and d['rank_interval95'][0] > self.strength
        return True

    def signature(self):
        return (self.minimum, self.common, self.exact, self.shapes, self.strength)

    def summary(self):
        digest = lambda values: hashlib.sha256('\n'.join(sorted(values)).encode()).hexdigest()
        return {'minimum': self.minimum, 'k': self.k, 'j': self.j,
                'common_size': len(self.common), 'exact_size': len(self.exact),
                'shape_size': len(self.shapes), 'common_sha256': digest(self.common),
                'exact_sha256': digest(self.exact), 'shape_sha256': digest(self.shapes),
                'strength_threshold': self.strength, 'structure_encoding': 'ASCII-run-length-LUDS'}


def rebuild(base, recent, k, j):
    exact = sorted((w for w, n in recent.items() if n >= 2), key=lambda w: (-recent[w], w))[:k]
    shapes = Counter()
    for w, n in recent.items():
        shapes[structure(w)] += n
    selected = sorted((s for s, n in shapes.items() if n >= 20), key=lambda s: (-shapes[s], s))[:j]
    return JointRule(base.minimum, base.common, frozenset(exact), frozenset(selected), k, j)


def candidates(base, recent, hold, ks, js):
    result, seen = [(hold, True)], {hold.signature()}
    for k in ks:
        for j in js:
            rule = rebuild(base, recent, k, j)
            if rule.signature() not in seen:
                seen.add(rule.signature())
                result.append((rule, False))
    return result


class Preferences:
    """No hidden list, attacker, or policy object is read by proposal generation.

    Length padding is deterministic and uses only the common public minimum.
    It deliberately avoids high-entropy random padding as a source of safety.
    """
    def __init__(self, pool, minimum=8, weights=(.60, .25, .15), mode='mixed', limit=1000):
        self.pool, self.minimum, self.weights, self.mode, self.limit = pool, minimum, weights, mode, limit

    def proposals(self, original, identifier, seed):
        yield original
        rng = random.Random(stable_seed(seed, identifier, VERSION))
        suffixes = ('1', '!', '123', '2026', '2026!', '@1', '01', '1234', '7!', '123!')
        for attempt in range(1, self.limit + 1):
            if attempt == 1 and len(original) < self.minimum:
                word = original
            else:
                draw = rng.random()
                kind = ('reselect' if self.mode == 'independent' else
                        'repair' if self.mode == 'minimal' or draw < self.weights[0] else
                        'phrase' if draw < sum(self.weights[:2]) else 'reselect')
                if kind == 'reselect':
                    word = self.pool[rng.randrange(len(self.pool))]
                elif kind == 'phrase':
                    a = self.pool[rng.randrange(len(self.pool))]
                    b = self.pool[rng.randrange(len(self.pool))]
                    word = a[:4] + b[:4] + suffixes[rng.randrange(len(suffixes))]
                else:
                    op = rng.randrange(7)
                    tail = suffixes[rng.randrange(len(suffixes))]
                    word = (original + tail if op == 0 else tail + original if op == 1 else
                            original.capitalize() + tail if op == 2 else original.upper() + tail if op == 3 else
                            original[:len(original)//2] + tail + original[len(original)//2:] if op == 4 else
                            original[:-1] + tail if op == 5 else original + tail + tail)
            if len(word) < self.minimum:
                word += '1' * (self.minimum - len(word))
            yield word

    def simulate(self, records, rule, seed, *, distances=False, patience=None):
        from policy.open_policy import edit_distance
        outputs, attempts, edits = Counter(), [], 0
        modified = pending = visible = blocked = 0
        users = []
        for identifier, original in records:
            target = None
            last_attempt = 0
            for attempt, word in enumerate(self.proposals(original, identifier, seed)):
                if patience is not None and attempt > patience:
                    break
                last_attempt = attempt
                if rule.accepts(word):
                    target = word
                    break
            attempts.append(last_attempt)
            visible += len(original) < self.minimum
            blocked += len(original) >= self.minimum and not rule.accepts(original)
            if target is None:
                pending += 1
            else:
                outputs[target] += 1
                modified += target != original
                if distances and target != original:
                    edits += edit_distance(original, target)
            users.append(target)
        n = len(records)
        ordered = sorted(attempts)
        return {'counts': outputs, 'users': users, 'attempts': attempts,
                'summary': {'initial_users': n, 'completed_users': n - pending, 'pending_users': pending,
                            'completion_rate': (n-pending)/n if n else 0,
                            'modification_rate': modified/n if n else 0,
                            'mean_extra_submissions': sum(attempts)/n if n else 0,
                            'p90_extra_submissions': ordered[min(n-1, int(.9*n))] if n else 0,
                            'more_than_three_fraction': sum(x > 3 for x in attempts)/n if n else 0,
                            'visible_length_repairs': visible, 'hidden_initial_rejections': blocked,
                            'mean_edit_distance': edits/n if n and distances else None,
                            'terminal_state': 'patience_exhausted_simulated_exit' if patience else 'candidate_limit_pending',
                            'preference_protocol': VERSION}}
