"""Paper-style multi-round simulation on synthetic users.

Half the users keep their string. The other half adopt up to two suggestions.
This is the paper's simulated response, not a measured adoption rate. User
count is unchanged; nobody is deleted for refusing an edit.
"""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path

from core.htpg_features import HTPGFeatureExtractor
from experiments.robustness_protocol import BUDGET, _rank_frequency, public_candidates, sample_mechanism
from experiments.suggestion_compare import _apply_plan, _head_model, _paper_features
from core.metrics import evaluate_ranking


SIMULATION = "半数不改，其余最多采用两项建议。这是论文模拟条件，不是实测采用率。"


def iterate(*, size: int = 300, seed: int = 1, rounds: tuple[int, ...] = (1, 2, 5, 10), budget: int = BUDGET) -> dict:
    if any(int(round_index) < 1 for round_index in rounds):
        raise ValueError("轮次必须是正整数")
    scenario = sample_mechanism("zipf", size=size, seed=seed)
    users = scenario["train"] + scenario["validation"] + scenario["test"]
    population = len(users)
    extractor = HTPGFeatureExtractor(["joy", "happy"], ["smith", "li"])
    candidates = public_candidates("zipf")
    wanted = set(int(value) for value in rounds)
    history = []
    for step in range(1, max(wanted) + 1):
        model = _head_model(users, extractor)
        features = _paper_features(model)[:2]
        order = list(range(population))
        random.Random(seed + step).shuffle(order)
        adopters = set(order[: population // 2])
        updated = []
        for index, password in enumerate(users):
            if index not in adopters:
                updated.append(password)
                continue
            updated.append(_apply_plan([password], [f"iter-{step}-{index}"], features, model, "frequency")[0])
        if len(updated) != population:
            raise RuntimeError("迭代改变了人数")
        users = updated
        if step not in wanted:
            continue
        counts = Counter(users)
        ranking = _rank_frequency(users, candidates)
        evaluation = evaluate_ranking(ranking, users, budgets=(budget,))
        history.append({
            "round": step,
            "users": population,
            "adopters": population // 2,
            "unchanged": population - population // 2,
            "features": features,
            "cutoff_rank": model["fit"]["cutoff_rank"],
            "alpha_rounded_3dp": model["fit"]["alpha_rounded_3dp"],
            "head_mass": model["fit"]["head_mass"],
            "max_frequency": max(counts.values()),
            "unique_types": len(counts),
            "frequency_cracked_at_budget": evaluation["points"][0]["rate"],
            "denominator": "all_simulated_users",
        })
        counts.clear()
    return {
        "simulation": SIMULATION,
        "measured_adoption": False,
        "users_deleted_for_refusal": 0,
        "user_count_constant": all(row["users"] == population for row in history),
        "budget": budget,
        "budget_is_not_login_attempts": True,
        "rounds": history,
        "plaintext_retained": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="论文式多轮模拟，不删除拒绝修改的用户")
    parser.add_argument("--output", type=Path, default=Path("reports/htpg_iteration.json"))
    parser.add_argument("--size", type=int, default=300)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    payload = iterate(size=args.size, seed=args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for row in payload["rounds"]:
        print(
            f"round={row['round']} users={row['users']} max_frequency={row['max_frequency']} "
            f"cracked={row['frequency_cracked_at_budget']:.3f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
