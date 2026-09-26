"""19-site distribution summary from the aggregate audit. No passwords are read."""
from __future__ import annotations

import json
from pathlib import Path

from experiments.split_stability import scale_factor, scaled_curvature_peak
from core.htpg_fit import curvature_maximum


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "reports" / "research19" / "data_audit.json"
IDENTITY = ROOT / "reports" / "research19" / "preprocess_identity.json"


def _apply_current_counts(row: dict, current: dict | None) -> None:
    row["count_source"] = "historical_audit_not_rescanned"
    row["counts_match_current_preprocess"] = None
    if current is None:
        return
    row["preprocess_version"] = current.get("preprocess_version")
    row["source_sha256"] = current.get("source_sha256")
    row["rows_read"] = current.get("rows_read")
    row["rows_excluded_internal_controls"] = current.get("rows_excluded_internal_controls")
    same = (
        row["occurrence_total"] == current.get("occurrence_total")
        and row["unique_types"] == current.get("unique_types")
    )
    row["counts_match_current_preprocess"] = same
    row["count_source"] = "current_preprocess" if same else "current_preprocess_replaces_historical_audit"
    if not same:
        row["historical_occurrence_total"] = row["occurrence_total"]
        row["historical_unique_types"] = row["unique_types"]
        row["occurrence_total"] = current.get("occurrence_total")
        row["unique_types"] = current.get("unique_types")
        occurrence = row["occurrence_total"] or 0
        unique = row["unique_types"] or 0
        row["repeat_ratio"] = None if occurrence == 0 else 1 - unique / occurrence
        row["historical_alpha_rounded_3dp"] = row.get("alpha_rounded_3dp")
        row["historical_cutoff_rank"] = row.get("cutoff_rank")
        row["alpha_source"] = "historical_audit_different_preprocess"
        if current.get("alpha_rounded_3dp") is not None:
            row["alpha_rounded_3dp"] = current["alpha_rounded_3dp"]
            row["cutoff_rank"] = current.get("cutoff_rank")
            row["alpha_source"] = "current_preprocess"


def summarize_audit(path: Path = AUDIT, identity_path: Path = IDENTITY) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    identity = json.loads(identity_path.read_text(encoding="utf-8")) if identity_path.is_file() else None
    current_sites = (identity or {}).get("sites") or {}
    sites = []
    for row in payload["sites"]:
        occurrence = row.get("occurrence_total") or 0
        unique = row.get("unique_types") or 0
        item = {
            "name": row["name"],
            "occurrence_total": occurrence,
            "unique_types": unique,
            "repeat_ratio": None if occurrence == 0 else 1 - unique / occurrence,
            "paper_threshold": row.get("paper_threshold"),
            "feature_status": row.get("feature_status"),
            "alpha_rounded_3dp": row.get("alpha_rounded_3dp"),
            "cutoff_rank": row.get("cutoff_rank"),
            "frequency_use": row.get("frequency_use"),
            "occurrence_weighted_metric_available": row.get("frequency_use") == "occurrence_not_verified_accounts",
            "account_risk_applicable": False,
            "account_risk_status": "unknown",
        }
        _apply_current_counts(item, current_sites.get(row["name"]))
        sites.append(item)
    applicable = [row for row in sites if row["occurrence_weighted_metric_available"]]
    if identity is None:
        identity_status = "missing"
        not_rescanned = None
    elif not isinstance(identity.get("sites_not_rescanned"), list):
        identity_status = "incomplete"
        not_rescanned = None
    else:
        identity_status = "present"
        not_rescanned = identity["sites_not_rescanned"]
    current_rows = [row for row in applicable if str(row.get("count_source", "")).startswith("current_preprocess")]
    historical_rows = [row for row in applicable if row.get("count_source") == "historical_audit_not_rescanned"]

    def _mean(rows: list[dict]) -> float | None:
        values = [row["repeat_ratio"] for row in rows if row.get("repeat_ratio") is not None]
        return None if not values else sum(values) / len(values)

    return {
        "protocol": "research19-v1",
        "historical_audit_is_not_the_attack_preprocess": True,
        "identity_status": identity_status,
        "preprocess_identity": None if identity is None else identity.get("preprocess_version"),
        "sites_not_rescanned": not_rescanned,
        "repeat_ratio_mean_current_preprocess": _mean(current_rows),
        "repeat_ratio_mean_current_count": len(current_rows),
        "repeat_ratio_mean_historical_unread": _mean(historical_rows),
        "repeat_ratio_mean_historical_count": len(historical_rows),
        "macro_mean_repeat_ratio": None,
        "macro_mean_repeat_ratio_reason": "当前预处理与历史未重读站点不合成一个均值",
        "sites": len(sites),
        "paper_threshold_sites": sum(bool(row["paper_threshold"]) for row in sites),
        "feature_computed_sites": sum(row["feature_status"] == "computed" for row in sites),
        "account_risk_not_claimed_sites": [row["name"] for row in sites if row["account_risk_status"] != "verified_accounts"],
        "rows": sites,
        "scale_check": {
            "statement": "Holding alpha fixed, multiplying C by k multiplies x0 by k**(1/(alpha+1)).",
            "example_alpha": 1.0,
            "example_multiplier": 10.0,
            "factor": scale_factor(1.0, 10.0),
            "peak_ratio": scaled_curvature_peak(1000.0, 1.0, 10.0) / curvature_maximum(1000.0, 1.0),
        },
        "plaintext_retained": False,
        "not_an_attack_result": True,
    }


def main(root: Path | None = None) -> int:
    root = Path(root) if root is not None else ROOT
    identity = root / "reports" / "research19" / "preprocess_identity.json"
    audit = root / "reports" / "research19" / "data_audit.json"
    if not identity.is_file():
        raise SystemExit(f"缺少 {identity}。不能把未重读站数写成 0。")
    report = summarize_audit(audit, identity)
    if report["identity_status"] != "present" or report["sites_not_rescanned"] is None:
        raise SystemExit("身份文件没有给出未重读站点列表，拒绝写出当前分布摘要。")
    output = root / "reports" / "research19" / "distribution_diagnostics.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"sites={report['sites']} paper_threshold={report['paper_threshold_sites']} features={report['feature_computed_sites']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
