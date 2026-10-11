"""Runnable HTPG experiment with disclosed replacements for missing paper inputs.

The equations and first-M decision remain in core.htpg_paper.  This module is
an operational assumption set, never evidence of the authors' implementation.
"""
from __future__ import annotations

from collections import Counter
from fractions import Fraction
import hashlib
import math
import random

from core.htpg_features import HTPGFeatureExtractor
from core.htpg_paper import (FEATURES, append_paper_date, feature_sort,
                             fit_and_classify, policies_generation,
                             transition_half_quarters)
from experiments.suggestion_compare import _rewrite

ASSUMPTION_VERSION = 'htpg-conditional-v1'


def _scalar(vector):
    values = vector.to_dict()
    values['lsd_structure'] = float(sum(c in 'LDS' for c in values['lsd_structure']))
    places = set(values['specplace'].split('+'))
    values['specplace'] = float(sum(bit for name, bit in
                                    (('head', 1), ('middle', 2), ('tail', 4)) if name in places))
    return {name: float(values[name]) for name in FEATURES}


def _tail_modes(rows, weights, cutoff):
    result = {}
    for name in ('lsd_structure', 'specplace'):
        counts = Counter()
        for row, weight in zip(rows[cutoff:], weights[cutoff:]):
            counts[row[name]] += weight
        result[name] = sorted(counts, key=lambda value: (-counts[value], value))[0]
    return result


def _response(word, feature, round_number, slot, head, tail, modes, extractor):
    tag = f'{ASSUMPTION_VERSION}|{round_number}|{slot}|{feature}|{word}'
    seed = int.from_bytes(hashlib.sha256(tag.encode('utf-8', 'surrogatepass')).digest()[:8], 'big')
    if feature == 'date' and tail['date'] > head['date']:
        candidate = append_paper_date(word, random.Random(seed))
    elif feature == 'length':
        # Table IV permits lengthening only when tail mean exceeds this password.
        candidate = (_rewrite(word, feature, 'increase_length', str(math.ceil(tail['length'])),
                              extractor, user_id=tag, response_mode='diversified')
                     if tail['length'] > len(word) else word)
    elif feature in ('lsd_structure', 'specplace'):
        action = ('change_lsd_toward_tail_mode' if feature == 'lsd_structure'
                  else 'move_special_placement_toward_tail_mode')
        candidate = _rewrite(word, feature, action, 'tail mode '+modes[feature], extractor,
                             user_id=tag)
    elif feature == 'lowercase':
        if tail[feature] > head[feature]:
            candidate = word[:1].lower()+word[1:] if word else word
        else:
            candidate = word[:1].upper()+word[1:] if word else word
    else:
        positive = {'capital': 'use_capital', 'date': 'use_date',
                    'keyboard': 'use_keyboard_walk', 'word_type': 'use_emotion_word',
                    'lastname': 'use_lastname'}
        negative = {'capital': 'avoid_capital', 'date': 'avoid_date',
                    'keyboard': 'avoid_keyboard_walk', 'word_type': 'avoid_emotion_word',
                    'lastname': 'avoid_lastname'}
        action = positive[feature] if tail[feature] > head[feature] else negative[feature]
        candidate = _rewrite(word, feature, action, None, extractor, user_id=tag)
    if not isinstance(candidate, str) or not candidate:
        return word
    # Date's year append is the paper's concrete example. The operational date
    # recognizer may miss a year appended to an existing digit run, so do not
    # silently substitute a different date rewrite here.
    if feature == 'date' and tail['date'] > head['date']:
        return candidate
    observed = extractor.extract(candidate).to_dict()
    if feature in ('lsd_structure', 'specplace'):
        fulfilled = observed[feature] == modes[feature]
    elif feature == 'length':
        fulfilled = len(candidate) >= math.ceil(tail['length']) if tail['length'] > len(word) else True
    else:
        fulfilled = observed[feature] is (tail[feature] > head[feature])
    return candidate if fulfilled else word


