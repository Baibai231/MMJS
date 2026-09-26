"""One pre-registered cross-site run: train on hak5, score hotmail.

The target split is not shown to the generators. Reports keep counts only.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.occurrence_frequency import load_occurrence_counter
from experiments.research19_smoke import frequency_attack, omen_attack, pcfg_attack, occurrence_split


ROOT = Path(__file__).resolve().parents[1]
PAIR = ROOT / "configs" / "research19" / "transfer_pair.json"


def _pickle(site: str) -> Path:
    return next(Path(f"local_datasets/maya/{site}/extracted").rglob("*.pickle"))


def main() -> int:
    pair = json.loads(PAIR.read_text(encoding="utf-8"))
    budgets = tuple(pair["budgets"])
    train_meta, train_counts = load_occurrence_counter(ROOT / _pickle(pair["train_site"]))
    target_meta, target_counts = load_occurrence_counter(ROOT / _pickle(pair["target_site"]))
    train_split = occurrence_split(train_counts, seed=pair["seed"])
    target_split = occurrence_split(target_counts, seed=pair["seed"])
    if train_meta["preprocess_version"] != target_meta["preprocess_version"]:
        raise SystemExit("训练站和目标站的预处理版本不一致")
    train = train_split["train"]
    target = target_split["test"]
    report = {
        "protocol": "research19-v1",
        "pair": pair,
        "train_source_sha256": train_meta["source_sha256"],
        "target_source_sha256": target_meta["source_sha256"],
        "preprocess_version": train_meta["preprocess_version"],
        "train_rows": len(train),
        "target_test_rows": len(target),
        "frequency": frequency_attack(train, target, budgets),
        "omen": omen_attack(train, target, max(budgets)),
        "pcfg_open_stream": pcfg_attack(train, target, max(budgets)),
        "claim_supported": False,
        "plaintext_retained": False,
    }
    output = ROOT / "reports" / "research19" / "transfer_hak5_hotmail.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "frequency": report["frequency"]["points"],
        "omen": report["omen"].get("points"),
        "pcfg": report["pcfg_open_stream"].get("points"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
