"""Paper-style PDF-Zipf regression and curvature head/tail cut.

This is the Xiao & Zeng HTPG fit, not the finite-support model competition in
``core/distributions.py``. Ranks are 1-based positions in a nonincreasing
frequency vector. The cut is ``floor(x0)`` of the analytic curvature maximum.
It is not replaced by the paper's published rank 1171.
"""
from __future__ import annotations

import math
from typing import Sequence


FIT_VERSION = "htpg-pdf-zipf-ols-v1"


def _as_descending_counts(counts: Sequence[int]) -> list[int]:
    if len(counts) < 3:
        raise ValueError("至少需要 3 个频次才能拟合")
    if any(isinstance(count, bool) or not isinstance(count, (int,)) or count < 0 for count in counts):
        raise ValueError("频次必须是非负整数")
    values = [int(count) for count in counts]
    if any(values[index] < values[index + 1] for index in range(len(values) - 1)):
        raise ValueError("频次必须按降序排列；调用方需要先排序并记录是否改变了文件顺序")
    return values


def curvature_maximum(constant: float, alpha: float) -> float:
    """Analytic argmax of curvature for ``f(r)=C/r^alpha`` on raw rank/count axes."""
    if constant <= 0 or alpha <= 0:
        raise ValueError("C 和 alpha 必须为正")
    return (
        constant * constant * alpha * alpha * (2 * alpha + 1) / (alpha + 2)
    ) ** (1 / (2 * alpha + 2))


def fit_pdf_zipf(counts: Sequence[int], *, min_frequency_exclusive: int = 3) -> dict:
    """Ordinary least squares of ``log f_r = log C - alpha log r``.

    Only frequencies strictly greater than ``min_frequency_exclusive`` enter
    the regression. Their ranks are 1..K in the already sorted vector, which
    matches the paper's high-frequency subset when the input is descending.
    """
    values = _as_descending_counts(counts)
    if isinstance(min_frequency_exclusive, bool) or int(min_frequency_exclusive) < 0:
        raise ValueError("频率门槛必须是非负整数")
    threshold = int(min_frequency_exclusive)
    fitted = [count for count in values if count > threshold]
    if len(fitted) < 3:
        raise ValueError("高于频率门槛的类别不足 3 个")
    count_n = len(fitted)
    sum_x = sum_y = sum_xx = sum_xy = 0.0
    logs = []
    for rank, frequency in enumerate(fitted, start=1):
        x = math.log(rank)
        y = math.log(frequency)
        logs.append(y)
        sum_x += x
        sum_y += y
        sum_xx += x * x
        sum_xy += x * y
    denominator = count_n * sum_xx - sum_x * sum_x
    if denominator == 0:
        raise ValueError("对数秩没有方差，无法回归")
    slope = (count_n * sum_xy - sum_x * sum_y) / denominator
    intercept = (sum_y - slope * sum_x) / count_n
    alpha = -slope
    if alpha <= 0:
        raise ValueError("拟合得到的 alpha 不是正数，不能计算曲率峰")
    constant = math.exp(intercept)
    mean_y = sum_y / count_n
    residual = total = 0.0
    for rank, y in enumerate(logs, start=1):
        predicted = intercept + slope * math.log(rank)
        residual += (y - predicted) ** 2
        total += (y - mean_y) ** 2
    x0 = curvature_maximum(constant, alpha)
    cutoff = max(1, min(len(values), int(math.floor(x0))))
    head_mass = int(sum(values[:cutoff]))
    total_mass = int(sum(values))
    return {
        "fit_version": FIT_VERSION,
        "min_frequency_exclusive": threshold,
        "fit_types": count_n,
        "omitted_low_frequency_types": len(values) - count_n,
        "C": constant,
        "alpha": alpha,
        "alpha_rounded_3dp": round(alpha, 3),
        "log_r_squared": 1 - residual / total if total else None,
        "curvature_x0": x0,
        "cutoff_rule": "floor(x0), then clipped to [1, number of ranks]",
        "cutoff_rank": cutoff,
        "head_types": cutoff,
        "tail_types": len(values) - cutoff,
        "head_mass": head_mass,
        "tail_mass": total_mass - head_mass,
        "total_mass": total_mass,
        "rounding_note": (
            "x0 是连续曲率峰，不是论文表里的整数排名。"
            "本实现用 floor(x0) 作为 HeadSet 的最后一个名次，不把结果改写成 1171。"
        ),
    }
