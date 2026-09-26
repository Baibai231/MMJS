"""Count an ordered guess stream without mixing it into a closed candidate ranking.

The generator receives training identifiers only. Held-out targets are an
argument of evaluation, never of preparation. This module does not train or
emit a password guesser.
"""
from __future__ import annotations

from typing import Iterable, Sequence


def prepare_generator(*, training_ids: Sequence[str], targets: Sequence[str] | None = None) -> dict:
    if targets is not None:
        raise ValueError("测试目标不能进入生成器")
    if any(not isinstance(item, str) or item == "" for item in training_ids):
        raise ValueError("训练标识必须是非空字符串")
    return {
        "training_count": len(training_ids),
        "targets_seen_by_generator": False,
    }


def account_emissions(records: Iterable[dict]) -> dict:
    """Count raw, valid, unique, duplicate, and out-of-domain emissions.

    Each record has ``text`` and ``valid``. ``in_domain`` defaults to true.
    An empty or invalid record still consumes one raw emission.
    A record with ``interrupted`` and without ``failed_emission`` is only a
    stop notice and does not consume a generation.
    """
    raw = 0
    valid_count = 0
    duplicate_count = 0
    outside_domain = 0
    seen: set[str] = set()
    unique: list[str] = []
    interrupted = False
    for record in records:
        if record.get("interrupted") and not record.get("failed_emission"):
            interrupted = True
            break
        raw += 1
        if record.get("interrupted"):
            interrupted = True
            break
        text = record.get("text")
        valid = bool(record.get("valid")) and isinstance(text, str) and text != ""
        if record.get("in_domain") is False:
            outside_domain += 1
        if not valid:
            continue
        valid_count += 1
        if text in seen:
            duplicate_count += 1
            continue
        seen.add(text)
        unique.append(text)
    return {
        "raw_emissions": raw,
        "valid_candidates": valid_count,
        "unique_candidates": len(unique),
        "duplicate_count": duplicate_count,
        "outside_domain": outside_domain,
        "interrupted": interrupted,
        "ordered_unique": unique,
    }


COMPLETIONS = {
    "reached_budget",
    "dictionary_exhausted",
    "resource_truncated",
    "failed",
    "interrupted",
    "unspecified",
}


def _axis_points(
    ordered: Sequence[str | None], targets: Sequence[str], budgets: Sequence[int], *,
    completion: str, axis: str, terminal: str,
) -> dict:
    position = {}
    for index, guess in enumerate(ordered, start=1):
        if isinstance(guess, str) and guess != "":
            position.setdefault(guess, index)
    produced = len(ordered)
    total = len(targets)
    points = []
    for budget in budgets:
        budget = int(budget)
        point_completion = completion
        if terminal == "interrupted":
            point_completion = "reached_before_interrupt" if budget <= produced else "interrupted"
        base = {
            "budget": budget,
            "axis": axis,
            "total": total,
            "produced": produced,
            "completion": point_completion,
        }
        if point_completion in {"failed", "interrupted"} or point_completion not in COMPLETIONS | {"reached_before_interrupt"}:
            points.append({**base, "cracked": None, "rate": None, "incomplete": True,
                           "reason": "生成没有正常结束，不能把短序列写成该预算已完成"})
            continue
        if budget > produced and completion != "dictionary_exhausted":
            points.append({**base, "cracked": None, "rate": None, "incomplete": True,
                           "reason": "该轴上的候选数没有达到预算，序列短本身不能证明已经穷尽"})
            continue
        cracked = sum(position.get(target, 10**18) <= min(budget, produced) for target in targets)
        note = None
        if budget > produced and completion == "dictionary_exhausted":
            note = "词典已经穷尽，更高预算没有新的候选"
        elif point_completion == "reached_before_interrupt":
            note = "这个预算在中断前已经发出"
        points.append({
            **base,
            "cracked": cracked,
            "rate": None if total == 0 else cracked / total,
            "incomplete": False,
            "reason": note,
        })
    return {"points": points, "produced": produced}


def score_axes(
    records: Iterable[dict], targets: Sequence[str], budgets: Sequence[int], *,
    completion: str, include_produced: bool = False,
) -> dict:
    """Score one emission log on three axes that must not share a budget label.

    ``raw_position`` counts every emission, including invalid output.
    ``valid_position`` counts valid emissions, including duplicates.
    ``unique_position`` counts the first time each valid string appears.
    A short list is incomplete unless ``completion`` is ``dictionary_exhausted``.
    An interrupted run keeps that as the terminal state even if the caller
    asked for ``reached_budget``. Budgets already emitted stay scorable.
    """
    if completion not in COMPLETIONS:
        raise ValueError(f"未知完成状态：{completion}")
    raw_slots: list[str | None] = []
    valid_order: list[str] = []
    unique: list[str] = []
    seen: set[str] = set()
    valid_count = duplicate_count = outside_domain = 0
    interrupted = False
    for record in records:
        if record.get("interrupted"):
            interrupted = True
            if record.get("failed_emission"):
                raw_slots.append(None)
            break
        text = record.get("text")
        valid = bool(record.get("valid")) and isinstance(text, str) and text != ""
        if record.get("in_domain") is False:
            outside_domain += 1
        if not valid:
            raw_slots.append(None)
            continue
        valid_count += 1
        raw_slots.append(text)
        valid_order.append(text)
        if text in seen:
            duplicate_count += 1
            continue
        seen.add(text)
        unique.append(text)
    terminal = "interrupted" if interrupted else completion
    budget_list = tuple(budgets)
    if include_produced:
        budget_list = tuple(sorted(set(budget_list) | {len(raw_slots), len(valid_order), len(unique)}))
    return {
        "raw_emissions": len(raw_slots),
        "valid_candidates": valid_count,
        "unique_candidates": len(unique),
        "duplicate_count": duplicate_count,
        "outside_domain": outside_domain,
        "interrupted": interrupted,
        "requested_completion": completion,
        "completion": terminal,
        "axes": {
            "raw_position": _axis_points(
                raw_slots, targets, budget_list, completion=completion, axis="raw_position", terminal=terminal,
            ),
            "valid_position": _axis_points(
                valid_order, targets, budget_list, completion=completion, axis="valid_position", terminal=terminal,
            ),
            "unique_position": _axis_points(
                unique, targets, budget_list, completion=completion, axis="unique_position", terminal=terminal,
            ),
        },
        "ordered_unique": unique,
        "plaintext_retained": False,
    }


def evaluate_ordered_stream(ordered: Sequence[str], targets: Sequence[str], budgets: Sequence[int]) -> dict:
    """Score an already built sequence on the unique-position axis.

    Callers that still have the original emission log should use ``score_axes``.
    Budgets beyond this sequence stay incomplete.
    """
    axis = _axis_points(
        list(ordered), targets, budgets, completion="unspecified", axis="unique_position", terminal="unspecified",
    )
    return {"points": axis["points"], "plaintext_retained": False}


def checkpoint_raw(counted: dict) -> dict:
    """Record how far a stream got so a later run can skip those raw lines.

    The checkpoint does not invent the missing suffix or turn it into zeros.
    """
    raw = int(counted["raw_emissions"])
    if raw < 0:
        raise ValueError("原始条数不能为负")
    return {
        "completed_raw": raw,
        "resume_from_raw_index": raw,
        "interrupted": bool(counted.get("interrupted")),
        "plaintext_retained": False,
    }
