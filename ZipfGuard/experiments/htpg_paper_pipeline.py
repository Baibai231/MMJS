"""HTPG iteration/attack adapter with mandatory externally defined paper inputs.

The project evaluator is used AFTER decisions, never to select or reject HTPG
edits. Unlike selective intervention, this model permits repeated notification
and represents IV-D's exact expected frequency mass rather than rounded users.
"""
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
import math
from typing import Callable

from core.htpg_paper import (FEATURES, VERSION, MissingPaperDefinition,
                             feature_sort, fit_and_classify, policies_generation,
                             transition_half_quarters)


@dataclass(frozen=True)
class PaperInputs:
    """Definitions absent from the PDF; no implicit defaults.

    categories(password) returns all nine categories used by Algorithm 2.
    scalars(password) returns numerical values used in Algorithm 3.
    policies(password, head_means, tail_means, current_tail) returns P[f].
    responses(password, policies, round_number) returns two alternative strings.
    No original-author fidelity is inferred from the existence of callbacks.
    """
    categories: Callable
    scalars: Callable
    policies: Callable
    responses: Callable
    weighting: str
    provenance: dict


def _summary(counts):
    total = sum(counts.values())
    ranked = sorted(counts.values(), reverse=True)
    return {'population_mass': float(total), 'types': len(counts),
            'maximum_frequency_mass': float(ranked[0]),
            'hhi': float(sum((c/total)**2 for c in ranked)),
            'entropy_bits': -math.fsum(float(c/total)*math.log2(float(c/total)) for c in ranked),
            'mass_is_expected_not_integer_accounts': True}


def run_paper_pipeline(initial, *, inputs=None, rounds=10, evaluator=None, progress=None):
    """Execute full nine-feature contracts once all inputs are supplied.

    This doesn't silently call historical HTPGFeatureExtractor or apply_edit.
    The initial population can be the same unmodified or Google-started project
    counts; the caller must disclose the latter as a project comparison protocol.
    """
    if inputs is None:
        raise MissingPaperDefinition('paper_inputs_required',
            '严格 HTPG 需要原始特征编码、识别器、词表、权重和响应函数；未提供，未生成终态。')
    if not isinstance(inputs, PaperInputs) or inputs.weighting not in ('unique', 'frequency'):
        raise ValueError('explicit PaperInputs and unique/frequency weighting required')
    if not isinstance(inputs.provenance, dict) or not inputs.provenance:
        raise ValueError('paper input provenance is required')
    if type(rounds) is not int or not 1 <= rounds <= 200:
        raise ValueError('rounds must be between 1 and 200')
    # Validate all initial masses before any feature callback or attack query.
    counts = Counter(initial)
    if not counts or any(not isinstance(w, str) or not w or isinstance(n, bool)
                         or not isinstance(n, (int, float, Fraction)) or not math.isfinite(n) or n <= 0
                         for w, n in counts.items()):
        raise ValueError('initial counts require nonempty strings and positive finite masses')
    counts = Counter({w: Fraction(n) for w, n in counts.items()})
    initial_mass = sum(counts.values())
    initial_metrics = _summary(counts)
    initial_attack = evaluator.evaluate({w: float(n) for w, n in counts.items()}) if evaluator else None
    history = []
    for number in range(1, rounds+1):
        if progress:
            progress(f'HTPG 第 {number} 轮：重新拟合、分类、九特征排序和逐口令建议')
        ordered = sorted(counts, key=lambda w: (-counts[w], w))
        fit = fit_and_classify([counts[w] for w in ordered])
        cutoff = fit['cutoff_rank']
        labels = [rank < cutoff for rank in range(len(ordered))]
        weights = [1 if inputs.weighting == 'unique' else float(counts[w]) for w in ordered]
        rows = [inputs.categories(w) for w in ordered]
        sorting = feature_sort(rows, labels, weights=weights)
        if not sorting['complete']:
            raise MissingPaperDefinition('undefined_igr',
                'IV=0 导致 IGR 未定义；论文没有排序规则：'+', '.join(sorting['undefined_features']))
        scalar_rows = [inputs.scalars(w) for w in ordered]
        means = []
        for side in (True, False):
            members = [i for i, label in enumerate(labels) if label == side]
            total_weight = sum(weights[i] for i in members)
            mean = {}
            for name in FEATURES:
                for i in members:
                    value = scalar_rows[i].get(name)
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                        raise MissingPaperDefinition('missing_scalar_encoding', '算法 3 缺少数值特征：'+name)
                mean[name] = math.fsum(weights[i]*scalar_rows[i][name] for i in members)/total_weight
            means.append(mean)
        head_means, tail_means = means
        tail_counts = Counter({w: counts[w] for w in ordered[cutoff:]})
        children, suggestion_counts = {}, Counter()
        for i, word in enumerate(ordered[:cutoff]):
            mapping = inputs.policies(word, head_means, tail_means, Counter(tail_counts))
            suggested = policies_generation(in_head=True, ordered_features=sorting['ordered_features'],
                pwd_features=scalar_rows[i], head_features=head_means, tail_features=tail_means,
                policies=mapping, m=2)
            suggestion_counts[len(suggested)] += 1
            if len(suggested) != 2:
                raise MissingPaperDefinition('fewer_than_two_policies',
                    '前 M=2 项未产生两个建议；IV-D 未规定此时的分支分配，不补入后面的特征。')
            children[word] = inputs.responses(word, tuple(suggested), number)
        new_counts, ledger = transition_half_quarters(counts, children)
        if sum(new_counts.values()) != initial_mass:
            raise AssertionError('HTPG transition changed population mass')
        counts = new_counts
        history.append({'round': number, 'fit_before': fit, 'sorting': sorting,
                        'suggestions_per_head_type': dict(suggestion_counts),
                        'transition': ledger, 'distribution_after': _summary(counts),
                        'F_attack_after': evaluator.evaluate({w: float(n) for w, n in counts.items()}) if evaluator else None})
    return {'schema_version': VERSION+'-iteration', 'initial': initial_metrics,
            'initial_F_attack': initial_attack, 'rounds': history,
            'final': _summary(counts), 'final_F_attack': history[-1]['F_attack_after'],
            'final_counts': counts,
            'input_provenance': dict(inputs.provenance), 'weighting': inputs.weighting,
            'full_author_reproduction_verified': False,
            'A1_status': 'not_run: fractional expected masses are not original integer training accounts',
            'attack_used_for_policy_decisions': False, 'repeat_modification_allowed': True}
