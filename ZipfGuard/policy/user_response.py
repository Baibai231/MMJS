"""Per-occurrence finite-attempt response, separate from the analytic R0 model."""
from __future__ import annotations

import hashlib
import random
import re
from collections import Counter

from policy.open_policy import edit_distance


FALLBACK_WORDS = ('amber', 'birch', 'cedar', 'cloud', 'coral', 'delta',
                  'dune', 'falcon', 'forest', 'harbor', 'lunar', 'maple',
                  'meadow', 'ocean', 'orchid', 'panda', 'pebble', 'quartz',
                  'raven', 'river', 'spruce', 'tiger', 'violet', 'willow')


def phrase_vocabulary(train, limit=512):
    words = [word.lower() for word, _ in sorted(train.items(),
             key=lambda row: (-row[1], row[0]))
             if re.fullmatch(r'[A-Za-z]{3,12}', word)]
    return tuple(dict.fromkeys((*words[:limit], *FALLBACK_WORDS)))


def _rng(seed, identifier):
    digest = hashlib.sha256(f'{seed}|{identifier}'.encode('utf-8')).digest()
    return random.Random(int.from_bytes(digest[:8], 'big'))


def _propose(word, kind, rng, pool, vocabulary):
    if kind == 'repair':
        # Some users make a predictable repair; others add a longer fragment.
        if rng.random() < .55:
            return word + rng.choice(('!', '@', '#', '1', '7', '2026', '@7'))
        fragment = rng.choice(vocabulary)
        return (word[:1].upper() + word[1:] + rng.choice(('!', '@', '#'))
                + str(rng.randrange(10)) + fragment)
    if kind == 'segment':
        a, b, c = (rng.choice(vocabulary) for _ in range(3))
        return f'{a}-{b}-{c}' if rng.random() < .7 else f'{a}{b}{c}{rng.randrange(10)}!'
    words, cumulative, total = pool
    value = rng.randrange(total)
    import bisect
    return words[bisect.bisect_right(cumulative, value)]


def weighted_pool(counts):
    words = sorted(counts)
    cumulative = []
    total = 0
    for word in words:
        total += int(counts[word])
        cumulative.append(total)
    if not total:
        raise ValueError('重选池不能为空')
    return words, cumulative, total


def simulate_users(words, rule, *, pool, vocabulary, seed, identifiers=None,
                   max_attempts=8, abandon_probability=.02,
                   weights=(.6, .25, .15), records=True):
    """The same user and attempt draw the same proposal across policy arms."""
    if len(weights) != 3 or any(w < 0 for w in weights) or abs(sum(weights) - 1) > 1e-9:
        raise ValueError('三种响应行为的概率须非负且和为 1')
    if not 0 <= abandon_probability <= 1 or max_attempts < 1:
        raise ValueError('响应概率或尝试次数无效')
    if identifiers is None:
        identifiers = range(len(words))
    final = Counter()
    completed_original = Counter()
    final_by_user = []
    events = [] if records else None
    accepted = modified = abandoned = failed = attempts_sum = edit_sum = length_sum = 0
    kinds = Counter()
    for identifier, word in zip(identifiers, words):
        initial_ok = rule.accepts(word)
        target = word if initial_ok else None
        kind = 'unchanged' if initial_ok else 'failed'
        attempts = 0
        if initial_ok:
            accepted += 1
        else:
            rng = _rng(seed, identifier)
            for attempts in range(1, max_attempts + 1):
                if rng.random() < abandon_probability:
                    kind = 'abandoned'
                    abandoned += 1
                    attempts -= 1
                    break
                draw = rng.random()
                proposal_kind = ('repair' if draw < weights[0]
                                 else 'segment' if draw < weights[0] + weights[1]
                                 else 'reselect')
                proposal = _propose(word, proposal_kind, rng, pool, vocabulary)
                if rule.accepts(proposal):
                    target = proposal
                    kind = proposal_kind
                    break
            if target is None and kind != 'abandoned':
                failed += 1
        attempts_sum += attempts
        final_by_user.append(target)
        if target is not None:
            final[target] += 1
            completed_original[word] += 1
            changed = target != word
            modified += changed
            distance = edit_distance(word, target) if changed and records else 0
            if changed and records:
                edit_sum += distance
                length_sum += max(0, len(target) - len(word))
        else:
            changed = False
            distance = 0
        kinds[kind] += 1
        if records:
            events.append({'user_id': str(identifier), 'original': word,
                           'final': target, 'initially_accepted': initial_ok,
                           'modified': changed, 'attempts': attempts,
                           'response_kind': kind,
                           'edit_distance': distance,
                           'length_increase': max(0, len(target) - len(word))
                           if changed else 0})
    n = len(words)
    return {'final': final, 'completed_original': completed_original,
            'final_by_user': final_by_user,
            'records': events,
            'summary': {'initial_users': n, 'initial_accept_rate': accepted / n if n else None,
                        'modified_users': modified,
                        'modification_rate': modified / n if n else None,
                        'completed_users': sum(final.values()),
                        'completion_rate': sum(final.values()) / n if n else None,
                        'failed_users': failed, 'abandoned_users': abandoned,
                        'mean_extra_attempts': attempts_sum / n if n else None,
                        'mean_edit_distance': edit_sum / n if n and records else None,
                        'mean_length_increase': length_sum / n if n and records else None,
                        'response_kinds': dict(kinds)}}
