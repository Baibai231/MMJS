"""Reproducible empirical catalog coverage; never certifies dynamic pruning.

Works from checkpoints only. Partial attack files are reported, never ranked
as though the missing policies had zero risk. No registration is rerun.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOLERANCES = {"collision_probability": 1e-7, "modification_rate": .01,
              "attack_rate": .005}
SIZES = (15, 30, 60, 120, 240, 480, 960)
CONTEXTS = ("shuffle43", "shuffle44", "response43", "response44", "popular_first", "rare_first")
METRIC_NAMES = {"collision_probability": "碰撞概率", "modification_rate": "修改率", "attack_rate": "百万预算A1命中率"}
CONTEXT_NAMES = {"shuffle43": "打乱顺序43", "shuffle44": "打乱顺序44",
                 "response43": "响应种子43", "response44": "响应种子44",
                 "popular_first": "热门先注册", "rare_first": "热门后注册"}


def policy_label(profile):
    parts = [f"长度≥{profile['min_length']}"]
    if profile["required_classes"]:
        parts.append(f"类别≥{profile['required_classes']}")
    if profile["development_top_blocklist"]:
        parts.append(f"开发Top{profile['development_top_blocklist']}")
    if profile["historical_hotspot_blocklist"] != "off":
        parts.append("历史热点")
    labels = {"repeated": "禁连续重复", "sequential_digits": "禁连续数字", "keyboard_walk": "禁键盘模式"}
    parts.extend(labels[name] for name in profile["deny_features"])
    return "＋".join(parts)


def read_rows(directory, pattern):
    rows = {}
    fingerprints = {}
    for path in sorted(directory.glob(pattern)):
        data = path.read_bytes()
        # A writer may currently be appending a row. Only commit complete lines.
        data = data[:data.rfind(b"\n") + 1]
        fingerprints[path.name] = hashlib.sha256(data).hexdigest()
        for line in data.splitlines():
            row = json.loads(line)
            key = row["profile_id"]
            if key in rows:
                raise ValueError(f"Duplicate checkpoint: {key}")
            rows[key] = row
    return rows, fingerprints


def dominates(a, b, metrics):
    return (all(a[m] <= b[m] for m in metrics)
            and any(a[m] < b[m] for m in metrics))


def frontier(rows, metrics):
    return [a for a in rows if not any(dominates(b, a, metrics) for b in rows)]


def mechanism_tokens(profile):
    # Parameter-level coverage is a diagnostic, not a proof of interactions.
    result = {f"{k}={profile[k]}" for k in (
        "min_length", "required_classes", "development_top_blocklist",
        "historical_hotspot_blocklist")}
    for name in ("repeated", "sequential_digits", "keyboard_walk"):
        result.add(f"{name}={name in profile['deny_features']}")
    return result


def gap(candidate, target, metrics):
    """One representative must cover ALL objectives of a target simultaneously."""
    return max(0., *( (candidate[m] - target[m]) / TOLERANCES[m]
                    for m in metrics))


def coverage_order(rows, metrics):
    """Nested deterministic greedy covering, not an optimal subset solver.

    First include objective extremes, then cover all parameter levels, then
    the worst represented tradeoff. Keep all catalog rows as coverage targets,
    including dominated rows. Ties are resolved by stable profile ID.
    """
    rows = sorted(rows, key=lambda r: r["profile_id"])
    selected = []
    reasons = {}
    tokens = {r["profile_id"]: mechanism_tokens(r["profile"]) for r in rows}
    covered = set()
    residual = {r["profile_id"]: math.inf for r in rows}

    def add(row, reason):
        key = row["profile_id"]
        if key in reasons:
            return
        selected.append(row)
        reasons[key] = reason
        covered.update(tokens[key])
        for target in rows:
            k = target["profile_id"]
            residual[k] = min(residual[k], gap(row, target, metrics))

    for metric in metrics:
        add(min(rows, key=lambda r: (r[metric], *(r[m] for m in metrics), r["profile_id"])),
            f"objective_extreme:{metric}")
    while len(selected) < len(rows):
        remaining = [r for r in rows if r["profile_id"] not in reasons]
        extra = max(len(tokens[r["profile_id"]] - covered) for r in remaining)
        if extra:
            row = min(remaining, key=lambda r: (
                -len(tokens[r["profile_id"]] - covered),
                -residual[r["profile_id"]], r["profile_id"]))
            add(row, "parameter_level_coverage")
        else:
            row = min(remaining, key=lambda r: (-residual[r["profile_id"]], r["profile_id"]))
            add(row, "worst_joint_objective_gap" if residual[row["profile_id"]] else
                "zero_measured_gap_catalog_order_padding")
    return selected, reasons


def describe_set(selected, rows, metrics):
    universe = set().union(*(mechanism_tokens(r["profile"]) for r in rows))
    represented = set().union(*(mechanism_tokens(r["profile"]) for r in selected))
    nearest = []
    for target in rows:
        candidate = min(selected, key=lambda r: (gap(r, target, metrics), r["profile_id"]))
        nearest.append({"profile_id": target["profile_id"],
                        "representative_id": candidate["profile_id"],
                        "gap_units": gap(candidate, target, metrics)})
    worst = max(nearest, key=lambda r: (r["gap_units"], r["profile_id"]))
    return {"size": len(selected), "ids": [r["profile_id"] for r in selected],
            "max_gap_units": worst["gap_units"], "worst_case": worst,
            "within_tolerance": sum(r["gap_units"] <= 1 for r in nearest),
            "universe_size": len(rows), "missing_parameter_levels": sorted(universe - represented),
            "paths_for_ten_cohorts": str(len(selected) ** 10), "assignments": nearest}


def old_selection(rows):
    # Match the original published fifteen templates, without importing runners.
    seeds = ((8,0,0,0,0),(10,0,0,0,0),(12,0,0,0,0),(16,0,0,0,0),
             (8,2,0,0,0),(8,3,0,0,0),(8,0,100,0,0),(8,0,1000,0,0),
             (8,0,10000,0,0),(8,0,0,1,0),(8,0,0,0,1),(8,0,0,0,2),
             (12,2,0,0,0),(12,0,1000,0,0),(12,0,1000,1,0))
    result = []
    for length, classes, block, history, bits in seeds:
        deny = {name for bit, name in ((1,"repeated"),(2,"sequential_digits")) if bits & bit}
        matches = [r for r in rows if (
            r["profile"]["min_length"], r["profile"]["required_classes"],
            r["profile"]["development_top_blocklist"],
            r["profile"]["historical_hotspot_blocklist"] != "off",
            set(r["profile"]["deny_features"])) == (length, classes, block, bool(history), deny)]
        if len(matches) != 1:
            raise ValueError("Original fifteen cannot be matched uniquely")
        result.extend(matches)
    return result


def stability_results(directory, baseline, comparisons, manifest):
    metrics = ["collision_probability", "modification_rate"]
    base = {r["profile_id"]: r for r in baseline}
    results = []
    hashes = {}
    witnesses = {r["profile_id"]: next((b["profile_id"] for b in baseline
                 if dominates(b, r, metrics)), None) for r in baseline}
    for name in CONTEXTS:
        rows, fingerprints = read_rows(directory / "stability", f"{name}_shard_*.jsonl")
        hashes.update({"stability/" + key: value for key, value in fingerprints.items()})
        if not set(rows) <= set(base):
            raise ValueError("Unknown stability policy")
        complete = len(rows) == len(base)
        entry = {"context": name, "count": len(rows), "complete": complete,
                 "attack_evaluated": False}
        if complete:
            manifests = [json.loads(p.read_text(encoding="utf-8")) for p in
                         sorted((directory / "stability").glob(f"{name}_manifest_*.json"))]
            if not manifests or any(m != manifests[0] for m in manifests):
                raise ValueError("Inconsistent stability manifests")
            m = manifests[0]
            if (m["base_config_sha256"] != manifest["config_sha256"] or
                m["base_catalog_sha256"] != manifest["catalog_sha256"] or
                m["validation_hash"] != manifest["dataset"]["development_hashes"]["validation"] or
                not m["stable_identifiers"]):
                raise ValueError("Stability data incomparable")
            for key, row in rows.items():
                if (row["context"] != name or row["pending_users"] or row["status"] != "complete"
                        or row["registered_users"] != manifest["validation_users"]):
                    raise ValueError("Incomplete stability registration")
            if not name.startswith("response"):
                for key, row in rows.items():
                    if row["profile"]["historical_hotspot_blocklist"] == "off":
                        if any(row[metric] != base[key][metric] for metric in metrics):
                            raise ValueError("Order-only experiment changed a memoryless policy")
            entry["baseline_dominance_no_longer_strict"] = sum(
                witness is not None and not dominates(rows[witness], rows[key], metrics)
                for key, witness in witnesses.items())
            entry["changed_profiles"] = sum(any(row[m] != base[key][m] for m in metrics)
                                            for key, row in rows.items())
            entry["comparisons"] = []
            for comparison in comparisons:
                measured = describe_set([rows[key] for key in comparison["ids"]], list(rows.values()), metrics)
                entry["comparisons"].append({key: measured[key] for key in
                    ("size", "max_gap_units", "within_tolerance", "worst_case")})
        results.append(entry)
    return results, hashes


def analyze(directory):
    registered, hashes = read_rows(directory, "registration_shard_*.jsonl")
    attacks, attack_hashes = read_rows(directory, "attack_shard_*.jsonl")
    catalog = json.loads((ROOT / "docs/policy_candidate_profiles.json").read_text(encoding="utf-8"))["profiles"]
    if set(registered) != {p["id"] for p in catalog}:
        raise ValueError("Complete catalog registration is required")
    if not set(attacks) <= set(registered):
        raise ValueError("Unknown attack policy")
    manifests = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("manifest_*.json"))]
    if not manifests or any(m != manifests[0] for m in manifests):
        raise ValueError("Missing or inconsistent provenance")
    manifest = manifests[0]
    catalog_hash = hashlib.sha256((ROOT / "docs/policy_candidate_profiles.json").read_bytes()).hexdigest()
    if manifest["catalog_sha256"] != catalog_hash:
        raise ValueError("Catalog changed since measurement")
    rows = sorted(registered.values(), key=lambda r: r["profile_id"])
    for row in rows:
        if (row["status"] != "complete" or row["pending_users"] or
            row["registered_users"] != manifest["validation_users"] or
            row["validation_users"] != manifest["validation_users"]):
            raise ValueError("Registration incomplete or incomparable")
    complete = {}
    for key, attack in attacks.items():
        points = attack.get("A1_minauto", [])
        models = attack.get("models", [])
        if (attack.get("status") == "complete" and
            {p["budget"] for p in points} == {100,1000,10000,100000,1000000} and
            all(p["complete"] and p["all_models_completed"] and
                p["target_weight"] == manifest["validation_users"] for p in points) and
            {m["name"] for m in models} == {"frequency","dictionary-rules","character-ngram"} and
            all(m["stop_reason"] in ("reached_budget","exhausted") for m in models)):
            complete[key] = next(p["rate"] for p in points if p["budget"] == 1000000)
    metrics = ["collision_probability", "modification_rate"]
    all_attacked = len(complete) == len(rows)
    if all_attacked:
        metrics.append("attack_rate")
        rows = [{**r, "attack_rate": complete[r["profile_id"]]} for r in rows]
    order, reasons = coverage_order(rows, metrics)
    comparisons = [describe_set(order[:k], rows, metrics) for k in SIZES if k <= len(rows)]
    stability, stability_hashes = stability_results(directory, rows, comparisons, manifest)
    empirical = frontier(rows, metrics)
    first_passing = next((c["size"] for c in comparisons if c["max_gap_units"] <= 1
                          and not c["missing_parameter_levels"]), None)
    decisions = []
    demo_ids = set(comparisons[0]["ids"])
    for row in rows:
        witness = next((b for b in rows if dominates(b, row, metrics)), None)
        decisions.append({"profile_id": row["profile_id"], "profile": row["profile"],
                          "metrics": {m: row[m] for m in metrics},
                          "attack_complete": row["profile_id"] in complete,
                          "empirical_dominator": witness["profile_id"] if witness else None,
                          "diagnostic_15_member": row["profile_id"] in demo_ids,
                          "ordering_reason": reasons[row["profile_id"]],
                          "final_decision": "pending_stability_and_dynamic_validation",
                          "safe_dynamic_exclusion": False})
    return {"generated_utc": datetime.now(timezone.utc).isoformat(),
            "status": "three_objective_screen_only" if all_attacked else "registration_diagnostic_attack_pending",
            "registration_count": len(rows), "attack_complete": len(complete),
            "attack_rows": len(attacks), "attack_incomplete_ids": sorted(set(attacks)-set(complete)),
            "metrics": metrics, "tolerances": TOLERANCES,
            "tolerance_status": "exploratory engineering scales, not significance thresholds; specified after registration inspection, before full attack results",
            "final_candidate_count": None, "dynamic_paths_evaluated": 0,
            "stability_complete": False, "safe_excluded_count": 0,
            "registration_stability_complete": all(c["complete"] for c in stability),
            "stability_contexts": stability,
            "tolerance_sensitivity": [{"scale": scale, "smallest_tested_cover_size": next(
                (c["size"] for c in comparisons if c["max_gap_units"] <= scale
                 and not c["missing_parameter_levels"]), None)} for scale in (.5, 1., 2.)],
            "smallest_tested_cover_size": first_passing,
            "empirical_frontier_ids": [r["profile_id"] for r in empirical],
            "empirical_frontier_unique_metrics": len({tuple(r[m] for m in metrics) for r in empirical}),
            "original_fifteen": describe_set(old_selection(rows), rows, metrics),
            "comparisons": comparisons, "decisions": decisions,
            "length_groups": [{"length": length, "count": len(group),
                "zero_collision": sum(r["collision_probability"] == 0 for r in group),
                "min_cost": min(r["modification_rate"] for r in group),
                "max_cost": max(r["modification_rate"] for r in group)}
                for length in (8,10,12,14,16)
                for group in [[r for r in rows if r["profile"]["min_length"] == length]]],
            "source_hashes": {**hashes, **attack_hashes, **stability_hashes}, "manifest": manifest}


def report(result):
    r = result
    lines = ["# 口令策略候选数量探索记录", "",
        f"更新：{r['generated_utc']}。本文件由真实检查点生成。", "",
        "## 当前结论", "",
        f"注册评价已完成 {r['registration_count']}/960；完整百万预算攻击评价完成 {r['attack_complete']}/960。",
        "尚未冻结候选数量，尚未完成自由重复的十批动态搜索。稳定性检查进度见下文；攻击稳定性尚未完成。",
        "目前全部 960 条保留在研究范围内，数学上安全排除 0 条。下面的覆盖集合只是后续验证对象，不是最终筛选名单。", "",
        "## 1. 尝试与纠正的实验记录", "",
        "| 尝试 | 实际结果或问题 | 决策与理由 |", "| --- | --- | --- |",
        "| 人工列出原 15 条 | 没有市场采用率数据，也没有全池性能比较 | 撤回最热门、最具代表性等说法，保留为历史对照 |",
        "| 固定首批、只能加严的旧路径试验 | 2,023 条结构路径，247 条通过当时成本条件；其余 1,776 条被旧条件排除 | 不作为自由重复的全量结果，旧排除不能搬到新实验 |",
        "| 澄清每批自由选择且允许重复 | K 条模板十批对应 K¹⁰，15 条时为 576,650,390,625 条 | 首批也自由选，不继承上一模板的长度和开发名单，不强迫加严 |",
        "| 扩展至全部 960 条开发注册评价 | 相同 20,000 名开发验证用户，十批各 2,000 人；每个模板连续使用十批 | 不先按成本删除模板，不用正式十万人给候选打分 |",
        f"| 全池百万预算 A1 评估 | 完成 {r['attack_complete']}/960 | 未完成的攻击不视为零命中率，不给局部完成集合排全池名次 |",
        "| 比较不同候选数量的覆盖 | 本文后续给出实际计算的覆盖表 | 它衡量模板初筛的指标保留程度，不证明混合路径的全局最优 |", "",
        "## 2. 指标与数据边界", "",
        "训练 60,000 条、调参 20,000 条、开发验证 20,000 条；按出现次数模拟用户，并非已核验的独立账户。正式十万人未用于本轮候选评分，但旧实验曾查看过，不能声称是完全未接触的盲测数据。",
        "开发筛选每批 2,000 人，正式研究每批 10,000 人；历史名单触发频次会随批次规模改变，因此筛选覆盖不能直接当作正式规模效果。所有口令修改来自既定响应模拟器，结论依赖其生成机制，不能外推为真实用户行为定律。",
        "碰撞概率为从 N 名用户中不放回抽取两人的口令相同概率：", "",
        "$$", r"\widehat C=\frac{\sum_w n_w(n_w-1)}{N(N-1)}", "$$", "",
        "这是同类配对比例，亦是 Simpson 集中度的有限样本形式；在独立同分布假设下估计总体碰撞概率。零观测不等于总体风险为零。",
        "成本使用需要修改原口令的用户比例；它不能完整代表记忆难度或每次修改的负担。隐藏阻断另有记录，所有用户均保留，无用户放弃。",
        "攻击采用按策略响应后的开发训练与调参数据，评价频率、字典变换、字符 n-gram 的 A1 联合命中率。百万预算指各模型的猜测预算，联合命中并不是一个总共只猜一百万次的单一攻击器。", "",
        "## 3. 全池注册结果", "",
        "| 最短长度 | 模板数 | 本样本零碰撞模板数 | 修改率范围 |", "| --- | ---: | ---: | ---: |"]
    for g in r["length_groups"]:
        lines.append(f"| ≥{g['length']} | {g['count']} | {g['zero_collision']} | {g['min_cost']:.3%}～{g['max_cost']:.3%} |")
    lines += ["", "长度更高往往降低重复，但大幅增加修改人数。不能因为零碰撞就认定它最抗猜测，也不能因修改率高便直接删除：本阶段没有设置硬成本淘汰线。", "",
        f"当前参与比较的指标为：{'、'.join(METRIC_NAMES[m] for m in r['metrics'])}。经验非支配模板 {len(r['empirical_frontier_ids'])} 条，对应 {r['empirical_frontier_unique_metrics']} 个不同指标向量。非支配是指没有另一条在所有当前指标都不差、且至少一项更好。相同向量不代表响应口令、历史记忆或未来行为相同。", "",
        "## 4. 原 15 条为什么不足以直接作为最终池", "",
        f"原 15 条遗漏的参数水平：{', '.join(r['original_fifteen']['missing_parameter_levels']) or '无'}。",
        f"原集合最大联合覆盖差为 {r['original_fifteen']['max_gap_units']:.4f} 个容差单位；在容差内覆盖 {r['original_fifteen']['within_tolerance']}/960 条。",
        "缺少某参数水平本身不证明性能差，但没有覆盖它就不能断言它无用。尤其原集合完全没有键盘模式限制，当前全池评价允许直接检验其作用。", "",
        "## 5. 候选数量比较：已经执行的诊断", "",
        "先保留各目标极值，再补全参数水平，再逐次加入当前联合覆盖差最大的模板，编号作为并列排序。这样得到嵌套集合；算法可复现，但不声称找到了同规模的最优子集。参数覆盖也不等于所有参数交互已被覆盖。",
        "覆盖某模板必须由集合内同一条策略同时接近其所有目标，不能分别借用三条不同策略拼成不存在的理想结果。",
        "容差单位设为：每百万用户对增加 0.1 对相同口令、修改率增加 1 个百分点、百万预算攻击命中率增加 0.5 个百分点。最大差≤1表示每条目标模板都能找到同时满足这些容差的代表。容差是本研究的探索性尺度，不是公认阈值或显著性检验；它在看过注册结果、全池攻击完成前设定。", "",
        "**攻击未齐时，下表仅比较碰撞和修改率，不得据此定最终数量。**", "",
        "| 集合规模 | 最大联合覆盖差（容差单位） | 容差内覆盖 | 遗漏参数水平数 | 十批结构空间 |",
        "| --- | ---: | ---: | ---: | ---: |"]
    for c in r["comparisons"]:
        lines.append(f"| {c['size']} | {c['max_gap_units']:.6f} | {c['within_tolerance']}/960 | {len(c['missing_parameter_levels'])} | {c['paths_for_ten_cohorts']} |")
    lines += ["", f"在当前指标和容差下，测试规模中最小达标的是 {r['smallest_tested_cover_size']} 条。这个数字是诊断结果，**不是正式动态候选数量建议**。",
        "原始名单、每条模板的代表、差值、经验支配证据及数据指纹见同目录 JSON。即使覆盖差为零，未来动态历史也可能不同。", "",
        "容差敏感性（所有目标同时缩放；嵌套选择顺序不变）：", "",
        "| 容差倍率 | 测试规模中最小达标数 |", "| --- | ---: |"]
    for s in r["tolerance_sensitivity"]:
        lines.append(f"| {s['scale']} | {s['smallest_tested_cover_size']} |")
    lookup = {d["profile_id"]: d for d in r["decisions"]}
    lines += ["", "### 当前 15 条诊断集合：尚未冻结", "",
        "下列名单随完整攻击数据到齐可能改变；30 条及更大集合的编号见 exploration_summary.json 的 comparisons 字段。", "",
        "| 编号 | 模板规则 | 每百万对同口令数 | 修改率 | 本次加入理由 |",
        "| --- | --- | ---: | ---: | --- |"]
    for key in r["comparisons"][0]["ids"]:
        item = lookup[key]
        why = item["ordering_reason"]
        if why.startswith("objective_extreme:"):
            why = "保留" + METRIC_NAMES[why.split(":", 1)[1]] + "极值"
        else:
            why = {"parameter_level_coverage": "补足参数水平", "worst_joint_objective_gap": "补足尚未覆盖的权衡点",
                   "zero_measured_gap_catalog_order_padding": "当前指标已覆盖，按编号补足规模"}[why]
        lines.append(f"| {key} | {policy_label(item['profile'])} | {item['metrics']['collision_probability'] * 1e6:.6f} | {item['metrics']['modification_rate']:.3%} | {why} |")
    lines += ["", "### 注册稳定性：保持原集合，换情境重新评价", "",
        "同一组用户，使用固定身份；两次打乱顺序（43、44），两次更换响应种子（43、44），以及热门口令先注册、后注册两种离线压力情境。后两者用验证频次构造顺序，不代表在线控制器知道未来用户。",
        "每种情境都评价全部 960 条，候选集合仍由基准情境确定，不能根据当前情境的最优结果重新选集合再宣称稳定。本检查仍是单模板十批，未代替混合动态历史，也没有重算这些情境的攻击。", "",
        "| 情境 | 完成数 | 指标变化模板数 | 基准支配证据不再严格成立的条数 |",
        "| --- | ---: | ---: | ---: |"]
    for s in r["stability_contexts"]:
        lines.append(f"| {CONTEXT_NAMES[s['context']]} | {s['count']}/960 | {s.get('changed_profiles', '待完成')} | {s.get('baseline_dominance_no_longer_strict', '待完成')} |")
    finished = [s for s in r["stability_contexts"] if s["complete"]]
    if finished:
        lines += ["", "各规模在已完成稳定性情境中的最差联合覆盖差（仅碰撞和修改率）：", "",
                  "| 规模 | 最差覆盖差 |", "| --- | ---: |"]
        for index, c in enumerate(r["comparisons"]):
            worst = max(s["comparisons"][index]["max_gap_units"] for s in finished)
            lines.append(f"| {c['size']} | {worst:.6f} |")
    lines += ["", "支配证据不再严格成立可能是并列或反转，不等于对方全面胜出；具体结果保留在检查点。无历史依赖的模板在仅换顺序时若最终指标变化，程序会拒绝生成结论，避免混淆顺序与随机行为。", "",
        "## 6. 排除了哪些、保留了哪些", "",
        "- 研究范围：P0001～P0960 全部保留，尚无安全删除项。",
        "- 已排除的做法：用原 15 条的人工选择当科学依据；把旧受限路径当 K¹⁰；把单次零碰撞当绝对安全；把未完成攻击当低风险；把经验支配当动态数学剪枝。",
        "- 诊断集合的未入选项：只是当前给定规模下没有被贪心覆盖算法选中，或指标已被近似覆盖；不是无用策略。逐条理由已记录，不能据此删掉包含它的全部路径。",
        "- 下一步：补齐全池攻击，加入三目标比较；结合注册稳定性，再补攻击和混合动态历史的验证，然后冻结数量、编号和适用范围。之后才对冻结集合运行有证明的精确搜索。", "",
        "## 7. 可复现记录与限制", "",
        "输入来自 catalog_screen_seed42 的八份注册和八份攻击检查点；每份完整行的 SHA-256 记录在 exploration_summary.json。读取时忽略正在写入的末尾半行，不忽略已完成行的解析错误或重复编号。",
        "报告可用 tools/explore_catalog_size.py 重新生成，不会重新模拟注册或攻击。候选覆盖只用已完成的全池统一指标；缺少任一攻击项时不启用第三目标。",
        "本记录提供实验步骤、观测和决定依据。最终候选集的精确最优只对该集合有效，不能扩展为 960 条全部动态路径的全局最优。", ""]
    return "\n".join(lines)


def save(directory, result):
    markdown = report(result)
    # This generated document uses no code fences and one display equation.
    # Validate delimiters before replacing the readable snapshot.
    if (markdown.count('```') % 2 or markdown.count('$$') % 2 or
            markdown.count('**') % 2 or '\\(' in markdown or '\\[' in markdown):
        raise ValueError('Generated Markdown has unbalanced or unsupported delimiters')
    for name, content in (("exploration_summary.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n"),
                          ("候选数量探索记录.md", markdown)):
        path = directory / name
        temporary = path.with_suffix(path.suffix + ".part")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    previous = None
    while True:
        signature = tuple((str(p.relative_to(args.input_dir)), p.stat().st_size)
                          for p in sorted(args.input_dir.rglob("*_shard_*.jsonl")))
        if signature != previous:
            result = analyze(args.input_dir)
            save(args.input_dir, result)
            print(json.dumps({k: result[k] for k in ("status", "attack_complete", "smallest_tested_cover_size")}), flush=True)
            previous = signature
            if (result["attack_complete"] == result["registration_count"]
                    and result["registration_stability_complete"]):
                break
        if not args.watch:
            break
        time.sleep(60)


if __name__ == "__main__":
    main()
