"""Resample hak5 and compare curvature membership with a mass cutoff.

Only overlap counts are written. This does not establish hypothesis H1.
"""
from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

from core.htpg_fit import fit_pdf_zipf
from core.occurrence_frequency import load_occurrence_counter
from experiments.split_stability import mass_cutoff


ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ROOT / "local_datasets/maya/hak5/extracted/datasets/en/forum/hak5.pickle"


def _ordered(counts: Counter[str]) -> list[str]:
    return sorted(counts, key=lambda word: (-counts[word], word))


def _cutoff_members(counts: Counter[str], quantile: float) -> tuple[int, set[str]]:
    ordered = _ordered(counts)
    frequencies = [counts[word] for word in ordered]
    try:
        fitted = fit_pdf_zipf(frequencies)
        curvature = fitted["cutoff_rank"]
    except ValueError:
        curvature = None
    mass = mass_cutoff(frequencies, quantile)
    members = {
        "mass": set(ordered[:mass]),
        "curvature": set(ordered[:curvature]) if curvature else set(),
    }
    return curvature, members


def _jaccard(left: set[str], right: set[str]) -> float | None:
    if not left and not right:
        return None
    return len(left & right) / len(left | right)


def main() -> int:
    _meta, counts = load_occurrence_counter(PAYLOAD)
    rows = [password for password, count in counts.items() for _ in range(count)]
    base_curvature, base_members = _cutoff_members(counts, 0.5)
    counts.clear()
    draw = random.Random(19)
    overlaps = []
    for index in range(20):
        sample = rows[:]
        draw.shuffle(sample)
        kept = sample[: len(sample) // 2]
        sampled = Counter(kept)
        curvature, members = _cutoff_members(sampled, 0.5)
        overlaps.append({
            "replicate": index,
            "curvature_cutoff": curvature,
            "curvature_jaccard": _jaccard(base_members["curvature"], members["curvature"]),
            "mass_jaccard": _jaccard(base_members["mass"], members["mass"]),
        })
    def _mean(key: str) -> float | None:
        values = [row[key] for row in overlaps if row[key] is not None]
        return None if not values else sum(values) / len(values)
    report = {
        "protocol": "research19-v1",
        "site": "hak5",
        "replicates": 20,
        "subsample": "half of occurrence rows, without replacement",
        "base_curvature_cutoff": base_curvature,
        "mean_curvature_jaccard": _mean("curvature_jaccard"),
        "mean_mass_jaccard": _mean("mass_jaccard"),
        "rows": overlaps,
        "claim_supported": False,
        "plaintext_retained": False,
    }
    output = ROOT / "reports" / "research19" / "split_stability.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "curvature": report["mean_curvature_jaccard"],
        "mass": report["mean_mass_jaccard"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
