"""Per-password HTPG suggestions.

A password receives suggestions only when its raw-byte SHA-256 is in the
HeadSet built from the training corpus. Tail and unseen passwords get an
empty list. Categorical features do not have a scalar mean in the paper's
absolute-difference step, so closeness is mode membership: distance 0 when
the value equals that side's mode, otherwise 1. The paper counts a tie
``d_head <= d_tail`` as closer to the head. This generator is the
experimental strict rule: a suggestion is emitted only when ``d_head < d_tail``.
Equal distances produce no suggestion. The emitted record names that choice.
Lowercase is scored by IGR but Table IV has no policy for it, so it is not emitted.
"""
from __future__ import annotations

import hashlib
from typing import Mapping, Sequence

from core.htpg_features import HTPGFeatureExtractor
from core.htpg_igr import CATEGORICAL_FEATURES


GENERATOR_VERSION = "htpg-generator-v2"
_BOOLEAN_ACTIONS = {
    "capital": ("use_capital", "avoid_capital"),
    "date": ("use_date", "avoid_date"),
    "keyboard": ("use_keyboard_walk", "avoid_keyboard_walk"),
    "word_type": ("use_emotion_word", "avoid_emotion_word"),
    "lastname": ("use_lastname", "avoid_lastname"),
}
PAPER_POLICIES = {
    "length": "move_length_toward_tail_mean",
    "lsd_structure": "change_lsd_toward_tail_mode",
    "word_type": "move_emotion_word_toward_tail",
    "keyboard": "move_keyboard_toward_tail",
    "lastname": "move_lastname_toward_tail",
    "specplace": "move_special_placement_toward_tail_mode",
    "date": "move_date_toward_tail",
    "capital": "move_capital_toward_tail",
}


def password_digest(password: str | bytes) -> str:
    raw = password.encode("utf-8") if isinstance(password, str) else password
    return hashlib.sha256(raw).hexdigest()


def suggest_for_password(
    password: str,
    *,
    head_digests: set[str],
    igr_report: Mapping[str, object],
    extractor: HTPGFeatureExtractor,
    limit: int | None = None,
    weighting: str = "unique",
) -> dict:
    if not isinstance(password, str) or password == "":
        raise ValueError("建议对象必须是非空字符串")
    if limit is not None and (isinstance(limit, bool) or int(limit) <= 0):
        raise ValueError("limit 必须是正整数")
    digest = password_digest(password)
    if digest not in head_digests:
        return {
            "generator_version": GENERATOR_VERSION,
            "in_head_set": False,
            "suggestions": [],
            "conflicts_dropped": [],
            "reason": "tail_or_unseen",
        }
    if weighting not in ("unique", "frequency"):
        raise ValueError("weighting 只能是 unique 或 frequency")
    status_key = f"status_{weighting}"
    rank_key = f"rank_{weighting}"
    summary_head = f"head_summary_{weighting}"
    summary_tail = f"tail_summary_{weighting}"
    vector = extractor.extract(password).to_dict()
    ordered = sorted(
        igr_report["features"],  # type: ignore[index]
        key=lambda row: (row.get(rank_key) is None, row.get(rank_key) or 10**9, row["feature"]),
    )
    suggestions = []
    dropped = []
    seen_features: set[str] = set()
    inspected = 0
    for row in ordered:
        feature = str(row["feature"])
        if feature not in PAPER_POLICIES:
            continue
        if limit is not None and inspected >= int(limit):
            break
        inspected += 1
        if feature in seen_features:
            dropped.append({"feature": feature, "reason": "duplicate_feature"})
            continue
        decision = _closer_to_head(
            feature, vector.get(feature), row,
            status_key=status_key, summary_head=summary_head, summary_tail=summary_tail,
        )
        if decision is None:
            continue
        closer, d_head, d_tail, current, target = decision
        if not closer:
            continue
        seen_features.add(feature)
        suggestions.append({
            "feature": feature,
            "action": _action_name(feature, row[summary_head], row[summary_tail]),
            "igr_rank": row.get(rank_key),
            "igr": row.get(f"igr_{weighting}"),
            "weighting": weighting,
            "current": current,
            "target_description": target,
            "distance_head": d_head,
            "distance_tail": d_tail,
            "distance_definition": (
                "categorical_mode_0_or_1" if feature in CATEGORICAL_FEATURES else "absolute_difference_from_side_mean"
            ),
            "tie_policy": "experimental_strict: suggest only when d_head < d_tail; the paper counts d_head <= d_tail as head",
        })
    return {
        "generator_version": GENERATOR_VERSION,
        "in_head_set": True,
        "suggestions": suggestions,
        "conflicts_dropped": dropped,
        "reason": "head_set",
        "conflict_policy": "keep the earlier IGR order and drop a repeated feature",
    }


def _closer_to_head(
    feature: str, value: object, row: Mapping[str, object], *,
    status_key: str = "status_unique",
    summary_head: str = "head_summary_unique",
    summary_tail: str = "tail_summary_unique",
) -> tuple[bool, float, float, object, str] | None:
    if value is None or row.get(status_key) != "ok":
        return None
    head = row[summary_head]
    tail = row[summary_tail]
    if not isinstance(head, Mapping) or not isinstance(tail, Mapping):
        return None
    if feature in CATEGORICAL_FEATURES:
        if "mode" not in head or "mode" not in tail or head["mode"] == tail["mode"]:
            return None
        d_head = 0.0 if value == head["mode"] else 1.0
        d_tail = 0.0 if value == tail["mode"] else 1.0
        if d_head >= d_tail:
            return None
        target = f"tail mode {tail['mode']}"
    else:
        if head.get("mean") is None or tail.get("mean") is None:
            return None
        head_mean = float(head["mean"])
        tail_mean = float(tail["mean"])
        if abs(head_mean - tail_mean) < 1e-9:
            return None
        numeric = float(value)
        d_head = abs(head_mean - numeric)
        d_tail = abs(tail_mean - numeric)
        if d_head >= d_tail:
            return None
        target = f"tail mean {tail_mean:.4g}"
    return True, d_head, d_tail, value, target


def _action_name(feature: str, head: Mapping[str, object], tail: Mapping[str, object]) -> str:
    pair = _BOOLEAN_ACTIONS.get(feature)
    if pair is not None:
        return pair[0] if float(tail["mean"]) > float(head["mean"]) else pair[1]
    if feature == "length":
        return "increase_length" if float(tail["mean"]) > float(head["mean"]) else "decrease_length"
    if feature == "lsd_structure":
        return "change_lsd_toward_tail_mode"
    return "move_special_placement_toward_tail_mode"


def head_digests_from_rows(rows: Sequence[tuple[bytes, bool]]) -> set[str]:
    return {password_digest(password) for password, is_head in rows if is_head}
