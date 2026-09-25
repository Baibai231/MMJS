"""Compare paper IGR order with a budget-and-cost order on the same edits.

Head labels, IGR and the chosen feature order are fit on train or validation.
The test split is scored once. Candidate strings are registered before the
test passwords are read, then sorted by hash so the dictionary order is not
the generator order. This is a closed ranking on that registered set.
"""
from __future__ import annotations

import hashlib
import math
import random
import re
from collections import Counter
from typing import Iterable, Sequence

from core.attackers import CharacterNgramAttacker, FrequencyAttacker
from core.htpg_features import (
    HTPGFeatureExtractor,
    char_class,
    contains_date,
    contains_keyboard_walk,
    date_spans,
    keyboard_walk_span,
    lsd_structure,
)
from core.htpg_fit import fit_pdf_zipf
from core.htpg_igr import IGRAccumulator
from core.metrics import evaluate_ranking
from policy.htpg_generator import PAPER_POLICIES, password_digest, suggest_for_password


COMPARE_VERSION = "suggestion-compare-v2"
EXECUTION_STATUSES = ("success", "already_satisfied", "unable", "conflict")
RESPONSE_MODES = ("deterministic", "diversified")
EXECUTOR_BOOLEAN_ACTIONS = (
    ("capital", "use_capital"),
    ("capital", "avoid_capital"),
    ("date", "use_date"),
    ("date", "avoid_date"),
    ("keyboard", "use_keyboard_walk"),
    ("keyboard", "avoid_keyboard_walk"),
    ("word_type", "use_emotion_word"),
    ("word_type", "avoid_emotion_word"),
    ("lastname", "use_lastname"),
    ("lastname", "avoid_lastname"),
)
COST_WEIGHT = 0.25
STEMS = tuple(f"stem{index:02d}" for index in range(60))
TAILS = tuple(f"long{index:02d}{suffix}" for index in range(12) for suffix in ("aa", "bb", "cc", "dd"))
HELD_OUT = tuple(f"heldout{index:03d}zeta" for index in range(80))


def catalog() -> list[str]:
    values = [stem + suffix for stem in STEMS for suffix in ("2024", "123", "qwer", "asdf", "")]
    values.extend(stem + "smith" for stem in STEMS[:20])
    values.extend(stem + "joy" for stem in STEMS[:20])
    values.extend(TAILS)
    return list(dict.fromkeys(values))


def _target_number(target: str | None) -> float | None:
    if not target:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", target)
    return float(match.group(0)) if match else None


def shared_length_response(
    password: str, minimum: int, *, user_id: str = "", response_mode: str = "deterministic", bucket: int | None = None,
) -> str:
    """One length pad shared by every arm that is allowed to change length.

    Deterministic mode repeats ``x``. Diversified mode appends one of five
    suffixes and then uses ``x`` only for the remaining characters. The
    trigger that decides whether to call this function stays with the arm.
    """
    if response_mode not in RESPONSE_MODES:
        raise ValueError(f"未知响应方式 {response_mode}")
    if minimum < 1 or len(password) >= minimum:
        return password
    if response_mode == "deterministic":
        return password + ("x" * (minimum - len(password)))
    chosen = hashlib.sha256(f"length|{user_id}".encode("utf-8")).digest()[0] % 5 if bucket is None else bucket
    updated = password + f"-p{chosen}"
    if len(updated) < minimum:
        updated += "x" * (minimum - len(updated))
    return updated


def adopts(user_id: str, feature: str, *, seed: int, adoption_rate: float) -> bool:
    """Shared refusal draw. Rate 1 always accepts. This is not a user study."""
    if adoption_rate >= 1:
        return True
    if adoption_rate <= 0:
        return False
    digest = hashlib.sha256(f"adopt|{seed}|{user_id}|{feature}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") / 2**32 < adoption_rate


