"""Exploratory fixed-total-budget check on one hak5 split.

Validation chooses the single model. The test score is not used to pick the
quota. Each model still only sees the training rows. This is not a frozen
confirmation of hypothesis H2.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.attack_stream import score_axes
from core.occurrence_frequency import load_occurrence_counter
from experiments.research19_smoke import frequency_attack, omen_attack, occurrence_split, pcfg_attack


ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ROOT / "local_datasets/maya/hak5/extracted/datasets/en/forum/hak5.pickle"
TOTAL = 900
SHARE = 300


def _point(ordered: list[str], targets: list[str], budget: int, *, completion: str, axis: str) -> dict:
    ledger = score_axes(
        ({"text": guess, "valid": True} for guess in ordered),
        targets, (budget,), completion=completion,
    )
    return ledger["axes"][axis]["points"][0]


def combine_raw_prefixes(raw_streams: dict[str, list[str]], share: int) -> dict:
    """Spend ``share`` raw emissions from each model, then describe the union.

    Cross-model repeats are overlap, not a resource cutoff. A model that
    emitted fewer than ``share`` raw lines is the only resource truncation.
    """
    prefixes = {}
    within_model = 0
    cross_model = 0
    unique: list[str] = []
    seen: set[str] = set()
    for name, stream in raw_streams.items():
        taken = list(stream[:share])
        local: set[str] = set()
        local_dups = 0
        cross_dups = 0
        for guess in taken:
            if guess in local:
                local_dups += 1
                within_model += 1
                continue
            local.add(guess)
            if guess in seen:
                cross_dups += 1
                cross_model += 1
                continue
            seen.add(guess)
            unique.append(guess)
        prefixes[name] = {
            "requested_raw": share,
            "raw_emitted": len(taken),
            "completion": "reached_budget" if len(taken) >= share else "resource_truncated",
            "within_model_duplicate_slots": local_dups,
            "cross_model_duplicate_slots": cross_dups,
        }
    raw_emitted = sum(item["raw_emitted"] for item in prefixes.values())
    requested = share * len(raw_streams)
    generation_completion = (
        "reached_budget"
        if all(item["completion"] == "reached_budget" for item in prefixes.values())
        else "resource_truncated"
    )
    return {
        "prefixes": prefixes,
        "generation_requested": requested,
        "generation_raw_emitted": raw_emitted,
        "generation_completion": generation_completion,
        "within_model_duplicate_slots": within_model,
        "cross_model_duplicate_slots": cross_model,
        "unique_verification_candidates": len(unique),
        "unique_order": unique,
    }


def main() -> int:
    meta, counts = load_occurrence_counter(PAYLOAD)
    split = occurrence_split(counts, seed=19)
    attacks = {
        "frequency": frequency_attack(split["train"], split["validation"], (TOTAL,), return_stream=True),
        "omen": omen_attack(split["train"], split["validation"], TOTAL, return_stream=True),
        "pcfg": pcfg_attack(split["train"], split["validation"], TOTAL, return_stream=True),
    }
    raw_streams = {name: attack.pop("ordered_raw") for name, attack in attacks.items()}
    for attack in attacks.values():
        attack.pop("ordered_unique", None)
    validation = {
        name: _point(raw_streams[name][:TOTAL], split["validation"], TOTAL, completion=(
            "reached_budget" if len(raw_streams[name]) >= TOTAL else "resource_truncated"
        ), axis="raw_position")
        for name in raw_streams
    }
    ranked = sorted(
        (name for name, point in validation.items() if point["cracked"] is not None),
        key=lambda name: (-validation[name]["cracked"], name),
    )
    best = ranked[0] if ranked else None
    test_best = None if best is None else _point(
        raw_streams[best][:TOTAL], split["test"], TOTAL,
        completion="reached_budget" if len(raw_streams[best]) >= TOTAL else "resource_truncated",
        axis="raw_position",
    )
    combined = combine_raw_prefixes(raw_streams, SHARE)
    unique_order = combined.pop("unique_order")
    unique_budget = _point(
        unique_order, split["test"], TOTAL, completion="unspecified", axis="unique_position",
    )
    emitted = _point(
        unique_order, split["test"], len(unique_order), completion="reached_budget", axis="unique_position",
    )
    report = {
        "protocol": "research19-v1",
        "site": "hak5",
        "seed": 19,
        "source_sha256": meta["source_sha256"],
        "preprocess_version": meta["preprocess_version"],
        "stage": "exploratory_not_frozen",
        "h2_confirmation": False,
        "total_generation_budget": TOTAL,
        "per_model_share": SHARE,
        "models": ["frequency", "omen", "pcfg"],
        "validation_cracked": validation,
        "selected_single_model": best,
        "test_selected_single_model": test_best,
        "generation": {key: value for key, value in combined.items()},
        "unique_verification_budget": unique_budget,
        "unique_verification_among_emitted": emitted,
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
        "generation_completion": report["generation"]["generation_completion"],
        "unique_emitted": emitted["cracked"],
        "unique_budget_900_incomplete": unique_budget["incomplete"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
