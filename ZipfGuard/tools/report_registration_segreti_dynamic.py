"""Recount completed prefixes, without rerunning or changing the comparator."""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.registration_joint_data import load_private
from tools.report_registration_dual_full import audit
from tools.report_registration_variable_scope import summarize
from experiments.registration_segreti_dynamic import sufficient_gain


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    root = parser.parse_args().root.resolve()
    manifest = json.loads((root/"manifest.json").read_text("utf-8"))
    cfg = manifest["config"]
    results, records = audit(root, cfg)
    arm = "segreti_dynamic"
    result = results[arm]
    path = root/"runs/11"/arm
    state = load_private(path/"state.pkl.gz")
    baseline = load_private(root/"private/segreti_baseline.pkl.gz")["rule"]
    assert len(baseline.shapes) == 213
    assert state["timeline"][0]["shape_sha256"] == baseline.summary()["shape_sha256"]
    assert all(rule["shape_size"] >= 213 for rule in state["timeline"])
    assert all(c["terminal_state"] == "continue_until_accepted_no_retry_cap" for c in state["costs"])
    audits = []
    for file in sorted(path.glob("candidate_audit_*.json")):
        data = json.loads(file.read_text("utf-8"))
        rows = data["candidates"]
        base_row = next(r for r in rows if "baseline" in r["roles"])
        assert base_row["rule"]["shape_sha256"] == baseline.summary()["shape_sha256"]
        assert any("hold" in r["roles"] for r in rows) and not base_row["failures"]
        for row in rows + data["confirmation"]:
            for key in ["history_actual_dual", "preview_actual_dual"]:
                assert row[key]["models"] == ["pcfg", "omen"] and not row[key]["estimated"]
        if data["status"] == "confirmed_extension":
            win = next(r for r in data["confirmation"] if r["number"] == data["nominee"])
            refs = [r for r in data["confirmation"] if set(r["roles"]) & {"hold", "baseline"}
                    and not r["failures"]]
            assert not win["failures"]
            assert sufficient_gain(min(r["predicted_A1"] for r in refs)-win["predicted_A1"], cfg)
        audits.append({"file": file.name, "sha256": digest(file), "status": data["status"],
                       "shape_count": data["chosen"]["shape_size"]})
    assert len(audits) == cfg["batches"]-1
    old = Path(cfg["comparison_source"])
    old_cfg = json.loads((old/"manifest.json").read_text("utf-8"))["config"]
    # Reuse the existing fail-closed prefix recount; suppress its report writes
    # so the original five-arm audit and all historical evidence stay unchanged.
    with patch("tools.report_registration_dual_full.write_json"):
        old_results, old_records = audit(old, {**old_cfg, "arms": ["segreti_struct_m_nohint"]})
    other = old_results["segreti_struct_m_nohint"]
    previous = load_private(old/"runs/11/segreti_struct_m_nohint/state.pkl.gz")
    assert digest(root/"private/partition.pkl.gz") == digest(old/"private/partition.pkl.gz")
    assert state["final_batches"][0] == previous["batches"][0]
    assert state["dev_batches"][0] == previous["dev_batches"][0]
    rows = [
        summarize(arm, "Segreti起点上的动态调整", result, state),
        summarize("segreti_struct_m_nohint", "固定Segreti名单", other, previous),
    ]
    dest = root/"deliverables"
    dest.mkdir(exist_ok=True)
    with (dest/"million_attempt_summary.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    lines = [
        "# 同一Segreti名单起点的动态调整：完整实验",
        "",
        "只新增运行动态方案。100,000条模拟用户记录，10批，每批10,000条，种子11。"
        "固定Segreti对照采用相同目标与开发分区的既有完整结果。",
        "",
        "首批直接使用同一份213种结构名单，目标、开发结果及重选序列已逐项核对。"
        "每轮保留原始Segreti名单与当前名单，尝试在完整名单之外新增5或20种近期结构。"
        "预览累计联合风险只要比更好的基准低、并通过另一预览角色确认后，即启用新增规则；没有0.5个百分点的最低门槛。",
        "",
        "所有候选、确认及最终攻击均使用PCFG与OMEN各百万次实际猜测。"
        "主联合结果最多使用200万次尝试，重复输出计入预算，同一账户只计一次。"
        "用户持续重选至接受，不设修改次数上限。",
        "",
        "| 方法 | PCFG命中率 | OMEN命中率 | 联合命中率 | 人均额外提交 | 至少重选一次 | 超过3次 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in rows:
        lines.append(f'| {r["label"]} | {r["pcfg"]:.3%} | {r["omen"]:.3%} | '
                     f'{r["union_each_model_B"]:.3%} | {r["extra"]:.5f} | '
                     f'{r["modification"]:.3%} | {r["more_than_three"]:.3%} |')
    lines += [
        "",
        f'联合命中率相对固定Segreti变化：{100*(rows[0]["union_each_model_B"]-rows[1]["union_each_model_B"]):+.3f}个百分点；'
        f'人均额外提交变化：{rows[0]["extra"]-rows[1]["extra"]:+.5f}次。',
        "",
        f'动态方案的额外提交次数90%分位数为{rows[0]["extra_p90"]}次、95%分位数为{rows[0]["extra_p95"]}次，最大{rows[0]["extra_max"]}次；固定Segreti对应为{rows[1]["extra_p90"]}、{rows[1]["extra_p95"]}和{rows[1]["extra_max"]}次。这些是实际运行值，不是用户修改上限。',
        "",
        "| 注册批次 | 实际阻断结构数 | 决策 |",
        "| --- | ---: | --- |",
    ]
    names = {"hold_anchor": "保持当前名单", "confirmed_extension": "确认后增加结构", "restore_segreti_baseline": "恢复原始Segreti名单"}
    decisions = {d["batch"]: names[d["status"]] for d in state["decisions"]}
    for i, rule in enumerate(state["timeline"], 1):
        lines.append(f'| {i} | {rule["shape_size"]} | {decisions.get(i, "原始Segreti名单")} |')
    lines += [
        "",
        "这是基于已观察结果修订方法后，在同一份目标样本上进行的一次探索性比较，"
        "不是独立验证。预览改善不保证最终攻击更低，结果按实际输出报告。"
        "未测量真人创建时间、记忆、沮丧或退出行为。"
        "Segreti对照是共享模拟条件下的机制适配，不是原真人实验的完整复现。",
        "",
    ]
    text = "\n".join(lines)
    assert text.count(chr(96)*3) % 2 == 0 and text.count("$$") % 2 == 0
    (dest/"完整实验结果.md").write_text(text, "utf-8")
    evidence = {
        "status": "passed", "new_arm_only": arm,
        "new_evidence": records, "comparator_evidence": old_records,
        "same_partition": True, "same_baseline_artifact": True,
        "first_batch_exactly_matches_static": True, "candidate_audits": audits,
        "configuration_and_source_manifest": str(root/"manifest.json"),
        "human_verification": "pending",
        "outputs": {p.name: digest(p) for p in dest.iterdir() if p.suffix in (".csv", ".md")},
    }
    (dest/"comparison_evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), "utf-8")
    status = json.loads((root/"status.json").read_text("utf-8"))
    status.update(stage="complete", audit="passed")
    (root/"status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({"audit": "passed", "summary": rows, "output": str(dest)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
