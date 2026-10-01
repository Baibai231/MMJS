"""Visible-rule-aware candidates and hidden-rule retries, with no abandonment."""
from __future__ import annotations

import hashlib
import random
import re
from collections import Counter

from policy.open_policy import edit_distance

RESPONSE_PROTOCOL = 'visible-first-hidden-retry-v2'
CHARACTER_GROUPS = ('abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ',
                    '0123456789', '!@#$%&*?')
CLASS_PATTERNS = tuple(re.compile(pattern) for pattern in
                       (r'[a-z]', r'[A-Z]', r'\d', r'[^A-Za-z0-9]'))


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


def meets_visible_rules(word, min_length, classes):
    return (len(word) >= min_length
            and sum(bool(pattern.search(word)) for pattern in CLASS_PATTERNS) >= classes)


def satisfy_visible_rules(word, *, min_length, classes, rng):
    """Append missing character classes and length before submitting a candidate.

    Only the displayed length/classes are available here. The generator cannot
    inspect the blocklist, denied features or acceptance results in advance.
    """
    if not 0 <= classes <= 4 or min_length < 0:
        raise ValueError('显式长度或字符类别要求无效')
    present = [bool(pattern.search(word)) for pattern in CLASS_PATTERNS]
    for exists, alphabet in zip(present, CHARACTER_GROUPS):
        if sum(present) >= classes:
            break
        if not exists:
            word += rng.choice(alphabet)
            present = [bool(pattern.search(word)) for pattern in CLASS_PATTERNS]
    # Seeded padding is a documented construction algorithm, not user dropout.
    alphabet = CHARACTER_GROUPS[0] + CHARACTER_GROUPS[2]
    word += ''.join(rng.choice(alphabet) for _ in range(max(0, min_length - len(word))))
    assert meets_visible_rules(word, min_length, classes)
    return word


def satisfy_site_rules(word, rule, rng):
    if rule.max_length is not None:
        word = word[:rule.max_length]
    bypass = rule.long_password_bypass is not None and len(word) >= rule.long_password_bypass
    if not bypass:
        requirements = (rule.require_lower, rule.require_upper, rule.require_digit, rule.require_special)
        for required, pattern, alphabet in zip(requirements, CLASS_PATTERNS, CHARACTER_GROUPS):
            if required and not pattern.search(word):
                word += rng.choice(alphabet)
    word = satisfy_visible_rules(word, min_length=rule.min_length, classes=rule.classes, rng=rng)
    return word


def simulate_users(words, rule, *, pool, vocabulary, seed, identifiers=None,
                   retry_report_after=8, candidate_limit=1000,
                   weights=(.6, .25, .15), records=True):
    """Retry hidden rejections; exceeding the reporting threshold never stops a user.

    candidate_limit is a computation bound only. Exhausted records remain pending,
    retain their identity and attempts, and are never labeled failed/abandoned.
    """
    if len(weights) != 3 or any(w < 0 for w in weights) or abs(sum(weights) - 1) > 1e-9:
        raise ValueError('三种响应行为的概率须非负且和为 1')
    if not 1 <= retry_report_after <= candidate_limit:
        raise ValueError('重试报告阈值必须在候选计算上限以内')
    if identifiers is None:
        identifiers = range(len(words))
    final = Counter()
    completed_original = Counter()
    final_by_user = []
    events = [] if records else None
    accepted = modified = pending = attempts_sum = edit_sum = length_sum = 0
    reported = explicit_repairs = hidden_blocked = hidden_rejections_sum = 0
    kinds = Counter()
    for identifier, word in zip(identifiers, words):
        initial_ok = rule.accepts(word)
        visible_rule = __import__('dataclasses').replace(rule, blocklist=frozenset(), deny=())
        visible_ok = visible_rule.accepts(word)
        target = word if initial_ok else None
        kind = 'unchanged' if initial_ok else 'pending'
        attempts = 0
        hidden_rejections = int(visible_ok and not initial_ok)
        threshold_reached = False
        if initial_ok:
            accepted += 1
        else:
            explicit_repairs += not visible_ok
            for attempts in range(1, candidate_limit + 1):
                rng = _rng(seed, f'{identifier}|candidate-{attempts}')
                if attempts == 1 and not visible_ok:
                    proposal, proposal_kind = word, 'visible_repair'
                else:
                    draw = rng.random()
                    proposal_kind = ('repair' if draw < weights[0]
                                     else 'segment' if draw < weights[0] + weights[1]
                                     else 'reselect')
                    proposal = _propose(word, proposal_kind, rng, pool, vocabulary)
                proposal = satisfy_site_rules(proposal, rule, rng)
                if rule.accepts(proposal):
                    target = proposal
                    kind = proposal_kind
                    break
                hidden_rejections += 1
                if attempts >= retry_report_after:
                    threshold_reached = True
            if target is None:
                pending += 1
        reported += threshold_reached
        hidden_blocked += hidden_rejections > 0
        hidden_rejections_sum += hidden_rejections
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
                           'registration_status': 'registered' if target is not None else 'pending',
                           'pending_reason': None if target is not None else 'candidate_generation_limit',
                           'visible_initially_accepted': visible_ok,
                           'hidden_rejections': hidden_rejections,
                           'retry_threshold_reached': threshold_reached,
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
                        'pending_users': pending,
                        'pending_rate': pending / n if n else None,
                        'retry_reported_users': reported,
                        'visible_repaired_users': explicit_repairs,
                        'hidden_blocked_users': hidden_blocked,
                        'hidden_rejections': hidden_rejections_sum,
                        'retry_report_after': retry_report_after,
                        'candidate_limit': candidate_limit,
                        'response_protocol': RESPONSE_PROTOCOL,
                        'mean_extra_attempts': attempts_sum / n if n else None,
                        'mean_edit_distance': edit_sum / n if n and records else None,
                        'mean_length_increase': length_sum / n if n and records else None,
                        'response_kinds': dict(kinds)}}
