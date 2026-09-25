"""Adoption-rate sensitivity when no user study exists.

Optimistic, neutral and conservative rates are assumptions set before looking
at a new test split. They are not measured memorability. Users who decline
keep their previous string, so the population size stays the same.
"""
from __future__ import annotations

import random
from typing import Sequence

from core.htpg_features import HTPGFeatureExtractor
from experiments.robustness_protocol import (
    BUDGET,
    _apply_named,
    _head_model,
    _modification_rate,
    _score_split,
    public_candidates,
    sample_mechanism,
)
from experiments.suggestion_compare import _paper_features


ADOPTION = {"optimistic": 1.0, "neutral": 0.5, "conservative": 0.2}


def _respond(
    passwords: Sequence[str], user_ids: Sequence[str], features: Sequence[str], model: dict,
    rate: float, seed: int, response_mode: str = "deterministic",
) -> tuple[list[str], dict]:
    """At most two offers. A refusal keeps the previous string. This is not a user study."""
    draw = random.Random(seed)
    chosen = []
    attempt_counts = []
    first_offers = 0
    first_accepts = 0
    changed = 0
    for password, user_id in zip(passwords, user_ids):
        current = password
        used = 0
        for feature in features[:2]:
            edited = _apply_named(
                [current], [user_id], [feature], model, "budget_cost", response_mode=response_mode,
            )[0]
            if edited == current:
                continue
            used += 1
            accept = draw.random() <= rate
            if used == 1:
                first_offers += 1
                first_accepts += int(accept)
            if accept:
                current = edited
                break
        attempt_counts.append(used)
        if current != password:
            changed += 1
        chosen.append(current)
    users = len(passwords) or 1
    return chosen, {
        "mean_attempts": sum(attempt_counts) / users,
        "initial_acceptance_rate": (first_accepts / first_offers) if first_offers else 0.0,
        "changed_rate": changed / users,
        "attempt_cap": 2,
        "measured_user_study": False,
    }


def sensitivity(*, size: int = 300, seed: int = 1, budget: int = BUDGET, response_mode: str = "deterministic") -> dict:
    scenario = sample_mechanism("zipf", size=size, seed=seed)
    candidates = public_candidates("zipf")
    model = _head_model(scenario["train"], HTPGFeatureExtractor(["joy", "happy"], ["smith", "li"]))
    features = _paper_features(model)[:2]
    rows = {}
    for name, rate in ADOPTION.items():
        train_after, _train_behavior = _respond(
            scenario["train"], scenario["train_id"], features, model, rate, seed + 17, response_mode,
        )
        validation_after, _validation_behavior = _respond(
            scenario["validation"], scenario["validation_id"], features, model, rate, seed + 18, response_mode,
        )
        test_after, behavior = _respond(
            scenario["test"], scenario["test_id"], features, model, rate, seed + 19, response_mode,
        )
        from experiments.robustness_protocol import edit_leak_counts
        from experiments.evaluation_validity import splits_allow_closed_publication
        audit = {
            "train": edit_leak_counts(scenario["train"], train_after, candidates),
            "validation": edit_leak_counts(scenario["validation"], validation_after, candidates),
            "test": edit_leak_counts(scenario["test"], test_after, candidates),
        }
        published = splits_allow_closed_publication(audit)
        adaptive = _score_split(train_after, test_after, candidates, budget, with_markov=False, with_ngram=False)
        baseline = _score_split(scenario["train"], scenario["test"], candidates, budget, with_markov=False, with_ngram=False)
        rows[name] = {
            "adoption_assumption": rate,
            "modification_rate": _modification_rate(scenario["test"], test_after),
            "mean_attempts": behavior["mean_attempts"],
            "initial_acceptance_rate": behavior["initial_acceptance_rate"],
            "changed_rate": behavior["changed_rate"],
            "measured_user_study": False,
            "absolute_point_change": None if not published else baseline["worst_rate"] - adaptive["worst_rate"],
            "headline_published": published,
            "candidate_audit": audit,
            "users": len(scenario["test"]),
        }
    return {
        "measured_memory": False,
        "completion_is_not_real_world": True,
        "note": "当前响应程序会继续改到模拟规则接受为止。这里的采用率是事先设定的情景，不是 100% 完成率，也不是论文的 80.23%。",
        "response_mode": response_mode,
        "adoption_grid": ADOPTION,
        "cli_adoption_rate_not_used": True,
        "fixed_protocol_note": "这组采用率是敏感性情景，不跟随主实验的单一采用率。响应模式跟随调用参数。",
        "features": features,
        "budget": budget,
        "denominator": "test_users",
        "rows": rows,
        "plaintext_retained": False,
    }
