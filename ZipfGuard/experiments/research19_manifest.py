"""Fingerprints for the 19-site line. Old synthetic tables are not current results."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_ID = "research19-v1"
LEXICON_RELATIVE = "resources/htpg_reference_v1.json"
ANALYSIS_SOURCES = (
    "core/counted_corpus.py",
    "core/htpg_fit.py",
    "core/htpg_igr.py",
    "core/htpg_features.py",
    "core/site_distribution.py",
    "core/occurrence_frequency.py",
    "experiments/external_maya_validation.py",
    "experiments/evaluation_validity.py",
    "experiments/research19_manifest.py",
)
HISTORICAL_PROTOCOLS = {"robustness-v1", "robustness-v2", "robustness-v3"}
HISTORICAL_WARNING = "历史协议结果。不能自动显示为 research19 或当前 robustness-v4 的方法效果。"


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def lexicon_sha256() -> str:
    return sha256_file(ROOT / LEXICON_RELATIVE)


def analysis_source_sha256() -> str:
    joined = "".join(sha256_file(ROOT / relative) for relative in ANALYSIS_SOURCES)
    return sha256_bytes(joined.encode("utf-8"))


def fit_config_sha256() -> str:
    config = {
        "paper_min_frequency_exclusive": 3,
        "sensitivity_min_frequency_exclusive": 0,
        "cutoff_rule": "floor(x0), then clipped to [1, number of ranks]",
        "rank_tie_break": "higher count, then UTF-8 lexicographic text; text is not stored",
    }
    return sha256_bytes(json.dumps(config, sort_keys=True).encode("utf-8"))


def lexicon_identity_sha256(extractor) -> str:
    """Hash the lexicon actually used, not only the default path."""
    recorded = (getattr(extractor, "lexicon_metadata", None) or {}).get("sha256")
    if isinstance(recorded, str) and len(recorded) == 64:
        return recorded
    payload = json.dumps(
        {"word_type": extractor.word_terms, "lastname": extractor.surname_terms},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return sha256_bytes(payload.encode("utf-8"))


def analysis_cache_key(*, payload_sha256: str, max_feature_types: int, lexicon_digest: str | None = None) -> dict:
    """Identity that must change when data, code, lexicon, or fit config changes."""
    parts = {
        "payload_sha256": payload_sha256,
        "max_feature_types": int(max_feature_types),
        "analysis_source_sha256": analysis_source_sha256(),
        "lexicon_sha256": lexicon_digest if lexicon_digest is not None else lexicon_sha256(),
        "fit_config_sha256": fit_config_sha256(),
    }
    parts["cache_key_sha256"] = sha256_bytes(json.dumps(parts, sort_keys=True).encode("utf-8"))
    return parts


def cache_key_from_parts(*, payload: bytes, lexicon: bytes, source: bytes, config: bytes, max_feature_types: int) -> str:
    """Pure helper so tests can show that any changed part changes the key."""
    body = {
        "payload_sha256": sha256_bytes(payload),
        "max_feature_types": int(max_feature_types),
        "analysis_source_sha256": sha256_bytes(source),
        "lexicon_sha256": sha256_bytes(lexicon),
        "fit_config_sha256": sha256_bytes(config),
    }
    return sha256_bytes(json.dumps(body, sort_keys=True).encode("utf-8"))


def historical_report_status(protocol: str | None) -> dict:
    historical = protocol in HISTORICAL_PROTOCOLS or protocol is None
    return {
        "protocol": protocol,
        "historical": historical,
        "current_research_protocol": PROTOCOL_ID,
        "warning": HISTORICAL_WARNING if historical else None,
        "may_display_as_current_method_result": False if historical else protocol == PROTOCOL_ID,
    }
