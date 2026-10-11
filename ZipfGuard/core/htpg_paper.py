"""Literal computational contracts from Xiao & Zeng, TIFS 2022.

No categorical-to-number encoding, substitute lexicon, or password repair is
invented here. Those are inputs, just as HeadFeature/TailFeature/P are inputs
to Algorithm 3. Undefined inputs raise MissingPaperDefinition, not a fallback.
"""
from __future__ import annotations

from collections import Counter
from fractions import Fraction
import math
from numbers import Real
from typing import Mapping, Sequence

VERSION = 'htpg-paper-contract-v1'
FEATURES = ('lsd_structure', 'length', 'capital', 'date', 'keyboard',
            'specplace', 'lowercase', 'word_type', 'lastname')


class MissingPaperDefinition(ValueError):
    """Execution cannot continue without a definition absent from the paper."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError(name + ' must be finite and numeric')
    return float(value)


def fit_and_classify(frequencies: Sequence[Real]) -> dict:
    """Sec. III-C, Eq. 1-3, Alg. 1; ranks r <= x0 belong to the head.

    Regression uses log(count), not probability or CDF; counts > 3 only.
    Regression is centered to avoid cancellation. No fitted rank is replaced
    by Table III's 1171. Frequencies <= 3 remain in the classification.
    """
    if not len(frequencies):
        raise ValueError('empty frequency vector')
    previous, total, k = math.inf, 0, 0
    xs, ys = [], []
    for rank, count in enumerate(frequencies, 1):
        v = _number(count, 'frequency')
        if v <= 0 or v > previous:
            raise ValueError('frequencies must be positive and descending')
        previous = v
        total += count
        if v > 3:
            xs.append(math.log(rank))
            ys.append(math.log(v))
            k += 1
    if k < 2:
        raise MissingPaperDefinition('insufficient_fit', 'f > 3 的类别不足两个，不能执行论文回归；不放宽门槛。')
    mx, my = math.fsum(xs)/k, math.fsum(ys)/k
    sxx = math.fsum((x-mx)**2 for x in xs)
    alpha = -math.fsum((x-mx)*(y-my) for x, y in zip(xs, ys))/sxx
    if alpha <= 0:
        raise MissingPaperDefinition('nonpositive_alpha', '拟合不满足论文 C > 0、alpha > 0 前提。')
    log_c = my + alpha*mx
    constant = math.exp(log_c)
    # Analytic maximum of |f''|/(1+f'^2)^(3/2), Appendix, Eq. 7.
    log_x0 = (2*log_c + 2*math.log(alpha) + math.log(2*alpha+1)
              - math.log(alpha+2))/(2*alpha+2)
    x0 = math.exp(log_x0)
    cutoff = min(len(frequencies), math.floor(x0))
    head = sum(frequencies[:cutoff])
    sst = math.fsum((y-my)**2 for y in ys)
    sse = math.fsum((y-(log_c-alpha*x))**2 for x, y in zip(xs, ys))
    return {'version': VERSION, 'source': 'III-C; Eq.1-3; Algorithm 1; Appendix',
            'C': constant, 'alpha': alpha, 'curvature_x0': x0,
            'cutoff_rank': cutoff, 'fit_types': k,
            'fit_min_frequency_exclusive': 3, 'fit_log_r_squared': 1-sse/sst if sst else None,
            'head_types': cutoff, 'tail_types': len(frequencies)-cutoff,
            'head_mass': float(head), 'tail_mass': float(total-head), 'total_mass': float(total),
            'head_mass_rate': float(head/total),
            'integer_boundary': 'integer ranks r <= continuous x0; floor, no forced minimum head',
            'fit_is_goodness_of_fit_test': False}


def _entropy(counts):
    total = math.fsum(counts)
    return -math.fsum((x/total)*math.log2(x/total) for x in counts if x > 0) if total else 0.


def feature_sort(rows, labels, *, weights, feature_order=FEATURES):
    """Eq.4 / Algorithm 2 with explicitly supplied sample weights.

    Information gain divided by split information (feature entropy). IV=0
    is undefined; it is NOT changed to zero and cannot silently join a ranking.
    Categorical values are used as categories here, never assigned fake numbers.
    """
    if not rows or len(rows) != len(labels) or len(rows) != len(weights):
        raise ValueError('rows, labels and weights must be nonempty and aligned')
    if not feature_order or len(set(feature_order)) != len(feature_order):
        raise ValueError('feature names must be nonempty and unique')
    if any(type(y) is not bool for y in labels):
        raise ValueError('head/tail labels must be bool')
    if any(_number(w, 'weight') <= 0 for w in weights):
        raise ValueError('weights must be positive')
    if len(set(labels)) != 2:
        raise MissingPaperDefinition('empty_class', '头部或尾部为空，不能执行双类别特征排序。')
    total = math.fsum(weights)
    h_y = _entropy([math.fsum(w for y, w in zip(labels, weights) if y == side)
                    for side in (False, True)])
    scores = []
    for name in feature_order:
        joint = {}
        for row, label, weight in zip(rows, labels, weights):
            if name not in row or row[name] is None:
                raise MissingPaperDefinition('missing_feature', '缺少特征定义：' + name)
            value = row[name]
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError('feature value must be finite')
            sides = joint.setdefault(value, [0., 0.])
            sides[int(label)] += weight
        iv = _entropy([sum(sides) for sides in joint.values()])
        conditional = math.fsum(sum(sides)/total*_entropy(sides) for sides in joint.values())
        ig = max(0., h_y-conditional)  # floating point roundoff only
        scores.append({'feature': name, 'information_gain': ig, 'intrinsic_value': iv,
                       'information_gain_ratio': ig/iv if iv else None})
    undefined = [r['feature'] for r in scores if r['information_gain_ratio'] is None]
    defined = sorted((r for r in scores if r['information_gain_ratio'] is not None),
                     key=lambda r: -r['information_gain_ratio'])
    return {'source': 'III-E; Eq.4; Algorithm 2', 'scores': scores,
            'ordered_features': [r['feature'] for r in defined],
            'undefined_features': undefined, 'complete': not undefined,
            'tie_rule': 'stable supplied feature order (paper does not specify ties)',
            'weights_supplied_by_caller': True}


def policies_generation(*, in_head, ordered_features, pwd_features,
                        head_features, tail_features, policies, m):
    """Algorithm 3 exactly: inspect first M features, retain d_head <= d_tail.

    P[f] is a caller-supplied policy, not a rewrite guessed by this function.
    Equal head/tail means still pass line 10. No equal-mean suppression, mode
    distance, missing-feature skipping, or backfilling to M accepted policies.
    Table IV's Length guard belongs in the construction of P, not line 10.
    """
    if type(in_head) is not bool:
        raise ValueError('in_head must be bool')
    if not in_head:
        return []
    if type(m) is not int or not 0 <= m <= len(ordered_features):
        raise ValueError('M must be an integer within feature list bounds')
    if len(set(ordered_features)) != len(ordered_features):
        raise ValueError('feature list contains duplicates')
    suggested = []
    for name in ordered_features[:m]:
        numbers = []
        for values in (pwd_features, head_features, tail_features):
            if name not in values:
                raise MissingPaperDefinition('missing_numeric_feature', '缺少算法 3 的数值特征：' + name)
            try:
                numbers.append(_number(values[name], name))
            except ValueError as exc:
                raise MissingPaperDefinition('categorical_mean', '论文没有给出类别特征的数值编码：' + name) from exc
        pwd, head, tail = numbers
        if abs(head-pwd) <= abs(tail-pwd):
            if name not in policies:
                raise MissingPaperDefinition('missing_policy', '表 IV 未提供或尚未定义策略：' + name)
            suggested.append(policies[name])
    return suggested


def table_iv_length_required(password_length, tail_mean):
    return _number(tail_mean, 'tail_mean') > _number(password_length, 'password_length')


def append_paper_date(password, rng):
    """The one concrete rewrite specified in IV-A.2, including both endpoints."""
    if not isinstance(password, str) or not password:
        raise ValueError('password must be nonempty')
    return password + str(rng.randint(1980, 2020))


def transition_half_quarters(counts: Mapping[str, Real], children):
    """IV-D / Fig.12 expected-mass transition, simultaneous and conservative.

    children maps each selected head password to exactly TWO resulting strings,
    one for each alternative policy. It never applies both policies sequentially.
    Fractions avoid making up an integer-account rounding rule absent in the text.
    Actual child collisions are merged, never assumed away to force flattening.
    """
    if not counts or set(children)-set(counts):
        raise ValueError('invalid source distribution or unknown head password')
    before = Counter()
    for word, count in counts.items():
        if not isinstance(word, str) or not word or _number(count, 'mass') <= 0:
            raise ValueError('positive masses and nonempty strings required')
        before[word] = Fraction(count)
    after = Counter(before)
    changed_mass = Fraction(0)
    for word, pair in children.items():
        if not isinstance(pair, (tuple, list)) or len(pair) != 2 or any(
                not isinstance(w, str) or not w for w in pair):
            raise MissingPaperDefinition('two_responses_required', 'IV-D 需要两个单独的建议响应口令。')
        mass = before[word]
        after[word] -= mass/2
        for child in pair:
            after[child] += mass/4
            if child != word:
                changed_mass += mass/4
    after = Counter({word: mass for word, mass in after.items() if mass > 0})
    assert sum(before.values()) == sum(after.values())
    return after, {'source': 'IV-D; Fig.12', 'response_fractions': [.5, .25, .25],
                   'head_mass': float(sum(before[w] for w in children)),
                   'changed_mass': float(changed_mass), 'population_mass': float(sum(before.values())),
                   'semantics': 'exact expected mass; not rounded individual accounts',
                   'two_alternative_policies': True}


def iterate(initial, *, rounds, prepare_round):
    """Periodic HTPG: rebuild all statistics from the current distribution.

    prepare_round(counts, fit, ordered_words) must run Algorithms 2/3 and
    return two responses for EVERY head type. No stale head set, one-time-only
    intervention flag, attack gate, or distribution improvement filter is used.
    """
    if type(rounds) is not int or rounds < 1:
        raise ValueError('rounds must be positive integer')
    counts, history = Counter(initial), []
    for number in range(1, rounds+1):
        ordered = sorted(counts, key=lambda word: (-counts[word], word))
        fit = fit_and_classify([counts[w] for w in ordered])
        heads = set(ordered[:fit['cutoff_rank']])
        children = prepare_round(Counter(counts), fit, tuple(ordered))
        if set(children) != heads:
            raise MissingPaperDefinition('incomplete_round', '必须提供每个头部口令的两个响应；不得跳过无法处理的特征或口令。')
        counts, ledger = transition_half_quarters(counts, children)
        history.append({'round': number, 'fit_before': fit, 'transition': ledger})
    return counts, history
