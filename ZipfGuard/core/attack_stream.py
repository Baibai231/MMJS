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
    """
    raw = 0
    valid_count = 0
    duplicate_count = 0
    outside_domain = 0
    seen: set[str] = set()
    unique: list[str] = []
    interrupted = False
    for record in records:
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


def evaluate_ordered_stream(ordered: Sequence[str], targets: Sequence[str], budgets: Sequence[int]) -> dict:
    """First raw position in ``ordered`` is the guess rank. Missing targets stay uncracked."""
    position = {}
    for index, guess in enumerate(ordered, start=1):
        position.setdefault(guess, index)
    points = []
    total = len(targets)
    for budget in budgets:
        cracked = sum(position.get(target, 10**18) <= budget for target in targets)
        points.append({
            "budget": int(budget),
            "cracked": cracked,
            "total": total,
            "rate": None if total == 0 else cracked / total,
            "incomplete": False,
        })
    return {"points": points, "plaintext_retained": False}


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
