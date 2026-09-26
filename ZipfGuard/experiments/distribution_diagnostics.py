"""19-site distribution summary from the aggregate audit. No passwords are read."""
from __future__ import annotations

import json
from pathlib import Path

from experiments.split_stability import scale_factor, scaled_curvature_peak
from core.htpg_fit import curvature_maximum


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "reports" / "research19" / "data_audit.json"


def summarize_audit(path: Path = AUDIT) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    sites = []
    for row in payload["sites"]:
        occurrence = row.get("occurrence_total") or 0
        unique = row.get("unique_types") or 0
        sites.append({
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
        })
    applicable = [row for row in sites if row["occurrence_weighted_metric_available"]]
    return {
        "protocol": "research19-v1",
        "sites": len(sites),
        "paper_threshold_sites": sum(bool(row["paper_threshold"]) for row in sites),
        "feature_computed_sites": sum(row["feature_status"] == "computed" for row in sites),
        "account_risk_not_claimed_sites": [row["name"] for row in sites if row["account_risk_status"] != "verified_accounts"],
        "macro_mean_repeat_ratio": (
            None if not applicable else sum(row["repeat_ratio"] for row in applicable) / len(applicable)
        ),
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


def main() -> int:
    report = summarize_audit()
    output = ROOT / "reports" / "research19" / "distribution_diagnostics.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"sites={report['sites']} paper_threshold={report['paper_threshold_sites']} features={report['feature_computed_sites']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
