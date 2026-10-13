"""Run ONLY the Segreti-initialized 100k-user dynamic registration experiment."""
from pathlib import Path
from collections import Counter
import argparse
import hashlib
import json
import shutil
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.corpus import counts_hash
from policy.registration_joint import JointRule
from experiments.registration_joint_data import load_private, save_private, developer_stream
from experiments.registration_joint_pipeline import attach_base_cost, summed_cost, write_json
from experiments.registration_variable_scope import UnlimitedPreferences, ReusingDualAttackCache
from experiments.registration_variable_pipeline import frozen_union, risk
from experiments.registration_segreti_dynamic import select_rule, VERSION
from tools.run_registration_variable_scope import attack_results


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(root, cfg):
    seed = cfg["seeds"][0]
    arm = "segreti_dynamic"
    old = Path(cfg["comparison_source"])
    baseline_path = old/"runs/11/segreti_struct_m_nohint/blacklist.pkl.gz"
    assert digest(baseline_path) == cfg["baseline_artifact_sha256"]
    assert digest(old/"private/partition.pkl.gz") == cfg["paired_partition_sha256"]
    saved = load_private(baseline_path)
    r = saved["rule"]
    baseline = JointRule(r.minimum, r.common, r.exact, r.shapes, r.k, len(r.shapes))
    assert len(baseline.shapes) == 213 and not baseline.exact and baseline.minimum == 8
    base = JointRule(baseline.minimum, baseline.common)
    (root/"private").mkdir(parents=True, exist_ok=True)
    for src, dst in [(old/"private/partition.pkl.gz", root/"private/partition.pkl.gz"),
                     (baseline_path, root/"private/segreti_baseline.pkl.gz")]:
        if not dst.exists():
            shutil.copy2(src, dst)
        assert digest(src) == digest(dst)
    data = load_private(root/"private/partition.pkl.gz")
    words = [w for _, w in data["roles"]["train"]]
    response = UnlimitedPreferences(words, cfg["minimum"], cfg["weights"], cfg["response_mode"], None)
    size = cfg["users"]//cfg["batches"]
    records = data["roles"][f"test_{seed}"]
    cohorts = [records[t*size:(t+1)*size] for t in range(cfg["batches"])]
    developer = developer_stream(data["roles"]["train"], "raw", seed, cfg["batches"], data["median_length"])
    path = root/"runs"/str(seed)/arm
    path.mkdir(parents=True, exist_ok=True)
    progress = root/"status.json"

    def event(message):
        line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + message
        print(line, flush=True)
        with (path/"events.log").open("a", encoding="utf-8") as handle:
            handle.write(line+"\n")
        write_json(progress, {"stage": "running", "users": cfg["users"], "last_event": message,
                              "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})

    cache = ReusingDualAttackCache(root/"private/attacks"/str(seed)/arm, cfg, event)
    frozen = frozen_union(cache.index(Counter(words), purpose="frozen"))
    checkpoint = path/"state.pkl.gz"
    if checkpoint.exists():
        state = load_private(checkpoint)
    else:
        state = {"t": 0, "rule": baseline, "history": Counter(), "dev_history": Counter(),
                 "final_batches": [], "dev_batches": [], "costs": [], "timeline": [],
                 "attempts": [], "decisions": []}
    baseline_state = load_private(old/"runs/11/segreti_struct_m_nohint/state.pkl.gz")
    for t in range(state["t"], cfg["batches"]):
        rule = state["rule"]
        if t:
            recent = sum(state["final_batches"][-cfg["window"]:], Counter())
            recent_initial = cohorts[max(0, t-cfg["window"]):t]
            train = data["roles"]["train"]
            chunk = train[t*(len(train)//cfg["batches"]):(t+1)*(len(train)//cfg["batches"])]
            rule, decision = select_rule(
                baseline, rule, state["history"], recent, recent_initial,
                state["dev_history"], chunk, data, response, cache, frozen, cfg,
                seed, t, len(cohorts[t]), path/f"candidate_audit_{t+1:02}.json", event)
            state["decisions"].append(decision)
        assert baseline.shapes <= rule.shapes
        actual = response.simulate(cohorts[t], rule, seed, distances=True)
        common = response.simulate(cohorts[t], base, seed)
        attach_base_cost(actual, common)
        dev = response.simulate(developer[t], rule, seed)
        assert actual["summary"]["pending_users"] == dev["summary"]["pending_users"] == 0
        if t == 0:
            assert actual["counts"] == baseline_state["batches"][0]
            assert dev["counts"] == baseline_state["dev_batches"][0]
            assert actual["attempts"] == baseline_state["attempts"][:size]
            event("First batch matches the original Segreti target/developer counts and retry sequence exactly")
        state["history"].update(actual["counts"])
        state["dev_history"].update(dev["counts"])
        state["final_batches"].append(actual["counts"])
        state["dev_batches"].append(dev["counts"])
        state["costs"].append(actual["summary"])
        state["attempts"].extend(actual["attempts"])
        state["timeline"].append(rule.summary())
        state["rule"], state["t"] = rule, t+1
        save_private(checkpoint, state)
        event(f"Completed batch {t+1}/{cfg['batches']}: {sum(state['history'].values()):,} users; {len(rule.shapes)} structures")
    training = state["dev_history"]
    save_private(path/"developer_counts.pkl.gz", training)
    event("All 100,000 users registered; generating final PCFG and OMEN actual million-attempt prefixes")
    index = cache.index(training, purpose="final")
    cost = summed_cost(state["costs"])
    result = {
        "version": VERSION, "arm": arm, "seed": seed, "status": "complete",
        "cost": cost, "timeline": state["timeline"], "decisions": state["decisions"],
        "A1_training_sha256": counts_hash(training), "A1_training_occurrences": sum(training.values()),
        "eligibility_failures": [], "attack_failures": {},
        "mechanism": {"baseline": saved["metadata"], "baseline_from_first_batch": True,
                      "original_baseline_candidate_every_batch": True,
                      "all_execution_rules_contain_baseline_structures": True,
                      "additional_structures": cfg["additional_structures"],
                      "objective": "actual dual-model cumulative union, B attempts EACH",
                      "extension_requires_improvement_over_both_anchors": True,
                      "user_modification_caps": None},
        "actual_attacks": attack_results(index.prefixes, state["history"], cfg),
        "final_dual_risk": risk(index, state["history"], cfg["budgets"], cfg["users"]),
    }
    assert cost["completed_users"] == cfg["users"] and cost["pending_users"] == 0
    assert sum(training.values()) == 60000
    write_json(path/"result.json", result)
    event("Final result saved; audit pending")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    import msvcrt
    lock = (root/"runner.lock").open("a+b")
    lock.seek(0); lock.write(b"0"); lock.flush(); lock.seek(0)
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    cfg = json.loads(args.config.read_text("utf-8"))
    assert cfg["arms"] == ["segreti_dynamic"] and cfg["users"] == 100000
    assert cfg["objective_budget"] == max(cfg["budgets"]) == 1000000
    assert cfg["user_cost_caps_disabled"] and all(cfg[k] is None for k in (
        "max_modification", "max_extra", "max_more_than_three", "patience", "candidate_limit"))
    old_manifest = json.loads((Path(cfg["comparison_source"])/"manifest.json").read_text("utf-8"))
    for relative, expected in old_manifest["source_hashes"].items():
        assert digest(ROOT/relative) == expected, f"Original frozen source changed: {relative}"
    files = list(old_manifest["source_hashes"])
    files += [
        "experiments/registration_segreti_dynamic.py", "tools/run_registration_segreti_dynamic.py",
        "experiments/registration_variable_scope.py", "experiments/registration_variable_pipeline.py",
        "tools/run_registration_variable_scope.py",
    ]
    manifest = {
        "version": VERSION, "config": cfg,
        "source_hashes": {f: digest(ROOT/f) for f in sorted(set(files))},
        "analysis_type": "single paired exploratory strategy revision; not independent validation",
        "human_verification": "pending", "only_new_arm": "segreti_dynamic",
        "baseline_artifact_sha256": cfg["baseline_artifact_sha256"],
        "paired_partition_sha256": cfg["paired_partition_sha256"],
        "policy": "Start from exact Segreti rule; preserve original rule in every selection; add whole structures",
    }
    file = root/"manifest.json"
    if file.exists():
        assert json.loads(file.read_text("utf-8")) == manifest, "Frozen code/config changed"
    else:
        write_json(file, manifest)
    try:
        run(root, cfg)
        write_json(root/"status.json", {"stage": "complete", "users": cfg["users"], "audit": "pending"})
    except Exception as exc:
        write_json(root/"status.json", {"stage": "failed", "error": str(exc), "traceback": traceback.format_exc()})
        raise


if __name__ == "__main__":
    main()
