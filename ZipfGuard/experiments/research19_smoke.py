"""Small real-data smoke for frequency and OMEN. Reports contain counts only.

OMEN is run in stdout mode (``-p``), which the author documents as having no
success feedback. The simulated-attack mode ``-s`` is not used. Guess text is
counted in memory and not written to the report.
"""
from __future__ import annotations

import json
import random
import subprocess
from collections import Counter
from pathlib import Path

from ai.pcfg_adapter import PCFGAttacker, PCFGConfig
from core.attack_stream import account_emissions, evaluate_ordered_stream
from core.occurrence_frequency import load_occurrence_counter


ROOT = Path(__file__).resolve().parents[1]
OMEN = ROOT / "local_attack_models" / "omen"
PAYLOAD = ROOT / "local_datasets/maya/hak5/extracted/datasets/en/forum/hak5.pickle"
WORK = ROOT / "local_attack_models" / "runs" / "hak5_omen"
BUDGETS = (100, 1000)


def occurrence_split(counts: Counter[str], seed: int = 19) -> dict[str, list[str]]:
    draw = random.Random(seed)
    rows = [password for password, count in counts.items() for _ in range(count)]
    counts.clear()
    draw.shuffle(rows)
    train_end = int(len(rows) * 0.6)
    validation_end = train_end + int(len(rows) * 0.2)
    return {
        "train": rows[:train_end],
        "validation": rows[train_end:validation_end],
        "test": rows[validation_end:],
    }


def frequency_attack(train: list[str], test: list[str], budgets: tuple[int, ...], *, return_stream: bool = False) -> dict:
    counts = Counter(train)
    ordered = sorted(counts, key=lambda word: (-counts[word], word))
    counts.clear()
    scored = evaluate_ordered_stream(ordered, test, budgets)
    train_set = set(ordered)
    return {
        "protocol": "train_frequency_open_dictionary",
        "test_frequency_used_for_ranking": False,
        "train_types": len(ordered),
        "test_rows": len(test),
        "test_rows_seen_in_train": sum(password in train_set for password in test),
        "points": scored["points"],
        **({"ordered_unique": ordered} if return_stream else {}),
    }


def omen_attack(train: list[str], test: list[str], limit: int, *, return_stream: bool = False) -> dict:
    WORK.mkdir(parents=True, exist_ok=True)
    train_path = WORK / "train.txt"
    train_path.write_text("\n".join(train) + "\n", encoding="utf-8")
    try:
        trained = subprocess.run(
            [str(OMEN / "createNG"), f"--iPwdList={train_path}"],
            cwd=WORK, check=False, capture_output=True, text=True,
        )
        if trained.returncode != 0:
            return {"status": "failed", "stage": "createNG", "returncode": trained.returncode}
        generated = subprocess.run(
            [str(OMEN / "enumNG"), "-p", "-m", str(limit)],
            cwd=WORK, check=False, capture_output=True, text=True,
        )
        if generated.returncode != 0:
            return {"status": "failed", "stage": "enumNG", "returncode": generated.returncode}
        lines = [line for line in generated.stdout.splitlines() if line]
        counted = account_emissions({"text": line, "valid": True} for line in lines)
        scored = evaluate_ordered_stream(counted["ordered_unique"], test, BUDGETS)
        return {
            "status": "completed",
            "mode": "stdout_no_success_feedback",
            "feedback_mode_used": False,
            "requested_raw_emissions": limit,
            "raw_emissions": counted["raw_emissions"],
            "unique_candidates": counted["unique_candidates"],
            "duplicate_count": counted["duplicate_count"],
            "points": scored["points"],
            **({"ordered_unique": counted["ordered_unique"]} if return_stream else {}),
        }
    finally:
        train_path.unlink(missing_ok=True)


def pcfg_attack(train: list[str], test: list[str], limit: int, *, return_stream: bool = False) -> dict:
    attacker = PCFGAttacker(PCFGConfig.workspace_default(
        ROOT.parent, generation_limit=limit, timeout_seconds=180,
    ))
    try:
        generated = attacker.generate_raw_stream(train, limit=limit)
    except Exception as exc:
        return {"status": "failed", "error": type(exc).__name__, "candidate_filter_applied": False}
    stream = generated.pop("stream")
    counted = account_emissions({"text": line, "valid": True} for line in stream)
    scored = evaluate_ordered_stream(stream, test, BUDGETS)
    return {
        "status": "completed",
        **generated,
        "unique_candidates": counted["unique_candidates"],
        "duplicate_count": counted["duplicate_count"],
        "points": scored["points"],
        **({"ordered_unique": counted["ordered_unique"]} if return_stream else {}),
    }


def main() -> int:
    meta, counts = load_occurrence_counter(PAYLOAD)
    split = occurrence_split(counts)
    report = {
        "protocol": "research19-v1",
        "site": "hak5",
        "split": "occurrence_60_20_20",
        "seed": 19,
        "same_string_may_cross_splits": True,
        "rows": {name: len(rows) for name, rows in split.items()},
        "source_sha256": meta["source_sha256"],
        "frequency": frequency_attack(split["train"], split["test"], BUDGETS),
        "omen": omen_attack(split["train"], split["test"], 1000),
        "pcfg_open_stream": pcfg_attack(split["train"], split["test"], 1000),
        "passgpt": {"status": "smoke_only", "report": "reports/research19/passgpt_smoke.json", "hit_rate": None},
        "passllm": {"status": "smoke_only", "report": "reports/research19/passllm_smoke.json", "hit_rate": None},
        "fla": {"status": "weights_present_runtime_missing", "reason": "TensorFlow is not installed for Python 3.14"},
        "passgan": {"status": "checkpoint_present_runtime_missing", "reason": "TensorFlow is not installed for Python 3.14"},
        "plaintext_retained": False,
        "claim_supported": False,
    }
    output = ROOT / "reports" / "research19" / "hak5_smoke.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "rows": report["rows"],
        "frequency": report["frequency"]["points"],
        "omen": {key: report["omen"].get(key) for key in ("status", "raw_emissions", "unique_candidates", "points")},
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
