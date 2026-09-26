"""Cross-site IGR summary from the stored MAYA aggregate. No passwords are copied."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "reports" / "maya_external_validation.json"


def summarize_features(path: Path = SOURCE) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for site in payload["sites"]:
        result = site["result"]
        block = result.get("primary") or result.get("sensitivity") or {}
        features = (block.get("features") or {}) if isinstance(block, dict) else {}
        order = features.get("order_unique")
        rows.append({
            "name": site["dataset"]["name"],
            "language_metadata": site["dataset"].get("language"),
            "lexicon_language": site.get("lexicon_language"),
            "lexicon_matches_site_language": site.get("lexicon_matches_site_language"),
            "igr_protocol": site.get("igr_compared_protocol"),
            "feature_status": block.get("feature_status"),
            "spearman_vs_rockyou_unique": site.get("igr_spearman_vs_rockyou_unique"),
            "order_unique": order,
            "causal_modification_effect": None,
        })
    skipped = [row["name"] for row in rows if row["feature_status"] != "computed"]
    mismatched = [row["name"] for row in rows if row["lexicon_matches_site_language"] is False]
    return {
        "protocol": "research19-v1",
        "sites": len(rows),
        "features_computed": sum(row["feature_status"] == "computed" for row in rows),
        "features_skipped": skipped,
        "english_lexicon_on_other_language_metadata": mismatched,
        "rows": rows,
        "interpretation": "IGR 与头尾标签相关，不是修改该特征就会降低命中率。未计算的站点不能写成没有该特征。",
        "attack_risk_association": "incomplete",
        "attack_risk_reason": "还没有冻结的真实猜测流，不能把特征和 cracked@B 连起来。",
        "plaintext_retained": False,
    }


def main() -> int:
    report = summarize_features()
    output = ROOT / "reports" / "research19" / "feature_stability.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"computed={report['features_computed']} skipped={report['features_skipped']} lexicon_mismatch={report['english_lexicon_on_other_language_metadata']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
