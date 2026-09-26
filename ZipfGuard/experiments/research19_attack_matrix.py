"""Build the research19 attack matrix from current JSON inputs.

Numbers are copied from those files. This module does not invent cracked counts.
Inputs must share one preprocess version, source hash, and seed. A budget label
is taken only from a point with that budget and axis.
"""
from __future__ import annotations

import hashlib
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


def _require(payload: dict, filename: str, field: str):
    if field not in payload or payload[field] in (None, ""):
        raise ValueError(f"{filename} 缺少 {field}")
    return payload[field]


def _match(filename: str, field: str, actual, expected) -> None:
    if actual != expected:
        raise ValueError(f"{filename} 的 {field}={actual!r}，与 {expected!r} 不一致")


def select_budget_point(points: object, *, budget: int, axis: str, label: str) -> dict:
    """Return the one point for this budget and axis. Never use the last point."""
    if not isinstance(points, list) or not points:
        raise ValueError(f"{label} 没有预算点，不能借用其他预算")
    if not axis:
        raise ValueError(f"{label} 缺少 axis")
    matches = []
    for point in points:
        if not isinstance(point, dict) or "budget" not in point or "axis" not in point:
            raise ValueError(f"{label} 的预算点缺少 budget 或 axis")
        if point["budget"] == budget and point["axis"] == axis:
            matches.append(point)
    if not matches:
        raise ValueError(f"{label} 没有 budget={budget}、axis={axis} 的点")
    if len(matches) != 1:
        raise ValueError(f"{label} 的 budget={budget}、axis={axis} 重复")
    point = matches[0]
    if point.get("incomplete") is True and point.get("cracked") is not None:
        raise ValueError(f"{label} 把未完成的 budget={budget} 写成了命中数")
    return point


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
    requested = _require(quota, "quota_hak5.json", "total_generation_budget")
    if unique_budget.get("budget") != requested:
        raise ValueError(
            f"quota_hak5.json 的唯一验证点预算是 {unique_budget.get('budget')}，不是总预算 {requested}"
        )
    if generation.get("generation_requested") != requested:
        raise ValueError(
            f"quota_hak5.json 的生成请求是 {generation.get('generation_requested')}，不是总预算 {requested}"
        )
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
        "unique_budget_requested": requested,
        "unique_budget_incomplete": unique_budget["incomplete"],
        "unique_budget_cracked": unique_budget["cracked"],
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


def _site_sha(identity: dict, site: str) -> str:
    sites = identity.get("sites")
    if not isinstance(sites, dict) or site not in sites:
        raise ValueError(f"preprocess_identity.json 没有 {site} 的当前身份")
    return _require(sites[site], f"preprocess_identity.json:{site}", "source_sha256")


def _aligned_run(payload: dict, filename: str, *, seed: int, preprocess: str, source_sha256: str) -> None:
    _match(filename, "protocol", _require(payload, filename, "protocol"), "research19-v1")
    _match(filename, "seed", _require(payload, filename, "seed"), seed)
    _match(filename, "preprocess_version", _require(payload, filename, "preprocess_version"), preprocess)
    _match(filename, "source_sha256", _require(payload, filename, "source_sha256"), source_sha256)


