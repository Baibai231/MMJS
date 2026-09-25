"""Write the size-900 table from the saved run. Refuse if source hashes moved."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from experiments.provenance import robustness_manifest
from experiments.robustness_protocol import public_candidates


def candidate_sha256(mechanism: str) -> str:
    digest = hashlib.sha256()
    for value in public_candidates(mechanism):
        digest.update(hashlib.sha256(value.encode("utf-8")).digest())
    return digest.hexdigest()


def render(payload: dict, candidate_hashes: dict[str, str]) -> str:
    lines = [
        "# 由 reports/directed_size900.json 生成",
        "",
        "不要手改这张表。源码哈希与该次运行不一致时，生成脚本会拒绝写出。",
        "",
        f"协议 `{payload['protocol']}`。预算 {payload['budget']}。种子 {payload['provenance']['seeds']}。样本量 {payload['provenance']['sizes']}。",
        f"源码 SHA-256 `{payload['provenance']['source_sha256']}`。",
        "",
        "全体测试用户、封闭排序、绝对百分点下降。掉出候选集的比例一并列出。",
        "",
        "| 场景 | 长度加黑名单 | 论文 IGR | 预算/成本 | 预算/成本掉出候选集 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    labels = {
        "modern_blocklist": "长度加黑名单",
        "paper_igr_unique": "论文 IGR",
        "budget_cost": "预算/成本",
    }
    for mechanism in ("zipf", "long_tail"):
        arms = payload["mechanisms"][mechanism]["900"]["arms"]
        cells = []
        for key in ("modern_blocklist", "paper_igr_unique", "budget_cost"):
            value = arms[key]["mean"]
            if value is None or arms[key].get("comparison_published") is False:
                cells.append("未发布")
            else:
                cells.append(f"{value * 100:.1f}")
        outside = arms["budget_cost"]["outside_candidate_rate"]["mean"]
        lines.append(f"| {mechanism} | " + " | ".join(cells) + f" | {outside:.3f} |")
    lines.append("")
    lines.append("候选集 SHA-256（只哈希排序后的候选，不写出口令）：")
    lines.append("")
    for mechanism, digest in candidate_hashes.items():
        lines.append(f"- {mechanism}: `{digest}`")
    lines.append("")
    lines.append("单位是百分点。这不是“新方法已经优于基线”的结论。")
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    source = root / "reports" / "directed_size900.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    live = robustness_manifest(
        budget=int(payload["budget"]),
        seeds=list(payload["provenance"]["seeds"]),
        sizes=list(payload["provenance"]["sizes"]),
    )
    if live["source_sha256"] != payload["provenance"]["source_sha256"]:
        raise SystemExit("源码哈希与 directed_size900.json 不一致，拒绝生成表格")
    payload["provenance"]["candidate_sha256"] = {
        mechanism: candidate_sha256(mechanism) for mechanism in ("zipf", "long_tail")
    }
    output = root / "docs" / "generated_directed_size900.md"
    output.write_text(render(payload, payload["provenance"]["candidate_sha256"]), encoding="utf-8")
    print(output)
    print("outside", payload["mechanisms"]["long_tail"]["900"]["arms"]["budget_cost"]["outside_candidate_rate"]["mean"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
