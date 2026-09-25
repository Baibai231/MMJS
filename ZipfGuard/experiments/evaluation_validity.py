"""Publication rules for closed-ranking results.

A closed gain that depends on an edit leaving the pre-registered candidate
list is diagnostic only. Open generation is judged on its own stream and is
not cleared just because the closed list leaked.
"""
from __future__ import annotations

from typing import Sequence


def closed_withheld_reason(candidate_audit: dict | None) -> str:
    if not candidate_audit:
        return "封闭比较不可发布"
    parts = []
    for split, row in candidate_audit.items():
        caused = int(row.get("edit_caused_outside") or 0)
        if caused:
            users = row.get("users", "?")
            parts.append(f"{split} 修改导致漏收 {caused}/{users}")
    if parts:
        return "；".join(parts)
    return "修改把原先在候选集内的口令推到了封闭候选之外，主指标不发布。"


def splits_allow_closed_publication(candidate_audit: dict) -> bool:
    return all(bool(row.get("headline_published")) for row in candidate_audit.values())


def finalize_closed_publication(row: dict, *, open_attackers: set[str]) -> None:
    """Blank closed derivatives when the arm is unpublished. Keep open intervals."""
    published = bool(row.get("headline_published"))
    if published:
        row["closed_diagnostics"] = None
        row["withheld_reason"] = None
        return
    reason = row.get("withheld_reason") or closed_withheld_reason(row.get("candidate_audit"))
    row["withheld_reason"] = reason
    paired = dict(row.get("paired_vs_none") or {})
    head = row.get("head_users") or {}
    row["closed_diagnostics"] = {
        "absolute_point_change_vs_none": row.get("withheld_point_change", row.get("absolute_point_change_vs_none")),
        "relative_change_vs_none": row.get("relative_change_vs_none"),
        "head_frequency_absolute_point_change": head.get("frequency_absolute_point_change"),
        "paired_vs_none": {name: interval for name, interval in paired.items() if name not in open_attackers},
        "reason": reason,
    }
    row["absolute_point_change_vs_none"] = None
    row["relative_change_vs_none"] = None
    if row.get("all_users") is not None:
        row["all_users"]["absolute_point_change"] = None
    if row.get("head_users") is not None:
        row["head_users"]["frequency_absolute_point_change"] = None
        row["head_users"]["published"] = False
    cleaned = {}
    for name, interval in paired.items():
        if name in open_attackers:
            cleaned[name] = interval
            continue
        cleaned[name] = {
            "point": None,
            "ci95": [None, None],
            "n": None if interval is None else interval.get("n"),
            "method": None if interval is None else interval.get("method"),
            "published": False,
            "reason": reason,
        }
    row["paired_vs_none"] = cleaned


def format_closed_metric(value, *, published: bool, reason: str | None = None) -> str:
    if not published or value is None:
        if reason:
            return f"未发布：{reason}"
        return "未发布"
    return f"{float(value):.3f}"


def seed_summary(values: Sequence[float | None], published_flags: Sequence[bool]) -> dict:
    """A complete-experiment mean exists only when every planned seed is publishable."""
    planned = len(list(values))
    paired = list(zip(values, published_flags))
    valid = [value for value, flag in paired if flag and value is not None]
    complete = planned > 0 and len(valid) == planned and all(published_flags)
    subset = (sum(valid) / len(valid)) if valid else None
    reason = None if complete else "有种子的封闭比较不可发布，不能把有效子集均值当作完整实验均值。"
    return {
        "planned_seeds": planned,
        "valid_seeds": len(valid),
        "withheld_seeds": planned - len(valid),
        "comparison_published": complete,
        "seeds": len(valid) if complete else 0,
        "mean": subset if complete else None,
        "min": min(valid) if complete else None,
        "max": max(valid) if complete else None,
        "diagnostic_valid_subset_mean": None if complete else subset,
        "unpublished_reason": reason,
        "withheld": 0 if complete else planned - len(valid),
    }