def _frequency_rows(frequency: dict, identity: dict, *, seed: int, preprocess: str) -> list[dict]:
    filename = "frequency_matrix.json"
    _match(filename, "protocol", _require(frequency, filename, "protocol"), "research19-v1")
    _match(filename, "seed", _require(frequency, filename, "seed"), seed)
    _match(filename, "preprocess_version", _require(frequency, filename, "preprocess_version"), preprocess)
    rows = []
    for row in _require(frequency, filename, "rows"):
        site = _require(row, filename, "site")
        status = _require(row, filename, "status")
        if status != "completed":
            rows.append({
                "site": site,
                "status": status,
                "budget": 1000,
                "cracked": None,
                "total": None,
                "axis": None,
                "incomplete": True,
                "reason": row.get("reason"),
            })
            continue
        axis = _require(row, f"{filename}:{site}", "points_axis")
        _match(f"{filename}:{site}", "source_sha256", _require(row, f"{filename}:{site}", "source_sha256"), _site_sha(identity, site))
        point = select_budget_point(row.get("points"), budget=1000, axis=axis, label=f"{filename}:{site}")
        rows.append({
            "site": site,
            "status": status,
            "budget": 1000,
            "cracked": point.get("cracked"),
            "total": point.get("total"),
            "axis": point["axis"],
            "incomplete": point.get("incomplete"),
            "reason": row.get("reason"),
        })
    return rows


def build_matrix(report_dir: Path) -> dict:
    loaded = {name: _load(report_dir, name) for name in REQUIRED}
    identity = loaded["preprocess_identity.json"]
    if not isinstance(identity.get("sites_not_rescanned"), list):
        raise ValueError("preprocess_identity.json 缺少 sites_not_rescanned，不能当成没有待重读站点")
    preprocess = _require(identity, "preprocess_identity.json", "preprocess_version")
    hak = loaded["hak5_smoke.json"]
    seed = _require(hak, "hak5_smoke.json", "seed")
    hak_sha = _site_sha(identity, "hak5")
    _aligned_run(hak, "hak5_smoke.json", seed=seed, preprocess=preprocess, source_sha256=hak_sha)
    _match("hak5_smoke.json", "split", _require(hak, "hak5_smoke.json", "split"), "occurrence_60_20_20")
    neural = loaded["neural_eval.json"]
    _aligned_run(neural, "neural_eval.json", seed=seed, preprocess=preprocess, source_sha256=hak_sha)
    _match("neural_eval.json", "split", _require(neural, "neural_eval.json", "split"), hak["split"])
    quota = loaded["quota_hak5.json"]
    _aligned_run(quota, "quota_hak5.json", seed=seed, preprocess=preprocess, source_sha256=hak_sha)
    _match("quota_hak5.json", "site", _require(quota, "quota_hak5.json", "site"), "hak5")
    transfer = loaded["transfer_hak5_hotmail.json"]
    pair = _require(transfer, "transfer_hak5_hotmail.json", "pair")
    _match("transfer_hak5_hotmail.json", "protocol", _require(transfer, "transfer_hak5_hotmail.json", "protocol"), "research19-v1")
    _match("transfer_hak5_hotmail.json", "seed", _require(pair, "transfer_hak5_hotmail.json:pair", "seed"), seed)
    _match("transfer_hak5_hotmail.json", "preprocess_version", _require(transfer, "transfer_hak5_hotmail.json", "preprocess_version"), preprocess)
    _match("transfer_hak5_hotmail.json", "train_source_sha256", _require(transfer, "transfer_hak5_hotmail.json", "train_source_sha256"), hak_sha)
    _match("transfer_hak5_hotmail.json", "target_source_sha256", _require(transfer, "transfer_hak5_hotmail.json", "target_source_sha256"), _site_sha(identity, "hotmail"))
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
        "frequency_budget_1000": _frequency_rows(
            loaded["frequency_matrix.json"], identity, seed=seed, preprocess=preprocess,
        ),
        "transfer_hak5_to_hotmail": {
            "frequency": loaded["transfer_hak5_hotmail.json"]["frequency"]["points"],
            "omen": loaded["transfer_hak5_hotmail.json"]["omen"].get("points"),
            "pcfg": loaded["transfer_hak5_hotmail.json"]["pcfg_open_stream"].get("points"),
        },
        "quota": _quota_view(loaded["quota_hak5.json"]),
    }
    matrix["input_sha256"] = {
        name: hashlib.sha256((report_dir / name).read_bytes()).hexdigest() for name in REQUIRED
    }
    matrix["seed"] = seed
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
