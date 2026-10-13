"""Dynamic extensions of an immutable Segreti baseline; actual dual attacks."""
from collections import Counter
from dataclasses import replace
from concurrent.futures import ProcessPoolExecutor, as_completed

from core.corpus import counts_hash
from core.uniformity import forecast_collision
from policy.registration_joint import structure
from experiments.registration_variable_scope import score_training_group, promote_group
from experiments.registration_variable_pipeline import risk, cost_failures
from experiments.registration_joint_pipeline import (
    preview_records, reweighted_train, attach_base_cost, write_json,
)

VERSION = "registration-segreti-anchored-dynamic-positive-v2"


def anchored_candidates(baseline, hold, recent, additions):
    """Keep both anchors; additional counts never truncate the baseline."""
    if not baseline.shapes <= hold.shapes:
        raise ValueError("Current policy lost part of the Segreti baseline")
    output, seen = [], {}

    def add(rule, role):
        signature = rule.signature()
        if signature in seen:
            output[seen[signature]]["roles"].append(role)
        else:
            seen[signature] = len(output)
            output.append({"rule": rule, "roles": [role]})

    add(hold, "hold")
    add(baseline, "baseline")
    counts = Counter()
    for word, n in recent.items():
        if baseline.accepts(word):
            counts[structure(word)] += n
    for anchor, name in ((baseline, "baseline"), (hold, "hold")):
        ranked = sorted((s for s, n in counts.items()
                         if n >= 20 and s not in anchor.shapes),
                        key=lambda s: (-counts[s], s))
        for count in additions:
            shapes = anchor.shapes | frozenset(ranked[:count])
            add(replace(baseline, shapes=shapes, j=len(shapes)),
                f"{name}_plus_{count}")
    assert all(baseline.shapes <= item["rule"].shapes for item in output)
    return output


def add_risk_failures(rows, baseline_row, cfg, confirmation=False):
    """The exact baseline always passes its own relative risk checks."""
    prefix = "confirmation_" if confirmation else ""
    for row in rows:
        if row["frozen_rate"] - baseline_row["frozen_rate"] > cfg["frozen_increase"]:
            row["failures"].append(prefix + "frozen_risk_increase")
        if row["predicted_outside"] - baseline_row["predicted_outside"] > cfg["outside_increase"]:
            row["failures"].append(prefix + "outside_support_increase")


def score_key(row):
    return (row["predicted_A1"], row["collision_forecast"],
            row["cost"]["mean_extra_submissions"], row["rule"]["shape_size"],
            "hold" not in row["roles"])


def sufficient_gain(gain, cfg):
    # Only suppress floating-point roundoff, far below one-account resolution.
    return gain > cfg.get("gain_tolerance", 1e-12) and gain >= cfg["min_gain"]


def select_winner(rows, cfg):
    """An extension must improve over BOTH available feasible anchors."""
    feasible = [r for r in rows if not r["failures"]]
    anchors = [r for r in feasible if set(r["roles"]) & {"hold", "baseline"}]
    if not anchors:
        raise RuntimeError("No completed anchor evaluation")
    reference = min(anchors, key=score_key)
    best = min(feasible, key=score_key)
    gain = reference["predicted_A1"] - best["predicted_A1"]
    if not set(best["roles"]) & {"hold", "baseline"} and sufficient_gain(gain, cfg):
        return best, gain
    return None, gain


def confirm_choice(rows, winner_number, cfg):
    """Select an anchor unless a single nominated extension confirms."""
    baseline = next(r for r in rows if "baseline" in r["roles"])
    add_risk_failures(rows, baseline, cfg, confirmation=True)
    anchors = [r for r in rows if not r["failures"]
               and set(r["roles"]) & {"hold", "baseline"}]
    if not anchors:
        raise RuntimeError("Baseline confirmation incomplete")
    fallback = min(anchors, key=score_key)
    if winner_number is not None:
        winner = next(r for r in rows if r["number"] == winner_number)
        if not sufficient_gain(fallback["predicted_A1"] - winner["predicted_A1"], cfg):
            winner["failures"].append("confirmation_insufficient_gain")
        if not winner["failures"]:
            return winner["number"], "confirmed_extension"
    return fallback["number"], (
        "hold_anchor" if "hold" in fallback["roles"] else "restore_segreti_baseline")


