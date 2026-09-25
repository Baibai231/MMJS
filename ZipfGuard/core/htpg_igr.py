"""Information gain ratio for HTPG head/tail labels.

Primary weighting is one row per distinct password (``unique_equal``).
Account-frequency weighting is always computed beside it. The paper does not
state which one it used, so the primary choice is declared here rather than
inferred from a figure. ``IV = 0`` yields a null score instead of a division
by zero. Missing feature values are left out of that feature only.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Mapping, Sequence


IGR_VERSION = "htpg-igr-v1"
PRIMARY_WEIGHTING = "unique_equal"
NUMERIC_FEATURES = ("length", "capital", "date", "keyboard", "lowercase", "word_type", "lastname")
CATEGORICAL_FEATURES = ("lsd_structure", "specplace")
ALL_FEATURES = (
    "lsd_structure", "length", "capital", "date", "keyboard",
    "specplace", "lowercase", "word_type", "lastname",
)


def _entropy(counts: Mapping[object, float]) -> float:
    total = float(sum(counts.values()))
    if total <= 0:
        return 0.0
    entropy = 0.0
    for count in counts.values():
        if count <= 0:
            continue
        probability = count / total
        entropy -= probability * math.log2(probability)
    return entropy


class _FeatureStats:
    def __init__(self) -> None:
        self.joint: dict[str, Counter] = {"unique": Counter(), "frequency": Counter()}
        self.head: dict[str, Counter] = {"unique": Counter(), "frequency": Counter()}
        self.tail: dict[str, Counter] = {"unique": Counter(), "frequency": Counter()}
        self.numeric_sum = {"unique": [0.0, 0.0], "frequency": [0.0, 0.0]}
        self.numeric_weight = {"unique": [0.0, 0.0], "frequency": [0.0, 0.0]}
        self.missing = {"unique": 0.0, "frequency": 0.0}

    def add(self, value: object, *, is_head: bool, frequency: int) -> None:
        if value is None:
            self.missing["unique"] += 1
            self.missing["frequency"] += frequency
            return
        key = value if isinstance(value, str) else int(value) if isinstance(value, bool) or isinstance(value, int) else value
        if isinstance(value, bool):
            key = int(value)
        side = 0 if is_head else 1
        for name, weight in (("unique", 1), ("frequency", frequency)):
            self.joint[name][key] += weight
            (self.head if is_head else self.tail)[name][key] += weight
            if isinstance(key, int) and not isinstance(key, bool):
                self.numeric_sum[name][side] += float(key) * weight
                self.numeric_weight[name][side] += weight


class IGRAccumulator:
    """Online head/tail counts. Password text is not stored."""

    def __init__(self) -> None:
        self.features = {name: _FeatureStats() for name in ALL_FEATURES}
        self.rows = 0
        self.head_rows = 0
        self.frequency_total = 0
        self.head_frequency = 0

    def add(self, features: Mapping[str, object], *, is_head: bool, frequency: int) -> None:
        if isinstance(frequency, bool) or int(frequency) <= 0:
            raise ValueError("frequency 必须是正整数")
        frequency = int(frequency)
        self.rows += 1
        self.frequency_total += frequency
        if is_head:
            self.head_rows += 1
            self.head_frequency += frequency
        for name in ALL_FEATURES:
            self.features[name].add(features.get(name), is_head=is_head, frequency=frequency)

    def scores(self) -> dict:
        rows = []
        for name in ALL_FEATURES:
            stats = self.features[name]
            row = {"feature": name, "kind": "categorical" if name in CATEGORICAL_FEATURES else "numeric"}
            for weighting in ("unique", "frequency"):
                ig, iv, igr, reason = _igr(stats, weighting)
                prefix = "unique" if weighting == "unique" else "frequency"
                row[f"ig_{prefix}"] = ig
                row[f"iv_{prefix}"] = iv
                row[f"igr_{prefix}"] = igr
                row[f"status_{prefix}"] = reason
                row[f"missing_{prefix}"] = stats.missing[weighting]
                row[f"head_summary_{prefix}"] = _summary(stats, weighting, head=True, numeric=row["kind"] == "numeric")
                row[f"tail_summary_{prefix}"] = _summary(stats, weighting, head=False, numeric=row["kind"] == "numeric")
            rows.append(row)
        ordered = sorted(
            rows,
            key=lambda item: (
                item["igr_unique"] is None,
                -(item["igr_unique"] or 0.0),
                item["feature"],
            ),
        )
        for index, row in enumerate(ordered, start=1):
            row["rank_unique"] = index
        by_frequency = sorted(
            rows,
            key=lambda item: (
                item["igr_frequency"] is None,
                -(item["igr_frequency"] or 0.0),
                item["feature"],
            ),
        )
        for index, row in enumerate(by_frequency, start=1):
            row["rank_frequency"] = index
        return {
            "igr_version": IGR_VERSION,
            "primary_weighting": PRIMARY_WEIGHTING,
            "primary_reason": "论文未说明加权方式；主口径事先指定为每个不同口令等权，账户频次加权只作敏感性对照。",
            "rows": self.rows,
            "head_rows": self.head_rows,
            "tail_rows": self.rows - self.head_rows,
            "frequency_total": self.frequency_total,
            "head_frequency": self.head_frequency,
            "features": ordered,
        }


def _class_counts(stats: _FeatureStats, weighting: str) -> tuple[float, float]:
    head = float(sum(stats.head[weighting].values()))
    tail = float(sum(stats.tail[weighting].values()))
    return head, tail


def _igr(stats: _FeatureStats, weighting: str) -> tuple[float | None, float | None, float | None, str]:
    head, tail = _class_counts(stats, weighting)
    total = head + tail
    if total <= 0:
        return None, None, None, "no_observed_values"
    hy = _entropy({"head": head, "tail": tail})
    conditional = 0.0
    feature_counts: Counter = stats.joint[weighting]
    for value, count in feature_counts.items():
        head_count = float(stats.head[weighting][value])
        tail_count = float(stats.tail[weighting][value])
        conditional += (count / total) * _entropy({"head": head_count, "tail": tail_count})
    ig = hy - conditional
    iv = _entropy(feature_counts)
    if iv == 0:
        return ig, iv, None, "iv_zero"
    return ig, iv, ig / iv, "ok"


def _summary(stats: _FeatureStats, weighting: str, *, head: bool, numeric: bool) -> dict:
    counts = (stats.head if head else stats.tail)[weighting]
    total = float(sum(counts.values()))
    if total <= 0:
        return {"observed": 0}
    if numeric:
        side = 0 if head else 1
        weight = stats.numeric_weight[weighting][side]
        mean = stats.numeric_sum[weighting][side] / weight if weight else None
        return {"observed": total, "mean": mean}
    mode, mode_count = sorted(counts.items(), key=lambda item: (-item[1], str(item[0])))[0]
    return {
        "observed": total,
        "mode": mode,
        "mode_mass": mode_count / total,
        "tie_break": "higher mass, then lexicographic value",
    }


def ranked_feature_names(report: Mapping[str, object]) -> list[str]:
    return [str(row["feature"]) for row in report["features"]]  # type: ignore[index]