def apply_edit(
    password: str, feature: str, salt: str = "", *, action: str | None = None, target: str | None = None,
    response_mode: str = "deterministic",
) -> str:
    """Apply one undirected historical response.

    Directed suggestions go through ``execute_suggestion``, which accepts a
    result only after ``HTPGFeatureExtractor`` confirms it. This function no
    longer implements those actions, so the old LSD, capital, and date edits
    cannot bypass that check.
    """
    if action in {"increase_length", "decrease_length"}:
        number = _target_number(target)
        if number is None:
            return password
        if action == "decrease_length":
            return password[:max(1, int(number))]
        return shared_length_response(
            password, int(math.ceil(number)), user_id=salt, response_mode=response_mode,
        )
    if action is not None:
        raise ValueError("有方向的建议必须由 execute_suggestion 用同一个特征提取器判定")
    if feature == "capital":
        return password.lower()
    if feature == "date":
        stripped = re.sub(r"(?:19|20)\d{2}$", "", password)
        return stripped or password + "x"
    if feature == "keyboard":
        stripped = re.sub(r"qwer|asdf|zxcv", "", password)
        return stripped or password + "x"
    if feature == "word_type":
        stripped = password.replace("joy", "")
        return stripped or password + "x"
    if feature == "lastname":
        stripped = re.sub(r"smith", "", password)
        return stripped or password + "x"
    if feature == "lsd_structure":
        bucket = hashlib.sha256(f"lsd|{salt}".encode("utf-8")).digest()[0] % 5
        return f"{password}-s{bucket}"
    if feature == "length":
        bucket = hashlib.sha256(f"length|{salt}".encode("utf-8")).digest()[0] % 5
        return password if len(password) >= 14 else f"{password}-p{bucket}"
    if feature == "specplace":
        return password[1:] + "!" if password.startswith("!") else password
    raise ValueError(f"没有对应的修改动作：{feature}")


def edit_meets_target(before: str, after: str, action: str | None, target: str | None, extractor: HTPGFeatureExtractor | None = None, feature: str | None = None) -> bool:
    """True only when the same extractor says the target already held or now holds.

    An unchanged string is not success unless the condition was already met.
    Unknown actions are not treated as success.
    """
    if extractor is None or feature is None or action is None:
        return False
    if not _condition_met(extractor, after, feature, action, target):
        return False
    if after == before:
        return _condition_met(extractor, before, feature, action, target)
    return True


def execute_suggestion(
    password: str, feature: str, action: str, target: str | None, extractor: HTPGFeatureExtractor,
    prior: Sequence[tuple[str, str, str | None]] = (), *, user_id: str = "", response_mode: str = "deterministic",
) -> dict:
    """Return one of success, already_satisfied, unable, conflict.

    Failure keeps the incoming string. A rewrite that meets its own target
    but breaks an earlier accepted target is conflict and is not adopted.
    """
    if _condition_met(extractor, password, feature, action, target):
        return {"status": "already_satisfied", "password": password, "feature": feature, "broken_features": []}
    updated = _rewrite(password, feature, action, target, extractor, user_id=user_id, response_mode=response_mode)
    if not updated or updated == password or not _condition_met(extractor, updated, feature, action, target):
        return {"status": "unable", "password": password, "feature": feature, "broken_features": []}
    broken = [
        earlier for earlier, earlier_action, earlier_target in prior
        if not _condition_met(extractor, updated, earlier, earlier_action, earlier_target)
    ]
    if broken:
        return {"status": "conflict", "password": password, "feature": feature, "broken_features": broken}
    return {"status": "success", "password": updated, "feature": feature, "broken_features": []}


def _condition_met(extractor: HTPGFeatureExtractor, password: str, feature: str, action: str, target: str | None) -> bool:
    if not password:
        return False
    observed = extractor.extract(password)
    if action == "use_capital":
        return observed.capital
    if action == "avoid_capital":
        return not observed.capital
    if action == "use_date":
        return observed.date
    if action == "avoid_date":
        return not observed.date
    if action == "use_keyboard_walk":
        return observed.keyboard
    if action == "avoid_keyboard_walk":
        return not observed.keyboard
    if action == "use_emotion_word":
        return observed.word_type is True
    if action == "avoid_emotion_word":
        return observed.word_type is False
    if action == "use_lastname":
        return observed.lastname is True
    if action == "avoid_lastname":
        return observed.lastname is False
    if action in {"change_lsd_toward_tail_mode", "move_special_placement_toward_tail_mode"}:
        mode = _mode_token(target)
        if mode is None:
            return False
        current = observed.lsd_structure if action.startswith("change_lsd") else observed.specplace
        return current == mode
    if action == "increase_length":
        number = _target_number(target)
        return number is not None and len(password) >= int(math.ceil(number))
    if action == "decrease_length":
        number = _target_number(target)
        return number is not None and 1 <= len(password) <= max(1, int(number))
    return False


def _mode_token(target: str | None) -> str | None:
    if not target:
        return None
    marker = "tail mode "
    if marker not in target:
        return None
    return target.split(marker, 1)[1].strip()