def select_rule(baseline, hold, history, recent, recent_initial, dev_history,
                next_train, data, response, attacks, frozen, cfg, seed, t,
                cohort_size, audit_path, log):
    proposals = anchored_candidates(baseline, hold, recent, cfg["additional_structures"])
    training = reweighted_train(next_train, data["roles"]["train"],
                                recent_initial, seed, t, cfg["recent_weight"])
    previews = {phase: preview_records(data["roles"][f"preview_{phase}"],
                                       recent_initial, seed, t, phase,
                                       cfg["preview_users"], cfg["recent_weight"])
                for phase in ("select", "confirm")}
    base = replace(baseline, shapes=frozenset(), j=0)
    rows, private, groups = [], [], {}
    common = response.simulate(previews["select"], base, seed)
    for number, item in enumerate(proposals):
        rule = item["rule"]
        preview = response.simulate(previews["select"], rule, seed)
        transformed = response.simulate(training, rule, seed)
        if transformed["summary"]["pending_users"]:
            raise RuntimeError("Incomplete development response")
        attach_base_cost(preview, common)
        counts = dev_history + transformed["counts"]
        key = counts_hash(counts)
        row = {"number": number, "roles": item["roles"], "rule": rule.summary(),
               "cost": preview["summary"], "failures": cost_failures(preview["summary"], cfg),
               "collision_forecast": forecast_collision(history, preview["counts"], cohort_size)}
        rows.append(row)
        private.append({"rule": rule, "counts": counts})
        group = groups.setdefault(key, {"counts": counts, "items": []})
        group["items"].append({"number": number, "preview_counts": preview["counts"]})
    tasks = [{"key": key, **group, "history": history, "cohort_size": cohort_size,
              "cfg": cfg, "root": str(attacks.root), "frozen_ranks": frozen["ranks"]}
             for key, group in groups.items()]
    log(f"Batch {t+1}: {len(rows)} policies / {len(tasks)} actual dual fits; baseline retained")
    with ProcessPoolExecutor(max_workers=cfg["candidate_workers"]) as executor:
        futures = [executor.submit(score_training_group, task) for task in tasks]
        for future in as_completed(futures):
            scored = future.result()
            promote_group(scored["root"], attacks.root, scored["key"], cfg["objective_budget"])
            for item in scored["items"]:
                rows[item["number"]].update(item["score"])
            log(f"Batch {t+1}: completed dual fit for {len(scored['items'])} policies")
    baseline_row = next(r for r in rows if "baseline" in r["roles"])
    assert not baseline_row["failures"]
    add_risk_failures(rows, baseline_row, cfg)
    winner, gain = select_winner(rows, cfg)
    winner_number = winner["number"] if winner is not None else None
    confirm_numbers = [r["number"] for r in rows
                       if set(r["roles"]) & {"hold", "baseline"}]
    if winner_number is not None:
        confirm_numbers.append(winner_number)
    confirmed = []
    common = response.simulate(previews["confirm"], base, seed)
    for number in confirm_numbers:
        item, source = private[number], rows[number]
        preview = response.simulate(previews["confirm"], item["rule"], seed)
        attach_base_cost(preview, common)
        index = attacks.index(item["counts"], purpose="confirmation")
        h = risk(index, history, [cfg["objective_budget"]])
        p = risk(index, preview["counts"], [cfg["objective_budget"]], cfg["preview_users"])
        n, total = sum(history.values()), sum(history.values()) + cohort_size
        confirmed.append({
            "number": number, "roles": source["roles"], "rule": source["rule"],
            "cost": preview["summary"], "failures": cost_failures(preview["summary"], cfg),
            "predicted_A1": (n*h["rates"][str(cfg["objective_budget"])]
                             + cohort_size*p["rates"][str(cfg["objective_budget"])])/total,
            "predicted_outside": (n*h["outside_model_support_fraction"]
                                 + cohort_size*p["outside_model_support_fraction"])/total,
            "frozen_rate": sum(v for w, v in preview["counts"].items()
                               if w in frozen["ranks"])/cfg["preview_users"],
            "collision_forecast": forecast_collision(history, preview["counts"], cohort_size),
            "history_actual_dual": h, "preview_actual_dual": p,
            "training_sha256": counts_hash(item["counts"]),
        })
    chosen_number, status = confirm_choice(confirmed, winner_number, cfg)
    chosen = private[chosen_number]["rule"]
    write_json(audit_path, {
        "status": status, "candidates": rows, "selection_gain": gain,
        "nominee": winner_number, "confirmation": confirmed, "chosen": chosen.summary(),
        "baseline_signature": baseline.summary(), "baseline_present": True,
        "single_extension_nominee": True, "separate_preview_roles": True,
    })
    log(f"Batch {t+1}: {status}; structures={len(chosen.shapes)}; selection gain={100*gain:.3f} pp")
    return chosen, {"batch": t+1, "status": status,
                    "updated": chosen.signature() != hold.signature(),
                    "selection_gain": gain, "candidates": len(rows),
                    "baseline_present": True}
