"""Literature mechanisms and paired online registration, without query side effects.

Schechter: ideal exact-count popularity oracle (not the privacy-preserving CMS).
Malone/Maher: Section 6 proposal-frequency algorithm, including rejected proposals.
"""
from collections import Counter
from dataclasses import dataclass, replace
import random

from policy.registration_joint import JointRule, stable_seed


@dataclass(frozen=True)
class CompositionRule(JointRule):
    github: bool = False

    def accepts(self, word):
        if self.github and not (len(word) >= 15 or
                (len(word) >= 8 and any('a' <= c <= 'z' for c in word)
                 and any('0' <= c <= '9' for c in word))):
            return False
        return super().accepts(word)

    def signature(self):
        return super().signature() + (self.github,)

    def summary(self):
        return {**super().summary(), 'github_core': self.github}


def exact_rule(base, counts, k):
    words = sorted((w for w, n in counts.items() if n >= 2 and base.accepts(w)),
                   key=lambda w: (-counts[w], w))[:k]
    return replace(base, exact=frozenset(words), k=k)


class PopularityOracle:
    def __init__(self, base, rate, counts=None):
        self.base, self.rate = base, rate
        self.counts = Counter(counts or {})
        self.total = sum(self.counts.values())

    def begin(self, identifier, seed):
        pass

    def decide(self, word):
        # A never-seen string is permitted during small-N bootstrap. The test
        # otherwise implements flagging count >= r*N, with N accepted accounts.
        return self.base.accepts(word) and (not self.counts[word] or
                                           self.counts[word] < self.rate * self.total)

    def commit(self, word):
        if word is not None:
            self.counts[word] += 1
            self.total += 1


class MaloneMaher:
    def __init__(self, base, counts=None):
        self.base = base
        self.counts = Counter(counts or {})
        self.distinct = list(self.counts)
        self.proposals_seen = sum(self.counts.values())

    def begin(self, identifier, seed):
        self.rng = random.Random(stable_seed(seed, identifier, 'Malone-Maher-2012'))
        self.reference = self.rng.choice(self.distinct) if self.distinct else None

    def decide(self, word):
        previous = self.counts[word]
        u = self.rng.random() * previous
        if not previous:
            self.distinct.append(word)
        self.counts[word] += 1
        self.proposals_seen += 1
        reference_count = self.counts[self.reference] if self.reference is not None else 0
        # F includes composition-invalid and rejected proposals. Reference stays
        # fixed throughout the user's retries; its count is read AFTER update.
        return self.base.accepts(word) and u <= reference_count

    def commit(self, word):
        pass


def simulate_online(records, mechanism, response, seed, *, capture_proposals=False):
    outputs, attempts, users = Counter(), [], []
    proposed = Counter()
    modified = pending = 0
    for identifier, original in records:
        mechanism.begin(identifier, seed)
        target = None
        for attempt, word in enumerate(response.proposals(original, identifier, seed)):
            if capture_proposals:
                proposed[word] += 1
            if mechanism.decide(word):
                target = word
                break
        mechanism.commit(target)
        attempts.append(attempt)
        users.append(target)
        if target is None:
            pending += 1
        else:
            outputs[target] += 1
            modified += target != original
    n = len(records)
    return {'counts': outputs, 'users': users, 'attempts': attempts, 'proposed': proposed,
            'summary': {'initial_users': n, 'completed_users': n-pending,
                        'pending_users': pending, 'completion_rate': (n-pending)/n,
                        'modification_rate': modified/n, 'mean_extra_submissions': sum(attempts)/n,
                        'more_than_three_fraction': sum(a > 3 for a in attempts)/n,
                        'mean_edit_distance': None}}
