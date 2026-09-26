"""Fixed occurrence and unique-disjoint splits. Reports store sizes, not strings."""
from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from pathlib import Path

from core.occurrence_frequency import load_occurrence_counter


ROOT = Path(__file__).resolve().parents[1]


def occurrence_rows(counts: Counter[str], seed: int) -> dict[str, list[str]]:
    draw = random.Random(seed)
    rows = [password for password, count in counts.items() for _ in range(count)]
    draw.shuffle(rows)
    train_end = int(len(rows) * 0.6)
    validation_end = train_end + int(len(rows) * 0.2)
    return {
        "train": rows[:train_end],
        "validation": rows[train_end:validation_end],
        "test": rows[validation_end:],
    }


def unique_disjoint_rows(counts: Counter[str], seed: int) -> dict[str, list[str]]:
    """Put every copy of one string into a single split."""
    draw = random.Random(seed)
    items = list(counts.items())
    draw.shuffle(items)
    quotas = {"train": 0.6, "validation": 0.2, "test": 0.2}
    loads = {name: 0 for name in quotas}
    chosen: dict[str, list[str]] = {name: [] for name in quotas}
    for password, count in items:
        name = min(quotas, key=lambda item: (loads[item] / quotas[item], item))
        chosen[name].extend([password] * count)
        loads[name] += count
    return chosen


def _digest(rows: list[str]) -> str:
    payload = "\n".join(sorted(set(rows))).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def manifest_for(counts: Counter[str], seed: int) -> dict:
    occurrence = occurrence_rows(counts, seed)
    disjoint = unique_disjoint_rows(counts, seed)
    def _side(rows: dict[str, list[str]], *, overlap_allowed: bool) -> dict:
        sets = {name: set(values) for name, values in rows.items()}
        return {
            "rows": {name: len(values) for name, values in rows.items()},
            "unique_strings": {name: len(values) for name, values in sets.items()},
            "string_overlap_train_test": len(sets["train"] & sets["test"]),
            "same_string_may_cross_splits": overlap_allowed,
            "sha256": {name: _digest(values) for name, values in rows.items()},
        }
    return {
        "seed": seed,
        "occurrence_60_20_20": _side(occurrence, overlap_allowed=True),
        "unique_disjoint": _side(disjoint, overlap_allowed=False),
        "plaintext_retained": False,
    }


def main() -> int:
    payload = ROOT / "local_datasets/maya/hak5/extracted/datasets/en/forum/hak5.pickle"
    meta, counts = load_occurrence_counter(payload)
    report = {
        "protocol": "research19-v1",
        "site": "hak5",
        "source_sha256": meta["source_sha256"],
        **manifest_for(counts, 19),
    }
    output = ROOT / "reports" / "research19" / "split_manifest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["unique_disjoint"]["string_overlap_train_test"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