def run_conditional(initial, *, rounds, extractor, weighting='unique', progress=None,
                    round_observer=None):
    """Execute paper equations with explicit, auditable response assumptions.

    An absent recommendation occupies its 25% branch as no change. Failed
    rewrites likewise keep the original. No later feature is backfilled.
    """
    if weighting not in ('unique', 'frequency') or rounds < 1:
        raise ValueError('invalid conditional experiment parameters')
    counts = Counter({word: Fraction(mass) for word, mass in initial.items()})
    population = sum(counts.values())
    history = []
    for number in range(1, rounds+1):
        if progress:
            progress(f'条件 HTPG 第 {number}/{rounds} 轮：提取九项特征并重新排序')
        ordered = sorted(counts, key=lambda word: (-counts[word], word))
        fit = fit_and_classify([counts[word] for word in ordered])
        cutoff = fit['cutoff_rank']
        vectors = [extractor.extract(word) for word in ordered]
        rows = [vector.to_dict() for vector in vectors]
        labels = [i < cutoff for i in range(len(ordered))]
        weights = [1.0 if weighting == 'unique' else float(counts[word]) for word in ordered]
        sorting = feature_sort(rows, labels, weights=weights)
        # Explicit zero-IV assumption: undefined features rank after defined ones.
        ranked = sorting['ordered_features'] + sorting['undefined_features']
        scalars = [_scalar(vector) for vector in vectors]
        means = {}
        for side, indices in ((True, range(cutoff)), (False, range(cutoff, len(ordered)))):
            mass = math.fsum(weights[i] for i in indices)
            means[side] = {name: math.fsum(weights[i]*scalars[i][name] for i in indices)/mass
                           for name in FEATURES}
        modes = _tail_modes(rows, weights, cutoff)
        children = {}
        suggestions = Counter()
        failed = Counter()
        for i, word in enumerate(ordered[:cutoff]):
            selected = policies_generation(
                in_head=True, ordered_features=ranked, pwd_features=scalars[i],
                head_features=means[True], tail_features=means[False],
                policies={name: name for name in FEATURES}, m=2)
            suggestions[len(selected)] += 1
            children[word] = tuple(_response(word, selected[slot], number, slot,
                                             means[True], means[False], modes, extractor)
                                   if slot < len(selected) else word for slot in range(2))
            for slot, child in enumerate(children[word]):
                if child == word:
                    failed['no_change_or_missing_'+str(slot+1)] += 1
        if round_observer is not None:
            round_observer(number, dict(children))
        counts, ledger = transition_half_quarters(counts, children)
        if sum(counts.values()) != population:
            raise AssertionError('population mass changed')
        history.append({'round': number, 'fit': fit, 'top_two_features': ranked[:2],
                        'undefined_igr': sorting['undefined_features'],
                        'suggestions_per_head_type': dict(suggestions),
                        'no_change_response_types': dict(failed),
                        'transition': ledger, 'types_after': len(counts),
                        'hhi_after': math.fsum(float(m/population)**2 for m in counts.values())})
    return counts, history


def replay_reference_round(population, children, *, seed, round_number):
    """Apply the target group's public response map to independent accounts.

    This disclosed integer-account assumption samples the paper's 50/25/25
    branches with a stable uniform draw per account and round. The independent
    reference population is never used to choose target policies.
    """
    if type(seed) is not int or type(round_number) is not int or round_number < 1:
        raise ValueError('seed and round number must be integers')
    branches = [0, 0, 0]
    changed = eligible = 0
    for account in population.accounts:
        pair = children.get(account.password)
        if pair is None:
            continue
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise ValueError('reference replay requires two alternative responses')
        eligible += 1
        raw = hashlib.sha256(
            f'htpg-a1|{seed}|{round_number}|{account.identifier}'.encode('utf-8')).digest()
        draw = int.from_bytes(raw[:8], 'big') / 2**64
        branch = 0 if draw < .5 else 1 if draw < .75 else 2
        branches[branch] += 1
        if branch:
            new = pair[branch-1]
            if not isinstance(new, str) or not new:
                raise ValueError('reference response must be a nonempty string')
            if new != account.password:
                account.password = new
                changed += 1
    if sum(branches) != eligible or population.total != len(population.accounts):
        raise AssertionError('reference replay lost accounts')
    return {'round': round_number, 'eligible_accounts': eligible,
            'branches': {'keep': branches[0], 'first': branches[1], 'second': branches[2]},
            'changed_accounts': changed, 'reference_accounts': population.total}