def _rewrite(
    password: str, feature: str, action: str, target: str | None, extractor: HTPGFeatureExtractor,
    *, user_id: str = "", response_mode: str = "deterministic",
) -> str | None:
    if action == "use_capital":
        chars = list(password)
        for index, char in enumerate(chars):
            if char.isalpha() and char.islower():
                chars[index] = char.upper()
                return "".join(chars)
        return None
    if action == "avoid_capital":
        return password.lower()
    if action == "avoid_date":
        return _without_dates(password)
    if action == "use_date":
        for suffix in ("2024", "x2024"):
            candidate = password + suffix
            if contains_date(candidate):
                return candidate
        return None
    if action == "change_lsd_toward_tail_mode":
        realized = _realize_lsd(password, _mode_token(target))
        if realized is None or lsd_structure(realized) != _mode_token(target):
            return None
        return realized
    if action == "move_special_placement_toward_tail_mode":
        return _realize_specplace(password, _mode_token(target))
    if action in {"avoid_emotion_word", "avoid_lastname"}:
        terms = extractor.word_terms if action == "avoid_emotion_word" else extractor.surname_terms
        attribute = "word_type" if action == "avoid_emotion_word" else "lastname"
        return _without_terms(password, terms, extractor, attribute)
    if action == "use_emotion_word":
        term = extractor.word_terms[0] if extractor.word_terms else None
        return None if term is None else password + term
    if action == "use_lastname":
        term = extractor.surname_terms[0] if extractor.surname_terms else None
        return None if term is None else password + term
    if action == "use_keyboard_walk":
        for suffix in ("qwer", "xqwer"):
            candidate = password + suffix
            if contains_keyboard_walk(candidate):
                return candidate
        return None
    if action == "avoid_keyboard_walk":
        return _without_keyboard(password)
    if action in {"increase_length", "decrease_length"}:
        return apply_edit(password, feature, salt=user_id, action=action, target=target, response_mode=response_mode)
    return None


def _without_dates(password: str) -> str | None:
    updated = password
    for _ in range(16):
        spans = date_spans(updated)
        if not spans:
            return updated or None
        start, end = spans[-1]
        updated = updated[:start] + updated[end:]
        if not updated:
            return None
    return None


def _without_keyboard(password: str) -> str | None:
    updated = password
    for _ in range(32):
        span = keyboard_walk_span(updated)
        if span is None:
            return updated or None
        start, end = span
        updated = updated[:start] + updated[end:]
        if not updated:
            return None
    return None


def _without_terms(password: str, terms: Sequence[str], extractor: HTPGFeatureExtractor, attribute: str) -> str | None:
    updated = password
    for _ in range(64):
        if not updated:
            return None
        if getattr(extractor.extract(updated), attribute) is False:
            return updated
        replacement = None
        for term in sorted(terms, key=len, reverse=True):
            candidate = re.sub(re.escape(term), "", updated, count=1, flags=re.IGNORECASE)
            if candidate != updated:
                replacement = candidate
                break
        if replacement is None:
            return None
        updated = replacement
    return None


def _realize_specplace(password: str, mode: str | None) -> str | None:
    if mode == "none":
        kept = "".join(char for char in password if char_class(char) != "S")
        return kept or None
    if not mode:
        return None
    places = set(mode.split("+"))
    ordered = "+".join(place for place in ("head", "middle", "tail") if place in places)
    if places - {"head", "middle", "tail"} or ordered != mode:
        return None
    special = next((char for char in password if char_class(char) == "S"), "!")
    body = "".join(char for char in password if char_class(char) != "S") or "ab"
    if len(body) < 2:
        body = body + "b"
    chunks = [special] if "head" in places else []
    chunks.append(body[0] + special + body[1:] if "middle" in places else body)
    if "tail" in places:
        chunks.append(special)
    return "".join(chunks)


def _realize_lsd(password: str, mode: str | None) -> str | None:
    if not mode:
        return None
    parts = re.findall(r"([LDS])(\d+)", mode)
    if not parts or "".join(f"{kind}{count}" for kind, count in parts) != mode:
        return None
    letters = [char for char in password if char.isalpha()] or ["x"]
    digits = [char for char in password if char.isdecimal()] or ["1"]
    specials = [char for char in password if not char.isalnum()] or ["!"]
    pools = {"L": letters, "D": digits, "S": specials}
    rendered = []
    for kind, count in parts:
        pool = pools[kind]
        rendered.extend(pool[index % len(pool)] for index in range(int(count)))
    return "".join(rendered)


