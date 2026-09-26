"""Exploratory fixed-total-budget check on one hak5 split.

Validation chooses the single model. The test score is not used to pick the
quota. Each model still only sees the training rows. This is not a frozen
confirmation of hypothesis H2.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.attack_stream import evaluate_ordered_stream
from core.occurrence_frequency import load_occurrence_counter
from experiments.research19_smoke import frequency_attack, omen_attack, occurrence_split, pcfg_attack


ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ROOT / "local_datasets/maya/hak5/extracted/datasets/en/forum/hak5.pickle"
TOTAL = 900
SHARE = 300


def _cracked(ordered: list[str], targets: list[str], budget: int) -> dict:
    usable = min(budget, len(ordered))
    point = evaluate_ordered_stream(ordered, targets, (usable,))["points"][0]
    point["requested_budget"] = budget
    point["unique_candidates"] = len(ordered)
    if len(ordered) < budget:
        point["incomplete"] = True
        point["reason"] = "唯一候选数少于请求预算，命中数只统计已经生成的候选"
    return point


def _union(streams: dict[str, list[str]], share: int) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for name in ("frequency", "omen", "pcfg"):
        for guess in streams[name][:share]:
            if guess in seen:
                continue
            seen.add(guess)
            merged.append(guess)
    return merged


def main() -> int:
    _meta, counts = load_occurrence_counter(PAYLOAD)
    split = occurrence_split(counts, seed=19)
    attacks = {
        "frequency": frequency_attack(split["train"], split["validation"], (TOTAL,), return_stream=True),
        "omen": omen_attack(split["train"], split["validation"], TOTAL, return_stream=True),
        "pcfg": pcfg_attack(split["train"], split["validation"], TOTAL, return_stream=True),
    }
    streams = {name: attack.pop("ordered_unique") for name, attack in attacks.items()}
    validation = {
        name: _cracked(streams[name], split["validation"], TOTAL)
        for name in streams
    }
    ranked = sorted(
        (name for name, point in validation.items() if point["cracked"] is not None),
        key=lambda name: (-validation[name]["cracked"], name),
    )
    best = ranked[0] if ranked else None
    test_best = None if best is None else _cracked(streams[best], split["test"], TOTAL)
    combined = _union(streams, SHARE)
    test_union = _cracked(combined, split["test"], TOTAL)
    report = {
        "protocol": "research19-v1",
        "site": "hak5",
        "seed": 19,
        "stage": "exploratory_not_frozen",
        "total_budget": TOTAL,
        "per_model_share": SHARE,
        "models": ["frequency", "omen", "pcfg"],
        "validation_cracked": validation,
        "selected_single_model": best,
        "test_selected_single_model": test_best,
        "test_equal_share_union": test_union,
        "union_unique_candidates": len(combined),
        "passgpt_passllm_not_included": True,
        "claim_supported": False,
        "plaintext_retained": False,
    }
    output = ROOT / "reports" / "research19" / "quota_hak5.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "best": best,
        "validation": {name: point["cracked"] for name, point in validation.items()},
        "test_best": None if test_best is None else test_best["cracked"],
        "test_union": test_union["cracked"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
