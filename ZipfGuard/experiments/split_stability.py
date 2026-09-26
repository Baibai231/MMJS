"""Head/tail cut comparisons. The curvature cut depends on count scale.

Multiplying every frequency by k, with the same support, multiplies the
analytic curvature peak by k ** (1 / (alpha + 1)). That is a property of
f(r)=C/r^alpha on raw axes. It is not evidence that the paper is wrong.
"""
from __future__ import annotations

import math
from typing import Sequence

from core.htpg_fit import curvature_maximum


def scaled_curvature_peak(constant: float, alpha: float, multiplier: float) -> float:
    if multiplier <= 0:
        raise ValueError("倍数必须为正")
    return curvature_maximum(constant * multiplier, alpha)


def scale_factor(alpha: float, multiplier: float) -> float:
    return multiplier ** (1 / (alpha + 1))


def mass_cutoff(counts: Sequence[int], quantile: float) -> int:
    """Smallest 1-based rank whose cumulative mass reaches ``quantile``."""
    if not 0 < quantile <= 1:
        raise ValueError("质量阈值必须在 (0, 1] 内")
    total = sum(counts)
    if total <= 0:
        raise ValueError("频次和必须为正")
    running = 0
    for rank, count in enumerate(counts, start=1):
        running += count
        if running / total >= quantile:
            return rank
    return len(counts)