def length_bucket_user_ids() -> tuple[str, ...]:
    """One user id for each diversified length suffix. Experiment ids hash into these five."""
    found: dict[int, str] = {}
    index = 0
    while len(found) < 5:
        user_id = f"cover-{index}"
        bucket = hashlib.sha256(f"length|{user_id}".encode("utf-8")).digest()[0] % 5
        found.setdefault(bucket, user_id)
        index += 1
    return tuple(found[bucket] for bucket in range(5))


def blocked_length_edit(password: str, user_id: str = "", *, response_mode: str = "deterministic") -> str:
    """Head-triggered length response. A blocked string must change, including when it is already long."""
    minimum = 14 if len(password) < 14 else len(password) + 1
    return shared_length_response(password, minimum, user_id=user_id, response_mode=response_mode)


def public_executor_actions(words: Sequence[str], extractor: HTPGFeatureExtractor) -> list[tuple[str, str, str | None]]:
    """Actions the directed executor can be asked to perform on this support.

    Targets come from support strings and the public length bounds, not from test passwords.
    """
    actions: list[tuple[str, str, str | None]] = [(feature, action, None) for feature, action in EXECUTOR_BOOLEAN_ACTIONS]
    structures = sorted({extractor.extract(word).lsd_structure for word in words})
    places = sorted({extractor.extract(word).specplace for word in words})
    actions.extend(
        ("lsd_structure", "change_lsd_toward_tail_mode", f"tail mode {structure}") for structure in structures
    )
    actions.extend(
        ("specplace", "move_special_placement_toward_tail_mode", f"tail mode {place}") for place in places
    )
    longest = max((len(word) for word in words), default=1)
    for length in range(1, longest + 3):
        actions.append(("length", "increase_length", f"tail mean {length}"))
        actions.append(("length", "decrease_length", f"tail mean {length}"))
    return actions


def _executor_successes(
    password: str, actions: Sequence[tuple[str, str, str | None]], extractor: HTPGFeatureExtractor,
    response_mode: str, user_ids: Sequence[str],
) -> set[str]:
    found: set[str] = set()
    for feature, action, target in actions:
        identities = user_ids if action in {"increase_length", "decrease_length"} else ("",)
        for user_id in identities:
            result = execute_suggestion(
                password, feature, action, target, extractor,
                user_id=user_id, response_mode=response_mode,
            )
            if result["status"] == "success":
                found.add(result["password"])
    return found


def executor_closure(words: Sequence[str], extractor: HTPGFeatureExtractor) -> set[str]:
    """One-step and two-step outputs of ``execute_suggestion`` for both response modes.

    This is the closed-candidate generator. A hand-written edit list must not replace it.
    """
    material = list(dict.fromkeys(words))
    actions = public_executor_actions(material, extractor)
    user_ids = length_bucket_user_ids()
    pool = set(material)
    step = set()
    for word in material:
        for response_mode in RESPONSE_MODES:
            step |= _executor_successes(word, actions, extractor, response_mode, user_ids)
        step.add(blocked_length_edit(word, response_mode="deterministic"))
        for user_id in user_ids:
            step.add(blocked_length_edit(word, user_id, response_mode="diversified"))
    pool |= step
    second = set()
    for word in step:
        for response_mode in RESPONSE_MODES:
            second |= _executor_successes(word, actions, extractor, response_mode, user_ids)
    pool |= second
    return pool


def directed_coverage(word: str) -> set[str]:
    """Forms a directed response can produce, so they can be pre-registered."""
    bases = {word, word.lower()}
    if word and word[:1].islower():
        bases.add(word[:1].upper() + word[1:])
    covered = set(bases)
    for base in bases:
        for length in range(1, len(base) + 9):
            covered.add(base[:length] if length <= len(base) else base + ("x" * (length - len(base))))
        stripped = re.sub(r"(?:19|20)\d{2}$", "", base)
        covered.add(stripped or base + "x")
        if not re.search(r"(?:19|20)\d{2}$", base):
            covered.add(base + "2024")
        without_walk = re.sub(r"qwer|asdf|zxcv", "", base)
        covered.add(without_walk or base + "x")
        if "qwer" not in base:
            covered.add(base + "qwer")
        covered.add(base.replace("joy", "") or base + "x")
        covered.add(re.sub(r"smith", "", base) or base + "x")
        if not base.endswith("!"):
            covered.add(base + "!")
    once = set(covered)
    for item in once:
        covered.add(item + "smith")
        covered.add(item + "joy")
        covered.add(item + "2024")
        covered.add(item + "qwer")
        if not item.endswith("!"):
            covered.add(item + "!")
        if item[:1].islower():
            covered.add(item[:1].upper() + item[1:])
        covered.add(item.lower())
    return covered


