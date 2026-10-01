"""Paired full-catalog order/response diagnostics; no new policy pruning."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.registration import load_registration
from policy.user_response import phrase_vocabulary, weighted_pool
from tools.run_candidate_pool_pilot import settings
from tools.screen_policy_catalog import screen_profile

CONTEXTS = ("shuffle43", "shuffle44", "response43", "response44",
            "popular_first", "rare_first")


def context_users(words, name):
    indices = list(range(len(words)))
    if name.startswith("shuffle"):
        random.Random(int(name.removeprefix("shuffle"))).shuffle(indices)
    elif name in ("popular_first", "rare_first"):
        frequency = Counter(words)
        direction = -1 if name == "popular_first" else 1
        indices.sort(key=lambda i: (direction * frequency[words[i]], words[i], i))
    elif name not in ("response43", "response44"):
        raise ValueError(name)
    # Ordering does not change a user's simulated response random stream.
    return [words[i] for i in indices], [f"catalog-validation-{i}" for i in indices]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    args = parser.parse_args()
    if not 0 <= args.shard < args.shards:
        parser.error("Invalid shard")
    base = json.loads((args.input_dir / "manifest_00.json").read_text(encoding="utf-8"))
    cfg = settings()
    cfg_hash = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()
    if cfg_hash != base["config_sha256"]:
        raise ValueError("Base response configuration changed")
    d = cfg["data"]
    source = Path(d["path"])
    if not source.is_absolute():
        source = ROOT / source
    data = load_registration(source, source_format=d["format"], encoding=d["encoding"],
        users=d["users"], development=d["development"], seed=cfg["seed"],
        cohort_size=d["cohort_size"], progress=print)
    if data["metadata"] != base["dataset"]:
        raise ValueError("Development data changed")
    dev = data["development"]
    words = [word for word, count in sorted(dev["validation"].items()) for _ in range(count)]
    random.Random(cfg["seed"] ^ 0xCA7106).shuffle(words)
    train = dev["train"]
    ranked = tuple(sorted(train, key=lambda w: (-train[w], w)))
    pool, vocabulary = weighted_pool(train), phrase_vocabulary(train)
    catalog_bytes = (ROOT / "docs/policy_candidate_profiles.json").read_bytes()
    if hashlib.sha256(catalog_bytes).hexdigest() != base["catalog_sha256"]:
        raise ValueError("Catalog changed")
    profiles = json.loads(catalog_bytes)["profiles"]
    destination = args.input_dir / "stability"
    destination.mkdir(exist_ok=True)
    for name in CONTEXTS:
        selected, ids = context_users(words, name)
        cfg_context = copy.deepcopy(cfg)
        if name.startswith("response"):
            cfg_context["seed"] = int(name.removeprefix("response"))
        manifest = {"protocol": "paired-catalog-registration-stability-v1", "context": name,
                    "base_config_sha256": cfg_hash, "base_catalog_sha256": base["catalog_sha256"],
                    "validation_hash": base["dataset"]["development_hashes"]["validation"],
                    "response_seed": cfg_context["seed"], "shards": args.shards,
                    "order_hash": hashlib.sha256(json.dumps(ids).encode()).hexdigest(),
                    "users": len(words), "stable_identifiers": True,
                    "attack_evaluated": False, "context_uses_validation_frequency": name in ("popular_first", "rare_first"),
                    "context_note": "Frequency-sorted order is an offline stress test, not online knowledge of future users."}
        manifest_path = destination / f"{name}_manifest_{args.shard:02}.json"
        if manifest_path.exists():
            if json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
                raise ValueError("Stability provenance changed")
        else:
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        path = destination / f"{name}_shard_{args.shard:02}.jsonl"
        previous = {}
        if path.exists():
            content = path.read_bytes()
            if content and not content.endswith(b"\n"):
                raise ValueError("Partial own checkpoint needs inspection before resuming")
            for line in content.splitlines():
                record = json.loads(line)
                if record["profile_id"] in previous:
                    raise ValueError("Duplicate checkpoint")
                previous[record["profile_id"]] = record
        started = time.monotonic()
        done = 0
        with path.open("a", encoding="utf-8") as stream:
            for index, profile in enumerate(profiles):
                if index % args.shards != args.shard or profile["id"] in previous:
                    continue
                result = screen_profile(profile, selected, ranked, cfg_context,
                                        pool, vocabulary, identifiers=ids)
                result["context"] = name
                stream.write(json.dumps(result, ensure_ascii=False) + "\n")
                stream.flush()
                done += 1
                if done % 60 == 0:
                    print(f"{name} shard {args.shard}: {done} new, {time.monotonic()-started:.1f}s", flush=True)
    print("Registration stability contexts completed; attack stability still pending", flush=True)


if __name__ == "__main__":
    main()
