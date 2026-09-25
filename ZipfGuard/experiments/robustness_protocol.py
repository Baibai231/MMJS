"""Same-budget comparison outside the 576-item grammar.

The generator, the public candidate list, and the ranking rule are separate.
Frequency and n-gram rank a hash-sorted candidate list. The Markov substitute
keeps raw emission indexes. Five arms share the split, the budget, and the
attacker set. Ablations change one switch at a time.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import random
import re
import time
import tracemalloc
from collections import Counter
from pathlib import Path
from typing import Sequence

from core.attackers import _fit_ngram
from core.htpg_features import HTPGFeatureExtractor, contains_keyboard_walk
from core.markov_substitute import NOT_OMEN, SUBSTITUTE_ID, enumerate_markov, raw_positions
from core.metrics import evaluate_open_generation, evaluate_ranking
from experiments.evaluation_validity import (
    finalize_closed_publication,
    format_closed_metric,
    seed_summary,
    splits_allow_closed_publication,
)
from experiments.provenance import robustness_manifest
from experiments.suggestion_compare import (
    COST_WEIGHT,
    _apply_plan,
    _head_model,
    _modification_rate,
    _paired_interval,
    _paper_features,
    adopts,
    apply_edit,
    blocked_length_edit,
    directed_coverage,
    executor_closure,
    shared_length_response,
    summarize_execution,
)
from policy.htpg_generator import PAPER_POLICIES, password_digest


PROTOCOL_VERSION = "robustness-v4"
BUDGET = 40
MECHANISMS = ("zipf", "long_tail", "unknown_structure")
YEAR = re.compile(r"(?:19|20)\d{2}$")
EXTRACTOR = HTPGFeatureExtractor(["joy", "happy"], ["smith", "li"])


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def support_strings(mechanism: str) -> list[str]:
    """Generator support in construction order. This order is not a ranking."""
    if mechanism not in (*MECHANISMS, "shifted", "head_shift"):
        raise ValueError(f"未知生成机制 {mechanism}")
    stems = [f"stem{index:02d}" for index in range(36)]
    suffixes = ("2024", "2025", "123", "88", "qwer", "asdf", "", "smith", "joy", "7", "99", "!")
    values = [stem + suffix for stem in stems for suffix in suffixes]
    values.extend(f"tail{index:04d}zeta" for index in range(220))
    return list(dict.fromkeys(values))


def held_out_strings(count: int = 80) -> list[str]:
    return [f"qxheld{index:04d}" for index in range(count)]


def hash_order(values: Sequence[str]) -> list[str]:
    return sorted(set(values), key=_sha)


def legacy_edit(password: str, salt: str) -> str:
    """Historical class-mix rule. Not a deployment recommendation."""
    if len(password) >= 8 and any(character.isalpha() for character in password) and any(character.isdigit() for character in password):
        return password
    bucket = hashlib.sha256(f"legacy|{salt}".encode("utf-8")).digest()[0] % 5
    updated = f"{password}a{bucket}"
    if len(updated) < 8:
        updated += "x" * (8 - len(updated))
    return updated


def edit_leak_counts(before: Sequence[str], after: Sequence[str], candidates: Sequence[str] | set[str]) -> dict:
    """Separate passwords that were already outside from passwords an edit pushed outside."""
    allowed = candidates if isinstance(candidates, set) else set(candidates)
    originally_outside = 0
    after_outside = 0
    edit_caused = 0
    for left, right in zip(before, after):
        left_inside = left in allowed
        right_inside = right in allowed
        originally_outside += int(not left_inside)
        after_outside += int(not right_inside)
        edit_caused += int(left_inside and not right_inside)
    users = len(before)
    return {
        "users": users,
        "originally_outside": originally_outside,
        "after_outside": after_outside,
        "edit_caused_outside": edit_caused,
        "headline_published": edit_caused == 0,
    }


@functools.lru_cache(maxsize=8)
def public_candidates(mechanism: str) -> tuple[str, ...]:
    """Policy-aware dictionary, hash-sorted, independent of draw order.

    Directed edits come from ``executor_closure``, which calls the same
    ``execute_suggestion`` path as the experiment. Test passwords are not added.
    """
    support = support_strings(mechanism)
    pool: set[str] = set(executor_closure(support, EXTRACTOR))
    for word in support:
        pool.add(word)
        pool.update(directed_coverage(word))
        for feature in PAPER_POLICIES:
            if feature == "length" and len(word) < 14:
                pool.update(f"{word}-p{bucket}" for bucket in range(5))
            elif feature == "lsd_structure":
                pool.update(f"{word}-s{bucket}" for bucket in range(5))
            else:
                pool.add(apply_edit(word, feature, salt=""))
        for bucket in range(5):
            updated = f"{word}a{bucket}"
            if len(updated) < 8:
                updated += "x" * (8 - len(updated))
            pool.add(updated)
        if len(word) < 8:
            pool.add(word + ("x" * (8 - len(word))))
        if len(word) < 15:
            pool.add(word + ("x" * (15 - len(word))))
        for minimum in (8, 14, 15):
            pool.add(shared_length_response(word, minimum, response_mode="deterministic"))
            for bucket in range(5):
                pool.add(shared_length_response(word, minimum, response_mode="diversified", bucket=bucket))
            pool.add(blocked_length_edit(word, response_mode="deterministic"))
            pool.add(blocked_length_edit(word, f"bucket-{bucket}", response_mode="diversified"))
    return tuple(hash_order(pool))


def _split(passwords: Sequence[str], train_ratio: float = 0.6, validation_ratio: float = 0.2):
    size = len(passwords)
    train_end = int(size * train_ratio)
    validation_end = train_end + int(size * validation_ratio)
    return list(passwords[:train_end]), list(passwords[train_end:validation_end]), list(passwords[validation_end:])


def _weights(size: int, exponent: float) -> list[float]:
    return [1 / ((index + 1) ** exponent) for index in range(size)]


def sample_mechanism(
    mechanism: str, *, size: int, seed: int, shared_template: bool = False,
) -> dict:
    if size < 300:
        raise ValueError("样本量至少 300")
    support = support_strings(mechanism)
    draw = random.Random(seed)
    if mechanism == "zipf":
        passwords = draw.choices(support, weights=_weights(len(support), 0.65), k=size)
        unknown = 0
    elif mechanism == "long_tail":
        head, tail = support[:24], support[24:]
        passwords = []
        for _ in range(size):
            if draw.random() < 0.55:
                passwords.append(draw.choices(head, weights=_weights(len(head), 0.4), k=1)[0])
            else:
                passwords.append(tail[draw.randrange(len(tail))])
        unknown = 0
    elif mechanism == "shifted":
        train_n = int(size * 0.6)
        validation_n = int(size * 0.2)
        test_n = size - train_n - validation_n
        train = draw.choices(support, weights=_weights(len(support), 0.45), k=train_n)
        validation = draw.choices(support, weights=_weights(len(support), 0.45), k=validation_n)
        test = draw.choices(support, weights=_weights(len(support), 1.35), k=test_n)
        passwords = None
        unknown = 0
    elif mechanism == "head_shift":
        passwords = draw.choices(support, weights=_weights(len(support), 0.65), k=size)
        unknown = 0
    else:
        passwords = draw.choices(support, weights=_weights(len(support), 0.8), k=size)
        unknown = 0
    if passwords is not None:
        train, validation, test = _split(passwords)
    if mechanism == "unknown_structure":
        foreign = held_out_strings()
        rewritten = []
        for index, password in enumerate(test):
            if draw.random() < 0.35:
                rewritten.append(foreign[index % len(foreign)])
                unknown += 1
            else:
                rewritten.append(password)
        test = rewritten
    if mechanism == "head_shift":
        head = set(support[:24])
        replacement = support[80:180]
        rewritten = []
        replaced = 0
        for index, password in enumerate(test):
            if password in head:
                rewritten.append(replacement[(index + seed) % len(replacement)])
                replaced += 1
            else:
                rewritten.append(password)
        test = rewritten
        unknown = replaced
    salt_mode = "shared" if shared_template else "per_user"

    def identifiers(prefix: str, count: int) -> list[str]:
        if shared_template:
            return ["shared"] * count
        return [f"{prefix}-{index:05d}" for index in range(count)]

    return {
        "mechanism": mechanism,
        "seed": seed,
        "size": size,
        "train": train,
        "validation": validation,
        "test": test,
        "train_id": identifiers("tr", len(train)),
        "validation_id": identifiers("va", len(validation)),
        "test_id": identifiers("te", len(test)),
        "unknown_test_count": unknown,
        "support_size": len(support),
        "salt_mode": salt_mode,
        "catalog_size": len(support),
        "train_exponent": 0.45 if mechanism == "shifted" else None,
        "test_exponent": 1.35 if mechanism == "shifted" else None,
    }


def _rank_frequency(train: Sequence[str], candidates: Sequence[str]) -> list[str]:
    """Rank the pre-registered closed list by training counts.

    Training strings outside that list are omitted. The omission is not a
    defense: an edit that creates such a string must withhold the headline.
    """
    allowed = candidates if isinstance(candidates, set) else set(candidates)
    counts = Counter(password for password in train if password in allowed)
    return sorted(counts, key=lambda value: (-counts[value], _sha(value)))


def _rank_ngram(train: Sequence[str], candidates: Sequence[str]) -> list[str]:
    model = _fit_ngram(train, 2, 0.3, public_candidates=list(candidates))
    return sorted(candidates, key=lambda value: (model.negative_log_likelihood(value), _sha(value)))


def _attack_closed(ranking: Sequence[str], samples: Sequence[str], budget: int) -> tuple[dict, list[bool]]:
    evaluation = evaluate_ranking(ranking, samples, budgets=(budget,))
    order = {value: index + 1 for index, value in enumerate(dict.fromkeys(ranking))}
    flags = [order.get(sample, float("inf")) <= budget for sample in samples]
    return evaluation, flags


def _attack_open(stream: Sequence[str], samples: Sequence[str], budget: int) -> tuple[dict, list[bool]]:
    evaluation = evaluate_open_generation(stream, samples, budgets=(budget,))
    positions = raw_positions(list(stream))
    order = {}
    for value, position in zip(stream, positions):
        order.setdefault(value, position)
    flags = [order.get(sample, float("inf")) <= budget for sample in samples]
    return evaluation, flags


def _worst(attacks: dict, flags: dict) -> tuple[float, float, dict]:
    """The headline rate stays inside one budget semantics.

    Closed ranking and open emission are both reported. They are not combined
    with a single maximum.
    """
    closed_names = [name for name, row in attacks.items() if row.get("protocol") == "closed_hash_order"]
    open_names = [name for name, row in attacks.items() if row.get("protocol") == "open_raw_emission_index"]

    def pick(names: list[str]) -> tuple[str | None, float | None, float | None]:
        if not names:
            return None, None, None
        attacker = max(names, key=lambda name: (attacks[name]["rate"], name))
        point = attacks[attacker]
        return attacker, point["rate"], point["coverage"]

    closed_attacker, closed_rate, closed_coverage = pick(closed_names)
    open_attacker, open_rate, _open_coverage = pick(open_names)
    if closed_attacker is not None:
        headline, headline_rate, headline_coverage = closed_attacker, closed_rate, closed_coverage
    else:
        headline, headline_rate, headline_coverage = open_attacker, open_rate, _open_coverage
    return headline_rate, headline_coverage, {
        "worst_attacker": headline,
        "worst_attacker_protocol": None if headline is None else attacks[headline]["protocol"],
        "closed_worst_attacker": closed_attacker,
        "closed_worst_rate": closed_rate,
        "open_worst_attacker": open_attacker,
        "open_worst_rate": open_rate,
        "budgets_not_mixed": True,
        "attacks": attacks,
        "flags": flags,
    }


def _score_split(
    train: Sequence[str], samples: Sequence[str], candidates: Sequence[str], budget: int,
    *, with_markov: bool, with_ngram: bool,
) -> dict:
    attacks = {}
    flags = {}
    rankings = [("frequency", _rank_frequency(train, candidates))]
    if with_ngram:
        rankings.append(("character-ngram", _rank_ngram(train, candidates)))
    for name, ranking in rankings:
        evaluation, cracked = _attack_closed(ranking, samples, budget)
        attacks[name] = {
            "rate": evaluation["points"][0]["rate"],
            "coverage": evaluation["coverage"],
            "protocol": "closed_hash_order",
        }
        flags[name] = cracked
    if with_markov:
        stream = enumerate_markov(list(train), limit=max(budget, 64))
        evaluation, cracked = _attack_open(stream, samples, budget)
        attacks[SUBSTITUTE_ID] = {
            "rate": evaluation["points"][0]["rate"],
            "coverage": evaluation["coverage"],
            "protocol": "open_raw_emission_index",
            "stream_length": len(stream),
        }
        flags[SUBSTITUTE_ID] = cracked
    worst_rate, worst_coverage, detail = _worst(attacks, flags)
    detail["worst_rate"] = worst_rate
    detail["worst_coverage"] = worst_coverage
    return detail


def _pattern_rates(passwords: Sequence[str]) -> dict:
    total = len(passwords) or 1
    return {
        "year_suffix_rate": sum(bool(YEAR.search(password)) for password in passwords) / total,
        "keyboard_walk_rate": sum(contains_keyboard_walk(password) for password in passwords) / total,
        "denominator": "passwords_in_this_split",
    }


def _concentration(passwords: Sequence[str]) -> float:
    if not passwords:
        return 0.0
    counts = Counter(passwords)
    peak = max(counts.values())
    counts.clear()
    return peak / len(passwords)


def _apply_named(
    passwords: Sequence[str], user_ids: Sequence[str], features: Sequence[str],
    model: dict, mode: str, weighting: str = "frequency", *,
    response_mode: str = "deterministic", adoption_rate: float = 1.0, adoption_seed: int = 0,
    trace: list | None = None,
) -> list[str]:
    if mode == "legacy_complexity":
        return [legacy_edit(password, user_id) for password, user_id in zip(passwords, user_ids)]
    if mode == "nist_single_factor_15":
        return _apply_shared_length(passwords, user_ids, 15, response_mode, adoption_rate, adoption_seed)
    if mode == "nist_mfa_8":
        return _apply_shared_length(passwords, user_ids, 8, response_mode, adoption_rate, adoption_seed)
    if mode == "modern_blocklist":
        rewritten = []
        for password, user_id in zip(passwords, user_ids):
            triggered = password_digest(password) in model["head_digests"] or len(password) < 8
            if triggered and adopts(user_id, "length", seed=adoption_seed, adoption_rate=adoption_rate):
                rewritten.append(blocked_length_edit(password, user_id, response_mode=response_mode))
            else:
                rewritten.append(password)
        return rewritten
    if mode == "no_curvature":
        model = dict(model)
        model["head_digests"] = {password_digest(password) for password in model["train_passwords"]}
        return _apply_plan(
            passwords, user_ids, features, model, weighting, trace,
            response_mode=response_mode, adoption_rate=adoption_rate, adoption_seed=adoption_seed,
        )
    if mode == "no_igr":
        return _apply_plan(
            passwords, user_ids, features, model, weighting, trace,
            response_mode=response_mode, adoption_rate=adoption_rate, adoption_seed=adoption_seed,
        )
    if mode == "per_password":
        changed = []
        for password, user_id in zip(passwords, user_ids):
            current = password
            for feature in features:
                edited = _apply_plan(
                    [current], [user_id], [feature], model, weighting, trace,
                    response_mode=response_mode, adoption_rate=adoption_rate, adoption_seed=adoption_seed,
                )[0]
                if edited != current:
                    current = edited
                    break
            changed.append(current)
        return changed
    return _apply_plan(
        passwords, user_ids, features, model, weighting, trace,
        response_mode=response_mode, adoption_rate=adoption_rate, adoption_seed=adoption_seed,
    )


def _apply_shared_length(
    passwords: Sequence[str], user_ids: Sequence[str], minimum: int,
    response_mode: str, adoption_rate: float, adoption_seed: int,
) -> list[str]:
    rewritten = []
    for password, user_id in zip(passwords, user_ids):
        if adopts(user_id, "length", seed=adoption_seed, adoption_rate=adoption_rate):
            rewritten.append(shared_length_response(
                password, minimum, user_id=user_id, response_mode=response_mode,
            ))
        else:
            rewritten.append(password)
    return rewritten


def _select_features(
    scenario: dict, model: dict, candidates: Sequence[str], budget: int, mode: str, *,
    response_mode: str = "deterministic", adoption_rate: float = 1.0,
) -> list[str]:
    if mode in {"none", "legacy_complexity", "modern_blocklist", "nist_single_factor_15", "nist_mfa_8"}:
        return []
    if mode == "paper_igr_unique":
        return _paper_features(model)
    if mode == "no_igr":
        return ["length", "lsd_structure"]
    use_cost = mode != "no_cost"
    use_budget = mode != "no_budget"
    adaptive = mode != "no_adaptive"
    apply_mode = "no_igr" if mode == "no_igr" else "no_curvature" if mode == "no_curvature" else "budget_cost"
    rows = []
    baseline = _score_split(
        scenario["train"], scenario["validation"], candidates, budget,
        with_markov=False, with_ngram=False,
    )
    for feature in PAPER_POLICIES:
        train_after = _apply_named(
            scenario["train"], scenario["train_id"], [feature], model, apply_mode,
            response_mode=response_mode, adoption_rate=adoption_rate, adoption_seed=scenario["seed"],
        )
        validation_after = _apply_named(
            scenario["validation"], scenario["validation_id"], [feature], model, apply_mode,
            response_mode=response_mode, adoption_rate=adoption_rate, adoption_seed=scenario["seed"],
        )
        attack_train = train_after if adaptive else scenario["train"]
        attacked = _score_split(
            attack_train, validation_after, candidates, budget,
            with_markov=False, with_ngram=False,
        )
        gain = baseline["worst_rate"] - attacked["worst_rate"]
        modification = _modification_rate(scenario["validation"], validation_after)
        score = (gain if use_budget else 0.0) - (COST_WEIGHT * modification if use_cost else 0.0)
        train_leak = edit_leak_counts(scenario["train"], train_after, candidates)
        validation_leak = edit_leak_counts(scenario["validation"], validation_after, candidates)
        if train_leak["edit_caused_outside"] or validation_leak["edit_caused_outside"]:
            score = float("-inf")
        rows.append((score, feature))
    rows.sort(key=lambda item: (-item[0], item[1]))
    chosen = [feature for score, feature in rows if score > 0]
    if mode == "per_password":
        return chosen
    return chosen[:2]


def evaluate_scenario(
    scenario: dict, *, budget: int = BUDGET, candidates: Sequence[str] | None = None,
    modes: Sequence[str] | None = None, with_markov: bool = True,
    response_mode: str = "deterministic", adoption_rate: float = 1.0,
) -> dict:
    started = time.perf_counter()
    tracing = tracemalloc.is_tracing()
    if not tracing:
        tracemalloc.start()
    candidates = list(candidates if candidates is not None else public_candidates(scenario["mechanism"]))
    if any(password in set(candidates) for password in held_out_strings()):
        raise RuntimeError("未知结构口令进入了公开候选集")
    scan_started = time.perf_counter()
    model = _head_model(scenario["train"], EXTRACTOR)
    model["train_passwords"] = list(scenario["train"])
    scan_seconds = time.perf_counter() - scan_started
    selected_modes = list(modes or (
        "none", "legacy_complexity", "nist_single_factor_15", "nist_mfa_8",
        "modern_blocklist", "paper_igr_unique", "budget_cost", "per_password",
    ))
    recommend_started = time.perf_counter()
    plans = {
        mode: _select_features(
            scenario, model, candidates, budget, mode,
            response_mode=response_mode, adoption_rate=adoption_rate,
        )
        for mode in selected_modes
    }
    recommendation_seconds = time.perf_counter() - recommend_started
    arms = {}
    flag_bank = {}
    attack_started = time.perf_counter()
    for mode in selected_modes:
        features = plans[mode]
        weighting = "unique" if mode == "paper_igr_unique" else "frequency"
        apply_mode = mode if mode in {
            "legacy_complexity", "modern_blocklist", "nist_single_factor_15", "nist_mfa_8",
            "no_curvature", "no_igr", "per_password",
        } else "budget_cost"
        if mode == "paper_igr_unique":
            apply_mode = "budget_cost"
        test_trace: list[dict] = []
        train_after = _apply_named(
            scenario["train"], scenario["train_id"], features, model, apply_mode, weighting,
            response_mode=response_mode, adoption_rate=adoption_rate, adoption_seed=scenario["seed"],
        )
        validation_after = _apply_named(
            scenario["validation"], scenario["validation_id"], features, model, apply_mode, weighting,
            response_mode=response_mode, adoption_rate=adoption_rate, adoption_seed=scenario["seed"],
        )
        test_after = _apply_named(
            scenario["test"], scenario["test_id"], features, model, apply_mode, weighting,
            response_mode=response_mode, adoption_rate=adoption_rate, adoption_seed=scenario["seed"],
            trace=test_trace,
        )
        candidate_audit = {
            "train": edit_leak_counts(scenario["train"], train_after, candidates),
            "validation": edit_leak_counts(scenario["validation"], validation_after, candidates),
            "test": edit_leak_counts(scenario["test"], test_after, candidates),
        }
        headline_published = all(split["headline_published"] for split in candidate_audit.values())
        frozen = _score_split(
            scenario["train"], test_after, candidates, budget,
            with_markov=with_markov, with_ngram=True,
        )
        adaptive = _score_split(
            train_after, test_after, candidates, budget,
            with_markov=with_markov, with_ngram=True,
        )
        flag_bank[mode] = {"frozen": frozen["flags"], "adaptive": adaptive["flags"]}
        arms[mode] = {
            "features": list(features),
            "test_modification_rate": _modification_rate(scenario["test"], test_after),
            "template_concentration": _concentration(test_after),
            "frozen_worst_rate": frozen["worst_rate"],
            "adaptive_worst_rate": adaptive["worst_rate"],
            "adaptive_worst_attacker": adaptive["worst_attacker"],
            "adaptive_worst_attacker_protocol": adaptive["worst_attacker_protocol"],
            "open_adaptive_worst_rate": adaptive["open_worst_rate"],
            "adaptive_coverage": adaptive["worst_coverage"],
            "adaptive_attacks": adaptive["attacks"],
            "patterns_after": _pattern_rates(test_after),
            "outside_candidate_rate": (
                candidate_audit["test"]["after_outside"] / candidate_audit["test"]["users"]
                if candidate_audit["test"]["users"] else 0.0
            ),
            "candidate_audit": candidate_audit,
            "headline_published": headline_published,
            "baseline_name": "head_triggered_length_response" if mode == "modern_blocklist" else mode,
            "comparison_role": "fixed_length_and_lsd_control" if mode == "no_igr" else mode,
            "weighting": weighting,
            "execution_status_counts": None if apply_mode in {
                "legacy_complexity", "modern_blocklist", "nist_single_factor_15", "nist_mfa_8",
            } else summarize_execution(test_trace),
        }
    head_index = [password_digest(password) in model["head_digests"] for password in scenario["test"]]

    def _masked_rate(flags: list[bool], mask: list[bool]) -> float | None:
        chosen = [flag for flag, keep in zip(flags, mask) if keep]
        if not chosen:
            return None
        return sum(chosen) / len(chosen)

    for mode, row in arms.items():
        use_frozen = mode == "no_adaptive"
        phase = "frozen" if use_frozen else "adaptive"
        baseline_rate = arms["none"]["frozen_worst_rate" if use_frozen else "adaptive_worst_rate"]
        compared_rate = row["frozen_worst_rate" if use_frozen else "adaptive_worst_rate"]
        point_change = baseline_rate - compared_rate
        row["absolute_point_change_vs_none"] = point_change if row["headline_published"] else None
        row["withheld_point_change"] = None if row["headline_published"] else point_change
        row["withheld_reason"] = None if row["headline_published"] else "修改把原先在候选集内的口令推到了封闭候选之外，主指标不发布。"
        row["comparison_phase"] = phase
        row["paired_vs_none"] = {
            attacker: _paired_interval(
                flag_bank["none"][phase][attacker], flag_bank[mode][phase][attacker],
                seed=scenario["seed"] + len(mode) + len(attacker),
            )
            for attacker in flag_bank["none"][phase]
        }
        before_flags = flag_bank["none"][phase]["frequency"]
        after_flags = flag_bank[mode][phase]["frequency"]
        head_before = _masked_rate(before_flags, head_index)
        head_after = _masked_rate(after_flags, head_index)
        row["all_users"] = {
            "denominator": "test_users",
            "users": len(scenario["test"]),
            "absolute_point_change": row["absolute_point_change_vs_none"],
        }
        row["head_users"] = {
            "denominator": "test_users_whose_original_password_is_in_the_train_head",
            "users": sum(head_index),
            "frequency_absolute_point_change": None if head_before is None else head_before - head_after,
        }
        row["relative_change_vs_none"] = (
            None if (not row["headline_published"] or baseline_rate == 0) else (baseline_rate - compared_rate) / baseline_rate
        )
        row["relative_change_note"] = "相对变化单独存放，不能与绝对百分点差混写成同一个百分数。"
        none_open = arms["none"]["open_adaptive_worst_rate"]
        this_open = row["open_adaptive_worst_rate"]
        row["open_absolute_point_change_vs_none"] = (
            None if none_open is None or this_open is None else none_open - this_open
        )
        row["open_change_note"] = "开放生成的百分点差单独计算，不并进封闭排序的最坏攻击者。"
        row["open_published"] = none_open is not None and this_open is not None
        finalize_closed_publication(row, open_attackers={SUBSTITUTE_ID})
    attack_seconds = time.perf_counter() - attack_started
    _current, peak_bytes = tracemalloc.get_traced_memory()
    if not tracing:
        tracemalloc.stop()
    saturated = (
        arms["none"]["adaptive_worst_rate"] >= 0.99
        and arms["none"]["adaptive_coverage"] >= 0.99
    )
    return {
        "protocol": PROTOCOL_VERSION,
        "mechanism": scenario["mechanism"],
        "seed": scenario["seed"],
        "size": scenario["size"],
        "budget": budget,
        "denominator": "test_users",
        "test_users": len(scenario["test"]),
        "head_test_users": sum(head_index),
        "support_size": scenario["support_size"],
        "candidate_count": len(candidates),
        "candidate_order": "sha256",
        "generator_order_used_as_ranking": False,
        "support_above_576": scenario["support_size"] > 576,
        "budget_below_candidate_count": budget < len(candidates),
        "unknown_test_count": scenario["unknown_test_count"],
        "salt_mode": scenario["salt_mode"],
        "main_budget_saturated": saturated,
        "main_budget_decided_by_576": scenario["support_size"] <= 576 and budget >= scenario["support_size"],
        "patterns_before": _pattern_rates(scenario["test"]),
        "arms": arms,
        "response_factor": {
            "mode": response_mode,
            "adoption_rate": adoption_rate,
            "adoption_seed": scenario["seed"],
            "shared_by": [
                "modern_blocklist", "nist_single_factor_15", "nist_mfa_8",
                "paper_igr_unique", "budget_cost", "per_password", "no_igr",
            ],
            "not_shared_by": ["legacy_complexity"],
            "legacy_note": "历史类别混合规则保留自己的改写，不参与策略和响应的交叉比较。",
            "no_igr_note": "特征固定为长度和 LSD。执行和拒绝规则与其他定向臂相同，不再使用无方向改写。",
            "blocklist_trigger_remains_arm_specific": True,
            "measured_user_study": False,
        },
        "test_used_for_selection": False,
        "selection_attackers": ["frequency"],
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "engineering": {
            "scan_seconds": round(scan_seconds, 3),
            "recommendation_seconds": round(recommendation_seconds, 3),
            "attack_seconds": round(attack_seconds, 3),
            "peak_memory_bytes": int(peak_bytes),
        },
        "threat": {
            "offline_guess_budget": budget,
            "online_login_budget": None,
            "budget_is_not_login_attempts": True,
            "denominator": "test_users",
        },
        "test_attackers": list(arms["none"]["adaptive_attacks"]),
        "markov_substitute": {
            "id": SUBSTITUTE_ID,
            "not_omen": NOT_OMEN,
            "open_positions": "raw emission index",
        },
        "plaintext_retained": False,
    }


def _mean_range(values: list[float | None]) -> dict:
    present = [value for value in values if value is not None]
    if not present:
        return {"seeds": 0, "mean": None, "min": None, "max": None, "withheld": len(values)}
    return {
        "seeds": len(present),
        "mean": sum(present) / len(present),
        "min": min(present),
        "max": max(present),
        "withheld": len(values) - len(present),
    }


def _optional_mean(values: list[float | None]) -> dict | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return _mean_range(present)


def multi_budget_curve(
    *, mechanism: str = "long_tail", size: int = 900, seed: int = 1,
    selection_budget: int = BUDGET, budgets: Sequence[int] = (10, 20, 40, 80),
    response_mode: str = "deterministic", adoption_rate: float = 1.0,
) -> dict:
    """Select once at the pre-registered budget, then score that plan at several budgets."""
    scenario = sample_mechanism(mechanism, size=size, seed=seed)
    candidates = public_candidates(mechanism)
    model = _head_model(scenario["train"], EXTRACTOR)
    model["train_passwords"] = list(scenario["train"])
    modes = ("none", "modern_blocklist", "paper_igr_unique", "budget_cost")
    plans = {
        mode: _select_features(
            scenario, model, candidates, selection_budget, mode,
            response_mode=response_mode, adoption_rate=adoption_rate,
        )
        for mode in modes
    }
    series = []
    for budget in budgets:
        point = {"budget": int(budget), "arms": {}}
        for mode in modes:
            features = plans[mode]
            weighting = "unique" if mode == "paper_igr_unique" else "frequency"
            apply_mode = "modern_blocklist" if mode == "modern_blocklist" else "budget_cost"
            train_after = _apply_named(
                scenario["train"], scenario["train_id"], features, model, apply_mode, weighting,
                response_mode=response_mode, adoption_rate=adoption_rate, adoption_seed=seed,
            )
            validation_after = _apply_named(
                scenario["validation"], scenario["validation_id"], features, model, apply_mode, weighting,
                response_mode=response_mode, adoption_rate=adoption_rate, adoption_seed=seed,
            )
            test_after = _apply_named(
                scenario["test"], scenario["test_id"], features, model, apply_mode, weighting,
                response_mode=response_mode, adoption_rate=adoption_rate, adoption_seed=seed,
            )
            audit = {
                "train": edit_leak_counts(scenario["train"], train_after, candidates),
                "validation": edit_leak_counts(scenario["validation"], validation_after, candidates),
                "test": edit_leak_counts(scenario["test"], test_after, candidates),
            }
            leak_published = splits_allow_closed_publication(audit)
            adaptive = _score_split(train_after, test_after, candidates, int(budget), with_markov=True, with_ngram=True)
            point["arms"][mode] = {
                "closed_rate": adaptive["closed_worst_rate"] if leak_published else None,
                "open_rate": adaptive["open_worst_rate"],
                "closed_attacker": adaptive["closed_worst_attacker"],
                "open_attacker": adaptive["open_worst_attacker"],
                "weighting": weighting,
                "headline_published": leak_published,
                "candidate_audit": audit,
                "withheld_reason": None if leak_published else "预算曲线沿用三阶段封闭审计，任一阶段的修改漏收都不发布封闭命中率。",
                "edit_caused_outside": audit["test"]["edit_caused_outside"],
            }
        series.append(point)
    return {
        "mechanism": mechanism,
        "size": size,
        "seed": seed,
        "selection_budget": selection_budget,
        "denominator": "test_users",
        "budget_is_not_login_attempts": True,
        "series": series,
        "closed_and_open_not_mixed": True,
        "selection_bias_note": "多条策略放在同一张表里时，挑最高的一行会夸大收益。预先指定的比较是预算/成本排序对论文 IGR 和长度加黑名单，不是表中最大值。",
        "plaintext_retained": False,
    }


def run_grid(
    *,
    seeds: Sequence[int] = (1, 2, 3, 4, 5),
    sizes: Sequence[int] = (300, 600, 900),
    mechanisms: Sequence[str] = MECHANISMS,
    budget: int = BUDGET,
    response_mode: str = "deterministic",
    adoption_rate: float = 1.0,
) -> dict:
    """Every seed is kept. The summary is the mean and the range, not the best seed."""
    grouped: dict[str, dict[int, list[dict]]] = {mechanism: {size: [] for size in sizes} for mechanism in mechanisms}
    for mechanism in mechanisms:
        candidates = public_candidates(mechanism)
        for size in sizes:
            for seed in seeds:
                scenario = sample_mechanism(mechanism, size=int(size), seed=int(seed))
                grouped[mechanism][int(size)].append(
                    evaluate_scenario(
                        scenario, budget=budget, candidates=candidates, with_markov=True,
                        response_mode=response_mode, adoption_rate=adoption_rate,
                    )
                )
    mechanisms_out = {}
    for mechanism, by_size in grouped.items():
        mechanisms_out[mechanism] = {}
        for size, rows in by_size.items():
            mechanisms_out[mechanism][str(size)] = {
                "support_size": rows[0]["support_size"],
                "candidate_count": rows[0]["candidate_count"],
                "saturated_seeds": sum(row["main_budget_saturated"] for row in rows),
                "decided_by_576_seeds": sum(row["main_budget_decided_by_576"] for row in rows),
                "arms": {
                    arm: seed_summary(
                        [row["arms"][arm]["absolute_point_change_vs_none"] for row in rows],
                        [bool(row["arms"][arm].get("headline_published")) for row in rows],
                    ) | {
                        "seeds_with_nonpositive_gain": sum(
                            row["arms"][arm]["absolute_point_change_vs_none"] is not None
                            and row["arms"][arm]["absolute_point_change_vs_none"] <= 0 for row in rows
                        ),
                        "frequency_paired_point_mean": (
                            _mean_range([
                                row["arms"][arm]["paired_vs_none"]["frequency"]["point"] for row in rows
                            ])["mean"]
                            if all(row["arms"][arm].get("headline_published") for row in rows)
                            else None
                        ),
                        "frequency_paired_lower_min": (
                            min(
                                row["arms"][arm]["paired_vs_none"]["frequency"]["ci95"][0] for row in rows
                            )
                            if all(
                                row["arms"][arm].get("headline_published")
                                and row["arms"][arm]["paired_vs_none"]["frequency"]["ci95"][0] is not None
                                for row in rows
                            )
                            else None
                        ),
                        "seeds_whose_frequency_paired_lower_bound_is_positive": sum(
                            (row["arms"][arm]["paired_vs_none"]["frequency"]["ci95"][0] or 0) > 0
                            and bool(row["arms"][arm].get("headline_published"))
                            for row in rows
                        ),
                        "modification_mean": _mean_range([
                            row["arms"][arm]["test_modification_rate"] for row in rows
                        ])["mean"],
                        "headline_protocol": "closed_hash_order",
                        "head_frequency_absolute_point_change": _optional_mean([
                            row["arms"][arm]["head_users"]["frequency_absolute_point_change"] for row in rows
                        ]),
                        "open_absolute_point_change": _optional_mean([
                            row["arms"][arm]["open_absolute_point_change_vs_none"] for row in rows
                        ]),
                        "outside_candidate_rate": _optional_mean([
                            row["arms"][arm]["outside_candidate_rate"] for row in rows
                        ]),
                    }
                    for arm in rows[0]["arms"]
                },
            }
    zipf_rows = grouped["zipf"][int(sizes[0])] if "zipf" in grouped else []
    pattern_shift = {
        "year_suffix_before": _mean_range([row["patterns_before"]["year_suffix_rate"] for row in zipf_rows]) if zipf_rows else None,
        "year_suffix_after_budget_cost": _mean_range([
            row["arms"]["budget_cost"]["patterns_after"]["year_suffix_rate"] for row in zipf_rows
        ]) if zipf_rows else None,
        "keyboard_before": _mean_range([row["patterns_before"]["keyboard_walk_rate"] for row in zipf_rows]) if zipf_rows else None,
        "keyboard_after_budget_cost": _mean_range([
            row["arms"]["budget_cost"]["patterns_after"]["keyboard_walk_rate"] for row in zipf_rows
        ]) if zipf_rows else None,
    }
    return {
        "protocol": PROTOCOL_VERSION,
        "budget": budget,
        "response_mode": response_mode,
        "adoption_rate": adoption_rate,
        "denominator": "test_users",
        "threat": {
            "offline_guess_budget": budget,
            "online_login_budget": None,
            "budget_is_not_login_attempts": True,
            "attacker_knows_policy_templates": True,
        },
        "seeds": [int(seed) for seed in seeds],
        "sizes": [int(size) for size in sizes],
        "mechanisms": mechanisms_out,
        "pattern_shift_zipf_smallest_size": pattern_shift,
        "markov_substitute": {"id": SUBSTITUTE_ID, "not_omen": NOT_OMEN},
        "interval_note": "每个种子有自己的配对区间。网格另报这些区间下界的最小值，不把不同种子合成一个用户。",
        "plaintext_retained": False,
        "claim": "合成协议上的同预算对照。不是真实口令防御结论，也不是 OMEN 复现。",
        "provenance": robustness_manifest(
            budget=budget, seeds=[int(seed) for seed in seeds], sizes=[int(size) for size in sizes],
        ),
    }


def run_ablation(
    *, seed: int = 1, size: int = 300, budget: int = BUDGET,
    response_mode: str = "deterministic", adoption_rate: float = 1.0,
) -> dict:
    """One frozen zipf scenario. Each row turns off a single switch."""
    scenario = sample_mechanism("zipf", size=size, seed=seed)
    shared = sample_mechanism("zipf", size=size, seed=seed, shared_template=True)
    candidates = public_candidates("zipf")
    modes = (
        "none", "budget_cost", "no_curvature", "no_igr", "no_budget", "no_cost", "no_adaptive",
    )
    full = evaluate_scenario(
        scenario, budget=budget, candidates=candidates, modes=modes, with_markov=True,
        response_mode=response_mode, adoption_rate=adoption_rate,
    )
    def forced_length(passwords: list[str], salts: list[str]) -> float:
        return _concentration([
            apply_edit(password, "length", salt=salt) for password, salt in zip(passwords, salts)
        ])
    rows = {}
    for mode in modes:
        if mode == "none":
            continue
        row = full["arms"][mode]
        rows[mode] = {
            "closed_absolute_point_change": row["absolute_point_change_vs_none"],
            "open_absolute_point_change": row["open_absolute_point_change_vs_none"],
            "head_frequency_absolute_point_change": row["head_users"]["frequency_absolute_point_change"],
            "features": row["features"],
            "switch_changed": "full method" if mode == "budget_cost" else mode,
        }
    rows["model_uncertainty"] = {
        "absolute_point_change_vs_none": None,
        "status": "absent",
        "switch_changed": "none",
        "note": "当前协议没有模型不确定性模块。不能靠删掉一个不存在的模块报告收益。",
    }
    return {
        "protocol": PROTOCOL_VERSION,
        "frozen": {
            "mechanism": "zipf", "seed": seed, "size": size, "budget": budget,
            "response_mode": response_mode, "adoption_rate": adoption_rate,
        },
        "rows": rows,
        "shared_template_concentration": forced_length(shared["test"], shared["test_id"]),
        "per_user_template_concentration": forced_length(scenario["test"], scenario["test_id"]),
        "template_note": "浓度比较的是同一长度修改在共享盐和逐用户盐下的结果，不是实测用户研究。",
        "plaintext_retained": False,
    }


def frequency_plan_check(
    mechanism: str, *, size: int = 900, seed: int = 1, budget: int = BUDGET,
    response_mode: str = "deterministic",
) -> dict:
    """Frequency-only selection diagnostic. It does not publish a leaked gain."""
    scenario = sample_mechanism(mechanism, size=size, seed=seed)
    candidates = public_candidates(mechanism)
    model = _head_model(scenario["train"], EXTRACTOR)
    model["train_passwords"] = list(scenario["train"])
    features = _select_features(
        scenario, model, candidates, budget, "budget_cost", response_mode=response_mode,
    )
    rewritten = {
        split: _apply_named(
            scenario[split], scenario[f"{split}_id"], features, model, "budget_cost", "frequency",
            response_mode=response_mode, adoption_seed=seed,
        )
        for split in ("train", "validation", "test")
    }
    leaks = {split: edit_leak_counts(scenario[split], rewritten[split], candidates) for split in rewritten}
    published = all(item["headline_published"] for item in leaks.values())
    baseline = _score_split(
        scenario["train"], scenario["test"], candidates, budget, with_markov=False, with_ngram=False,
    )
    adaptive = _score_split(
        rewritten["train"], rewritten["test"], candidates, budget, with_markov=False, with_ngram=False,
    )
    return {
        "mechanism": mechanism,
        "response_mode": response_mode,
        "features": features,
        "frequency_point_change": None if not published else baseline["worst_rate"] - adaptive["worst_rate"],
        "leaks": leaks,
        "headline_published": published,
        "closed_protocol": "closed_hash_order",
        "open_generation_not_mixed": True,
    }


def public_comparison_html(report: dict) -> str:
    """HTML table of arm rates. Unpublished closed gains show the reason, not zero."""
    rows = []
    for name, arm in report["arms"].items():
        published = bool(arm.get("headline_published", arm.get("absolute_point_change_vs_none") is not None))
        reason = arm.get("withheld_reason")
        head = arm.get("head_users") or {}
        rows.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{:.3f}</td></tr>".format(
                name,
                format_closed_metric(arm.get("absolute_point_change_vs_none"), published=published, reason=reason),
                format_closed_metric(
                    arm.get("open_absolute_point_change_vs_none"),
                    published=arm.get("open_published", arm.get("open_absolute_point_change_vs_none") is not None),
                    reason=None if arm.get("open_absolute_point_change_vs_none") is not None else "开放生成没有结果",
                ),
                format_closed_metric(
                    head.get("frequency_absolute_point_change"),
                    published=published,
                    reason=reason,
                ),
                arm["test_modification_rate"],
            )
        )
    return (
        "<section><h2>同预算对照 · 合成，不是 576 项语法</h2>"
        "<p>支持集 {} 项，公开候选 {} 项，离线预算 {}，测试用户 {}。"
        "马尔可夫不是 OMEN。这张表不是饱和的历史演示。</p>"
        "<table><thead><tr><th>策略</th><th>全体封闭百分点差</th><th>全体开放百分点差</th><th>头部频次百分点差</th><th>修改率</th></tr></thead><tbody>{}</tbody></table>"
        "<p>多条策略放在同一张表里时，挑最高的一行会夸大收益。预先指定的比较是预算/成本排序对论文 IGR 和长度加黑名单。</p></section>"
    ).format(report["support_size"], report["candidate_count"], report["budget"], report["test_users"], "".join(rows))


def main() -> int:
    parser = argparse.ArgumentParser(description="三机制、多样本、五种对照和消融")
    parser.add_argument("--output", type=Path, default=Path("reports/robustness_protocol.json"))
    parser.add_argument("--seeds", default="1,2,3,4,5")
    parser.add_argument("--sizes", default="300,600,900")
    parser.add_argument("--budget", type=int, default=BUDGET)
    parser.add_argument("--response-mode", default="deterministic", choices=("deterministic", "diversified"))
    parser.add_argument("--adoption-rate", type=float, default=1.0)
    args = parser.parse_args()
    seeds = tuple(int(item) for item in args.seeds.split(",") if item)
    sizes = tuple(int(item) for item in args.sizes.split(",") if item)
    from experiments.provenance import robustness_manifest
    from experiments.response_sensitivity import sensitivity
    payload = {
        "grid": run_grid(
            seeds=seeds, sizes=sizes, budget=args.budget,
            response_mode=args.response_mode, adoption_rate=args.adoption_rate,
        ),
        "ablation": run_ablation(
            seed=seeds[0], size=sizes[0], budget=args.budget,
            response_mode=args.response_mode, adoption_rate=args.adoption_rate,
        ),
        "response_sensitivity": sensitivity(
            size=sizes[0], seed=seeds[0], budget=args.budget, response_mode=args.response_mode,
        ),
        "provenance": robustness_manifest(
            budget=args.budget, seeds=list(seeds), sizes=list(sizes),
        ),
        "plaintext_retained": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for mechanism, by_size in payload["grid"]["mechanisms"].items():
        for size, row in by_size.items():
            gain = row["arms"]["budget_cost"]["mean"]
            published = row["arms"]["budget_cost"].get("comparison_published", gain is not None)
            shown = f"{gain:.4f}" if published and gain is not None else "未发布"
            print(
                f"{mechanism} n={size} budget_cost_mean_points={shown} "
                f"valid_seeds={row['arms']['budget_cost'].get('valid_seeds')} "
                f"planned_seeds={row['arms']['budget_cost'].get('planned_seeds')} "
                f"saturated_seeds={row['saturated_seeds']} support={row['support_size']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