def registered_candidates(words: Iterable[str]) -> list[str]:
    pool = set(words)
    for word in list(pool):
        for feature in PAPER_POLICIES:
            for bucket in range(5):
                pool.add(apply_edit(word, feature, salt=f"register-{bucket}"))
    return sorted(pool, key=lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest())


def _split_passwords(passwords: Sequence[str], train_ratio: float, validation_ratio: float):
    size = len(passwords)
    train_end = int(size * train_ratio)
    validation_end = train_end + int(size * validation_ratio)
    return passwords[:train_end], passwords[train_end:validation_end], passwords[validation_end:]


def sample_scenario(
    *, size: int = 900, seed: int = 7, exponent: float = 0.65,
    unknown_test_fraction: float = 0.0,
) -> dict:
    if size < 300:
        raise ValueError("对照场景至少 300 个用户")
    words = catalog()
    weights = [1 / ((index + 1) ** exponent) for index in range(len(words))]
    draw = random.Random(seed)
    passwords = draw.choices(words, weights=weights, k=size)
    train, validation, test = _split_passwords(passwords, 0.6, 0.2)
    if unknown_test_fraction:
        replaced = 0
        rewritten = []
        for index, password in enumerate(test):
            if draw.random() < unknown_test_fraction:
                rewritten.append(HELD_OUT[index % len(HELD_OUT)])
                replaced += 1
            else:
                rewritten.append(password)
        test = rewritten
    else:
        replaced = 0
    return {
        "train": train, "validation": validation, "test": test,
        "train_id": [f"tr-{index:05d}" for index in range(len(train))],
        "validation_id": [f"va-{index:05d}" for index in range(len(validation))],
        "test_id": [f"te-{index:05d}" for index in range(len(test))],
        "catalog_size": len(words), "unknown_test_count": replaced,
        "seed": seed, "exponent": exponent, "size": size,
    }


def _head_model(train: Sequence[str], extractor: HTPGFeatureExtractor) -> dict:
    counts = Counter(train)
    ranked = sorted(counts, key=lambda word: (-counts[word], word))
    fit = fit_pdf_zipf([counts[word] for word in ranked], min_frequency_exclusive=0)
    head = set(ranked[:fit["cutoff_rank"]])
    accumulator = IGRAccumulator()
    for password, count in counts.items():
        accumulator.add(extractor.extract(password).to_dict(), is_head=password in head, frequency=count)
    return {
        "fit": {key: value for key, value in fit.items()},
        "head_digests": {password_digest(password) for password in head},
        "igr": accumulator.scores(),
        "extractor": extractor,
    }


def _eligible(password: str, model: dict, weighting: str) -> list[str]:
    return list(_suggestion_map(password, model, weighting))


def _suggestion_map(password: str, model: dict, weighting: str) -> dict[str, dict]:
    advice = suggest_for_password(
        password, head_digests=model["head_digests"], igr_report=model["igr"],
        extractor=model["extractor"], weighting=weighting,
    )
    return {item["feature"]: item for item in advice["suggestions"]}


def summarize_execution(trace: Sequence[dict]) -> dict[str, int]:
    counts = {status: 0 for status in (*EXECUTION_STATUSES, "not_adopted")}
    for row in trace:
        status = row["status"]
        if status not in counts:
            raise ValueError(f"未标记的执行状态：{status}")
        counts[status] += 1
    return counts


def _apply_plan(
    passwords: Sequence[str], user_ids: Sequence[str], features: Sequence[str],
    model: dict, weighting: str, trace: list | None = None, *,
    response_mode: str = "deterministic", adoption_rate: float = 1.0, adoption_seed: int = 0,
) -> list[str]:
    plans = {password: _suggestion_map(password, model, weighting) for password in set(passwords)}
    changed = []
    for password, user_id in zip(passwords, user_ids):
        current = password
        available = plans[password]
        held: list[tuple[str, str, str | None]] = []
        for feature in features:
            item = available.get(feature)
            if item is None:
                continue
            if not adopts(user_id, feature, seed=adoption_seed, adoption_rate=adoption_rate):
                if trace is not None:
                    trace.append({
                        "user_id": user_id, "feature": feature, "status": "not_adopted",
                        "password": current, "broken_features": [],
                    })
                continue
            edited = execute_suggestion(
                current, feature, item["action"], item["target_description"], model["extractor"], prior=held,
                user_id=user_id, response_mode=response_mode,
            )
            if trace is not None:
                trace.append({"user_id": user_id, "feature": feature, **edited})
            if edited["status"] == "success":
                current = edited["password"]
                held.append((feature, item["action"], item["target_description"]))
            elif edited["status"] == "already_satisfied":
                held.append((feature, item["action"], item["target_description"]))
        changed.append(current)
    return changed


