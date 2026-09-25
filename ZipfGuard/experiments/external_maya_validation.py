"""Fit Zipf and HTPG features on MAYA corpora that are not the RockYou baseline.

Each site is described with aggregate numbers only. A failed download or a
failed paper-threshold fit stays in the report. These fits do not change the
suggestion-compare weights, and they are not a guessing-attack result.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.counted_corpus import CorpusFormatError
from core.htpg_features import HTPGFeatureExtractor
from core.site_distribution import analyze_occurrence_file
from data.maya_catalog import dataset_card
from data.maya_fetch import DEFAULT_DATASETS, MAX_ARCHIVE_BYTES, ensure_dataset


ROCKYOU_IGR_UNIQUE_ORDER = (
    "capital",
    "lsd_structure",
    "length",
    "date",
    "specplace",
    "lowercase",
    "word_type",
    "lastname",
    "keyboard",
)
CLAIM = {
    "validates": "MAYA 目录中每个已下载站点的 Zipf 曲率拟合，以及该站点自己头尾标签上的九特征 IGR。",
    "does_not_validate": "猜测攻击 cracked@B、跨站防御提升、论文 69%、25%、80.23%，或真实可记忆性。",
    "weights_refit": False,
    "rockyou_order_role": "MAYA 的 rockyou 不是独立外部站点。它只和其余站点一起做同样的聚合拟合，不重估建议策略。",
}
CACHE_VERSION = "maya-aggregate-v2"


def spearman(left: list[str], right: list[str]) -> float | None:
    if len(left) != len(right) or set(left) != set(right) or len(left) < 2:
        return None
    rank = {name: index for index, name in enumerate(right)}
    count = len(left)
    gap = sum((index - rank[name]) ** 2 for index, name in enumerate(left))
    return 1 - (6 * gap) / (count * (count * count - 1))


def _safe_error(exc: BaseException) -> str:
    detail = exc.args[0] if exc.args and isinstance(exc.args[0], str) else type(exc).__name__
    detail = " ".join(detail.split())
    if len(detail) > 180:
        detail = detail[:180] + "…"
    return f"{type(exc).__name__}: {detail}"


def _analyze_protocols(
    path: Path, extractor: HTPGFeatureExtractor, *, max_feature_types: int,
) -> dict:
    try:
        primary = analyze_occurrence_file(
            path, extractor, min_frequency_exclusive=3, max_feature_types=max_feature_types,
        )
        primary["paper_threshold"] = True
        return {"status": "ok", "primary": primary, "sensitivity": None}
    except (CorpusFormatError, ValueError) as exc:
        failure = _safe_error(exc)
    try:
        sensitivity = analyze_occurrence_file(
            path, extractor, min_frequency_exclusive=0, max_feature_types=max_feature_types,
        )
    except (CorpusFormatError, ValueError) as exc:
        return {
            "status": "failed",
            "error": failure,
            "sensitivity_error": _safe_error(exc),
            "primary": None,
            "sensitivity": None,
        }
    sensitivity["paper_threshold"] = False
    sensitivity["not_paper_protocol"] = True
    return {
        "status": "paper_threshold_failed",
        "error": failure,
        "primary": None,
        "sensitivity": sensitivity,
    }


def _order_of(block: dict | None) -> list[str] | None:
    if not block or block.get("feature_status") != "computed":
        return None
    features = block.get("features") or {}
    order = features.get("order_unique")
    return list(order) if order else None


def _cache_file(root: Path, name: str) -> Path:
    return root / name / "aggregate.json"


def _load_cache(path: Path, payload_sha256: str, max_feature_types: int) -> dict | None:
    if not path.is_file():
        return None
    cached = json.loads(path.read_text(encoding="utf-8"))
    if cached.get("cache_version") != CACHE_VERSION:
        return None
    if cached.get("payload_sha256") != payload_sha256 or cached.get("max_feature_types") != max_feature_types:
        return None
    result = cached.get("result")
    return result if isinstance(result, dict) else None


def _save_cache(path: Path, payload_sha256: str, max_feature_types: int, result: dict) -> None:
    path.write_text(json.dumps({
        "cache_version": CACHE_VERSION,
        "payload_sha256": payload_sha256,
        "max_feature_types": max_feature_types,
        "result": result,
        "plaintext_retained": False,
    }, ensure_ascii=False) + "\n", encoding="utf-8")


def _one_site(
    name: str, *, root: Path, extractor: HTPGFeatureExtractor, max_bytes: int, max_feature_types: int,
) -> dict:
    card = dataset_card(name)
    entry = {
        "dataset": card,
        "independent_of_paper_rockyou": name != "rockyou",
        "lexicon_language": "en",
        "lexicon_matches_site_language": card["language"] == "en",
        "analysis_reused": False,
    }
    try:
        local = ensure_dataset(name, root, max_bytes=max_bytes)
        payload_path = root / name / "extracted" / local["payload_name"]
        entry["local"] = {
            "reused": local["reused"],
            "archive_sha256": local["archive_sha256"],
            "archive_bytes": local["archive_bytes"],
            "payload_name": local["payload_name"],
            "payload_sha256": local["payload_sha256"],
            "payload_bytes": local["payload_bytes"],
        }
        cached = _load_cache(_cache_file(root, name), local["payload_sha256"], max_feature_types)
        if cached is None:
            cached = _analyze_protocols(payload_path, extractor, max_feature_types=max_feature_types)
            _save_cache(_cache_file(root, name), local["payload_sha256"], max_feature_types, cached)
        else:
            entry["analysis_reused"] = True
        entry["result"] = cached
    except Exception as exc:
        entry["result"] = {"status": "failed", "error": _safe_error(exc), "primary": None, "sensitivity": None}
    result = entry["result"]
    if result.get("primary"):
        compared = "primary"
    elif result.get("sensitivity"):
        compared = "sensitivity_not_paper"
    else:
        compared = None
    order = _order_of(result.get("primary") or result.get("sensitivity"))
    entry["igr_compared_protocol"] = compared
    entry["igr_spearman_vs_rockyou_unique"] = (
        None if name == "rockyou" else spearman(order, list(ROCKYOU_IGR_UNIQUE_ORDER)) if order else None
    )
    return entry


def assert_export_safe(payload: dict) -> None:
    forbidden = {"password", "passwords", "plaintext", "guess", "top_guesses", "frequencies"}

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).lower() in forbidden:
                    raise RuntimeError(f"导出字段被拒绝：{key}")
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str) and len(node) > 500:
            raise RuntimeError("导出字符串过长")

    walk(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="对本地或现下载的 MAYA 语料做聚合分布验证")
    parser.add_argument("--datasets", nargs="+", default=list(DEFAULT_DATASETS))
    parser.add_argument("--data-dir", type=Path, default=Path("local_datasets/maya"))
    parser.add_argument("--lexicon", type=Path, default=Path("resources/htpg_reference_v1.json"))
    parser.add_argument("--output", type=Path, default=Path("reports/maya_external_validation.json"))
    parser.add_argument("--max-bytes", type=int, default=MAX_ARCHIVE_BYTES)
    parser.add_argument("--max-feature-types", type=int, default=7_000_000)
    args = parser.parse_args()
    ready = [name for name in args.datasets if (args.data_dir / name / "ready.json").is_file()]
    missing = [name for name in args.datasets if name not in ready]
    extractor = HTPGFeatureExtractor.from_profile(args.lexicon)
    payload = {
        "claim": CLAIM,
        "rockyou_igr_unique_order": list(ROCKYOU_IGR_UNIQUE_ORDER),
        "sites": [],
        "max_feature_types": args.max_feature_types,
        "plaintext_retained": False,
        "method_weights_refit": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for name in ready + missing:
        payload["sites"].append(_one_site(
            name, root=args.data_dir, extractor=extractor,
            max_bytes=args.max_bytes, max_feature_types=args.max_feature_types,
        ))
        assert_export_safe(payload)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result = payload["sites"][-1]["result"]
        block = result.get("primary") or result.get("sensitivity") or {}
        fit = block.get("fit") or {}
        alpha = fit.get("alpha")
        alpha_text = f"{alpha:.4f}" if isinstance(alpha, float) else "-"
        print(
            f"{name} status={result['status']} reused={payload['sites'][-1]['analysis_reused']} "
            f"independent={payload['sites'][-1]['independent_of_paper_rockyou']} "
            f"alpha={alpha_text} x0={fit.get('curvature_x0', '-')} "
            f"spearman={payload['sites'][-1]['igr_spearman_vs_rockyou_unique']}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
