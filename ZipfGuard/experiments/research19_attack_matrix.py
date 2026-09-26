"""Build the research19 attack matrix from current JSON inputs.

Numbers are copied from those files. This module does not invent cracked counts.
"""
from __future__ import annotations

import json
from pathlib import Path


REQUIRED = (
    "hak5_smoke.json",
    "neural_eval.json",
    "frequency_matrix.json",
    "transfer_hak5_hotmail.json",
    "quota_hak5.json",
    "preprocess_identity.json",
)


def _load(directory: Path, name: str) -> dict:
    path = directory / name
    if not path.is_file():
        raise FileNotFoundError(f"缺少 {path}，拒绝生成主表")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} 不是对象")
    return payload


def _reject_incomplete_zero(node: object, location: str) -> None:
    if isinstance(node, dict):
        if node.get("incomplete") is True and node.get("cracked") == 0:
            raise ValueError(f"{location} 把未完成预算写成了 0 次命中")
        for key, value in node.items():
            _reject_incomplete_zero(value, f"{location}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _reject_incomplete_zero(value, f"{location}[{index}]")


def _quota_view(quota: dict) -> dict:
    if "test_equal_share_union" in quota or "generation" not in quota:
        raise ValueError("配额结果仍是旧结构，拒绝写入主表")
    generation = quota["generation"]
    emitted = quota["unique_verification_among_emitted"]
    unique_budget = quota["unique_verification_budget"]
    if (
        generation.get("generation_completion") == "resource_truncated"
        and generation.get("generation_raw_emitted") == generation.get("generation_requested")
    ):
        raise ValueError("生成预算已经发完，不能再标成 resource_truncated")
    if emitted.get("axis") != "unique_position":
        raise ValueError("并集命中必须标在 unique_position")
    return {
        "generation_completion": generation["generation_completion"],
        "generation_requested": generation["generation_requested"],
        "generation_raw_emitted": generation["generation_raw_emitted"],
        "within_model_duplicate_slots": generation["within_model_duplicate_slots"],
        "cross_model_duplicate_slots": generation["cross_model_duplicate_slots"],
        "unique_verification_candidates": generation["unique_verification_candidates"],
        "unique_budget_900_incomplete": unique_budget["incomplete"],
        "unique_budget_900_cracked": unique_budget["cracked"],
        "emitted_budget": emitted["budget"],
        "emitted_cracked": emitted["cracked"],
        "emitted_total": emitted["total"],
        "h2_confirmation": False,
        "claim_supported": False,
    }


def _passllm_view(neural: dict) -> dict:
    row = neural.get("passllm")
    if not isinstance(row, dict):
        raise ValueError("neural_eval.json 缺少 passllm")
    points = row.get("points_sorted_retained_position")
    if not isinstance(points, list) or not points:
        raise ValueError("PassLLM 缺少排序后保留候选的位置")
    if any(point.get("axis") != "sorted_retained_position" for point in points):
        raise ValueError("PassLLM 内层 axis 仍不是 sorted_retained_position")
    if row.get("points_raw_position") is not None:
        raise ValueError("PassLLM 原始生成位置必须为空，不能借用排序后的轴")
    return {
        "scheduled_trajectories": row.get("scheduled_trajectories"),
        "generation_order_available": False,
        "points_raw_position": None,
        "points_sorted_retained_position": points,
        "points_unique_position": row.get("points_unique_position"),
        "divide_search_completed": False,
    }


def build_matrix(report_dir: Path) -> dict:
    loaded = {name: _load(report_dir, name) for name in REQUIRED}
    identity = loaded["preprocess_identity.json"]
    if not isinstance(identity.get("sites_not_rescanned"), list):
        raise ValueError("preprocess_identity.json 缺少 sites_not_rescanned，不能当成没有待重读站点")
    hak = loaded["hak5_smoke.json"]
    matrix = {
        "protocol": "research19-v1",
        "schema": "axes-v3",
        "preprocess_version": identity.get("preprocess_version"),
        "inputs": list(REQUIRED),
        "sites_not_rescanned": identity["sites_not_rescanned"],
        "claim_supported": False,
        "plaintext_retained": False,
        "completed_multi_model": False,
        "hak5": {
            "frequency": hak["frequency"]["points"],
            "omen": {
                "status": hak["omen"]["status"],
                "axis": hak["omen"].get("points_axis"),
                "points": hak["omen"].get("points"),
            },
            "pcfg_raw_stream": {
                "status": hak["pcfg_open_stream"]["status"],
                "axis": hak["pcfg_open_stream"].get("points_axis"),
                "candidate_filter_applied": hak["pcfg_open_stream"].get("candidate_filter_applied"),
                "points": hak["pcfg_open_stream"].get("points"),
            },
            "passgpt": {
                "points_raw_position": loaded["neural_eval.json"]["passgpt"].get("points_raw_position"),
                "points_are_generation_order": loaded["neural_eval.json"]["passgpt"].get("points_are_generation_order"),
            },
            "passllm": _passllm_view(loaded["neural_eval.json"]),
            "fla": hak.get("fla"),
            "passgan": hak.get("passgan"),
        },
        "frequency_budget_1000": [
            {
                "site": row["site"],
                "status": row["status"],
                "cracked": None if not row.get("points") else row["points"][-1]["cracked"],
                "total": None if not row.get("points") else row["points"][-1]["total"],
                "axis": None if not row.get("points") else row["points"][-1].get("axis"),
                "incomplete": None if not row.get("points") else row["points"][-1].get("incomplete"),
                "reason": row.get("reason"),
            }
            for row in loaded["frequency_matrix.json"]["rows"]
        ],
        "transfer_hak5_to_hotmail": {
            "frequency": loaded["transfer_hak5_hotmail.json"]["frequency"]["points"],
            "omen": loaded["transfer_hak5_hotmail.json"]["omen"].get("points"),
            "pcfg": loaded["transfer_hak5_hotmail.json"]["pcfg_open_stream"].get("points"),
        },
        "quota": _quota_view(loaded["quota_hak5.json"]),
    }
    _reject_incomplete_zero(matrix, "attack_matrix")
    return matrix


def main(root: Path | None = None) -> int:
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    report_dir = root / "reports" / "research19"
    matrix = build_matrix(report_dir)
    output = report_dir / "attack_matrix.json"
    output.write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    quota = matrix["quota"]
    print(json.dumps({
        "generation_completion": quota["generation_completion"],
        "generation_raw_emitted": quota["generation_raw_emitted"],
        "unique_verification_candidates": quota["unique_verification_candidates"],
        "emitted_cracked": quota["emitted_cracked"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