def _clean_point(train: Sequence[str], evaluated: Sequence[str], candidates: Sequence[str], budget: int) -> dict:
    evaluation = evaluate_ranking(
        FrequencyAttacker().fit_select_rank(train, evaluated, candidates).guesses,
        evaluated, budgets=(budget,),
    )
    point = evaluation["points"][0]
    return {
        "rate": point["rate"],
        "cracked": point["cracked"],
        "total": evaluation["total"],
        "coverage": evaluation["coverage"],
    }


def _modification_rate(before: Sequence[str], after: Sequence[str]) -> float:
    changed = sum(left != right for left, right in zip(before, after))
    return changed / len(before) if before else 0.0


def select_budget_plan(scenario: dict, model: dict, candidates: Sequence[str], budget: int) -> dict:
    """Rank single-feature edits by adaptive validation risk and modification rate."""
    baseline = _clean_point(scenario["train"], scenario["validation"], candidates, budget)
    rows = []
    for feature in PAPER_POLICIES:
        train_after = _apply_plan(scenario["train"], scenario["train_id"], [feature], model, "frequency")
        validation_after = _apply_plan(scenario["validation"], scenario["validation_id"], [feature], model, "frequency")
        adaptive = _clean_point(train_after, validation_after, candidates, budget)
        modification = _modification_rate(scenario["validation"], validation_after)
        gain = baseline["rate"] - adaptive["rate"]
        rows.append({
            "feature": feature,
            "validation_adaptive_rate": adaptive["rate"],
            "absolute_point_gain": gain,
            "modification_rate": modification,
            "score": gain - COST_WEIGHT * modification,
        })
    ranked = sorted(rows, key=lambda row: (-row["score"], row["feature"]))
    chosen = [row["feature"] for row in ranked if row["score"] > 0][:2]
    return {"ranked": ranked, "features": chosen, "baseline_validation_rate": baseline["rate"]}


def _paper_features(model: dict) -> list[str]:
    rows = [
        row for row in model["igr"]["features"]
        if row["feature"] in PAPER_POLICIES and row["status_unique"] == "ok"
    ]
    rows.sort(key=lambda row: (row["rank_unique"], row["feature"]))
    return [row["feature"] for row in rows[:2]]


def _cracked_flags(ranking, samples: Sequence[str], budget: int) -> list[bool]:
    if ranking.open_positions is None:
        order = {value: index + 1 for index, value in enumerate(dict.fromkeys(ranking.guesses))}
    else:
        order = {}
        for value, position in zip(ranking.guesses, ranking.open_positions):
            order.setdefault(value, int(position))
    return [order.get(sample, float("inf")) <= budget for sample in samples]


def _final_attack(train: Sequence[str], test: Sequence[str], candidates: Sequence[str], budget: int) -> dict:
    frequency = FrequencyAttacker().fit_select_rank(train, test, candidates)
    ngram = CharacterNgramAttacker(orders=(2,), smoothing_values=(0.3,)).fit_select_rank(train, test, candidates)
    attacks = {}
    flags = {}
    for ranking in (frequency, ngram):
        evaluation = evaluate_ranking(ranking.guesses, test, budgets=(budget,), positions=ranking.open_positions)
        attacks[ranking.attacker_id] = {
            "rate": evaluation["points"][0]["rate"],
            "coverage": evaluation["coverage"],
        }
        flags[ranking.attacker_id] = _cracked_flags(ranking, test, budget)
    worst = max(attacks.values(), key=lambda row: row["rate"])
    return {
        "attacks": attacks, "worst_rate": worst["rate"], "worst_coverage": worst["coverage"],
        "flags": flags,
    }


def _apply_modern(passwords: Sequence[str], user_ids: Sequence[str], head_digests: set[str]) -> list[str]:
    """Train-head exact blocklist plus a minimum length of 8. No character-class rule."""
    rewritten = []
    for password, user_id in zip(passwords, user_ids):
        if password_digest(password) in head_digests or len(password) < 8:
            rewritten.append(blocked_length_edit(password, user_id))
        else:
            rewritten.append(password)
    return rewritten


