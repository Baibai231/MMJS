"""Train-frequency open dictionary on the smaller MAYA sites.

Sites above 200,000 distinct strings are recorded as incomplete instead of
being forced through an in-memory expansion. Test frequencies never rank guesses.
"""
from __future__ import annotations

import json
from pathlib import Path

from data.maya_catalog import dataset_names
from experiments.research19_smoke import BUDGETS, frequency_attack, occurrence_split
from core.occurrence_frequency import load_occurrence_counter


ROOT = Path(__file__).resolve().parents[1]
MAX_UNIQUE = 200_000


def main() -> int:
    rows = []
    for name in dataset_names():
        payload = next(Path(f"local_datasets/maya/{name}/extracted").rglob("*.pickle"))
        meta, counts = load_occurrence_counter(payload)
        unique = len(counts)
        if unique > MAX_UNIQUE:
            counts.clear()
            rows.append({
                "site": name,
                "status": "incomplete",
                "reason": f"独特字符串 {unique} 超过 {MAX_UNIQUE}，这次没有展开到内存里跑",
                "points": None,
            })
            continue
        split = occurrence_split(counts, seed=19)
        scored = frequency_attack(split["train"], split["test"], BUDGETS)
        rows.append({"site": name, "status": "completed", "source_sha256": meta["source_sha256"], **scored})
        print(name, scored["points"][-1]["cracked"], "/", scored["test_rows"], flush=True)
    report = {
        "protocol": "research19-v1",
        "attacker": "train_frequency_open_dictionary",
        "test_frequency_used_for_ranking": False,
        "budgets": list(BUDGETS),
        "rows": rows,
        "claim_supported": False,
        "plaintext_retained": False,
    }
    output = ROOT / "reports" / "research19" / "frequency_matrix.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