def _paired_interval(before: Sequence[bool], after: Sequence[bool], seed: int) -> dict:
    """Bootstrap the mean of per-user (cracked_before - cracked_after)."""
    differences = [int(left) - int(right) for left, right in zip(before, after)]
    count = len(differences)
    if count == 0:
        return {"point": None, "ci95": [None, None], "n": 0, "method": "paired bootstrap"}
    point = sum(differences) / count
    generator = random.Random(seed)
    samples = []
    for _ in range(1000):
        total = 0
        for _user in range(count):
            total += differences[generator.randrange(count)]
        samples.append(total / count)
    samples.sort()
    return {
        "point": point,
        "ci95": [samples[24], samples[974]],
        "n": count,
        "method": "paired bootstrap of per-user cracked indicators; 1000 resamples",
    }


def _closed_audit(scenario: dict, candidates: Sequence[str], train_after: Sequence[str], validation_after: Sequence[str], test_after: Sequence[str]) -> tuple[dict, bool]:
    from experiments.evaluation_validity import splits_allow_closed_publication
    from experiments.robustness_protocol import edit_leak_counts
    audit = {
        "train": edit_leak_counts(scenario["train"], train_after, candidates),
        "validation": edit_leak_counts(scenario["validation"], validation_after, candidates),
        "test": edit_leak_counts(scenario["test"], test_after, candidates),
    }
    return audit, splits_allow_closed_publication(audit)


def compare_scenario(scenario: dict, *, budget: int = 40) -> dict:
    extractor = HTPGFeatureExtractor(["joy", "happy"], ["smith", "li"])
    model = _head_model(scenario["train"], extractor)
    candidates = registered_candidates(catalog())
    if any(password in set(candidates) for password in HELD_OUT):
        raise RuntimeError("未知结构口令不应进入预注册候选集")
    budget_plan = select_budget_plan(scenario, model, candidates, budget)
    paper_features = _paper_features(model)
    feature_arms = {"none": [], "paper_igr_unique": paper_features, "budget_cost": budget_plan["features"]}
    results = {}
    adaptive_flags = {}
    for name, features in feature_arms.items():
        weighting = "unique" if name == "paper_igr_unique" else "frequency"
        train_after = _apply_plan(scenario["train"], scenario["train_id"], features, model, weighting)
        validation_after = _apply_plan(scenario["validation"], scenario["validation_id"], features, model, weighting)
        test_after = _apply_plan(scenario["test"], scenario["test_id"], features, model, weighting)
        audit, published = _closed_audit(scenario, candidates, train_after, validation_after, test_after)
        frozen = _final_attack(scenario["train"], test_after, candidates, budget)
        adaptive = _final_attack(train_after, test_after, candidates, budget)
        adaptive_flags[name] = adaptive["flags"]
        results[name] = {
            "features": list(features),
            "test_modification_rate": _modification_rate(scenario["test"], test_after),
            "frozen_worst_rate": frozen["worst_rate"],
            "adaptive_worst_rate": adaptive["worst_rate"],
            "adaptive_coverage": adaptive["worst_coverage"],
            "adaptive_attacks": adaptive["attacks"],
            "candidate_audit": audit,
            "headline_published": published,
            "formal_publication_path": False,
        }
    modern_train = _apply_modern(scenario["train"], scenario["train_id"], model["head_digests"])
    modern_validation = _apply_modern(scenario["validation"], scenario["validation_id"], model["head_digests"])
    modern_test = _apply_modern(scenario["test"], scenario["test_id"], model["head_digests"])
    modern_audit, modern_published = _closed_audit(scenario, candidates, modern_train, modern_validation, modern_test)
    modern_frozen = _final_attack(scenario["train"], modern_test, candidates, budget)
    modern_adaptive = _final_attack(modern_train, modern_test, candidates, budget)
    adaptive_flags["modern_blocklist"] = modern_adaptive["flags"]
    results["modern_blocklist"] = {
        "features": ["train_head_blocklist", "min_length_8"],
        "test_modification_rate": _modification_rate(scenario["test"], modern_test),
        "frozen_worst_rate": modern_frozen["worst_rate"],
        "adaptive_worst_rate": modern_adaptive["worst_rate"],
        "adaptive_coverage": modern_adaptive["worst_coverage"],
        "adaptive_attacks": modern_adaptive["attacks"],
        "rule": "头部触发的长度响应：头部或短口令必须改变，已经达到 14 的头部口令也要加长。",
        "candidate_audit": modern_audit,
        "headline_published": modern_published,
        "formal_publication_path": False,
        "comparison_role": "head_triggered_length_response",
    }
    none_rate = results["none"]["adaptive_worst_rate"]
    for name, row in results.items():
        row["absolute_point_change_vs_none"] = none_rate - row["adaptive_worst_rate"]
        row["paired_vs_none"] = {
            attacker: _paired_interval(
                adaptive_flags["none"][attacker], adaptive_flags[name][attacker],
                seed=scenario["seed"] + len(name) + len(attacker),
            )
            for attacker in ("frequency", "character-ngram")
        }
        if not row.get("headline_published", True):
            from experiments.evaluation_validity import finalize_closed_publication
            row["withheld_point_change"] = row["absolute_point_change_vs_none"]
            row["withheld_reason"] = "旧比较入口检测到修改导致的候选漏收，封闭收益不发布。"
            finalize_closed_publication(row, open_attackers=set())
    saturated = results["none"]["adaptive_worst_rate"] >= 0.99 and results["none"]["adaptive_coverage"] >= 0.99
    public_fit = {key: value for key, value in model["fit"].items()}
    return {
        "compare_version": COMPARE_VERSION,
        "budget": budget,
        "cost_weight": COST_WEIGHT,
        "catalog_size": scenario["catalog_size"],
        "candidate_count": len(candidates),
        "unknown_test_count": scenario["unknown_test_count"],
        "cutoff_rank": public_fit["cutoff_rank"],
        "alpha_rounded_3dp": public_fit["alpha_rounded_3dp"],
        "paper_features": paper_features,
        "budget_features": budget_plan["features"],
        "validation_feature_scores": budget_plan["ranked"],
        "arms": results,
        "test_used_for_selection": False,
        "selection_attacker": "frequency adaptive on validation",
        "test_attackers": ["frequency", "character-ngram"],
        "main_budget_saturated": saturated,
        "plaintext_retained": False,
        "formal_publication_path": False,
        "publication_note": "这是旧比较入口。正式封闭发布走 robustness_protocol，并且在修改漏收时不发布收益。",
    }


def run_two_scenarios(*, seed: int = 7, size: int = 900, budget: int = 40) -> dict:
    closed = compare_scenario(sample_scenario(size=size, seed=seed, unknown_test_fraction=0.0), budget=budget)
    opened = compare_scenario(
        sample_scenario(size=size, seed=seed, unknown_test_fraction=0.2), budget=budget,
    )
    return {"closed_catalog": closed, "open_unknown": opened, "plaintext_retained": False}


def _arm_summary(rows: Sequence[dict], arm: str) -> dict:
    from experiments.evaluation_validity import seed_summary
    summary = seed_summary(
        [row["arms"][arm]["absolute_point_change_vs_none"] for row in rows],
        [bool(row["arms"][arm].get("headline_published", True)) for row in rows],
    )
    lower_bounds = [
        row["arms"][arm]["paired_vs_none"]["frequency"]["ci95"][0]
        for row in rows
        if row["arms"][arm].get("headline_published", True)
    ]
    return {
        "seeds": summary["planned_seeds"],
        "valid_seeds": summary["valid_seeds"],
        "comparison_published": summary["comparison_published"],
        "mean_absolute_point_change": summary["mean"],
        "min_absolute_point_change": summary["min"],
        "max_absolute_point_change": summary["max"],
        "diagnostic_valid_subset_mean": summary["diagnostic_valid_subset_mean"],
        "unpublished_reason": summary["unpublished_reason"],
        "seeds_whose_frequency_paired_lower_bound_is_positive": sum(
            bound is not None and bound > 0 for bound in lower_bounds
        ) if summary["comparison_published"] else None,
    }


def summarize_seeds(
    seeds: Sequence[int] = (1, 2, 3, 4, 5), *, size: int = 900, budget: int = 40,
) -> dict:
    """Repeat the frozen protocol. The test split of each seed is used once inside that seed."""
    closed_rows = []
    open_rows = []
    for seed in seeds:
        both = run_two_scenarios(seed=int(seed), size=size, budget=budget)
        closed_rows.append(both["closed_catalog"])
        open_rows.append(both["open_unknown"])
    arms = ("paper_igr_unique", "budget_cost", "modern_blocklist")
    return {
        "seeds": [int(seed) for seed in seeds],
        "size": size,
        "budget": budget,
        "closed_catalog": {arm: _arm_summary(closed_rows, arm) for arm in arms},
        "open_unknown": {arm: _arm_summary(open_rows, arm) for arm in arms},
        "plaintext_retained": False,
        "interval_note": "每个种子内部是同一批测试用户的配对区间；跨种子只报告极差，不把五个种子合成一个用户。",
    }
