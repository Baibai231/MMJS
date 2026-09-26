"""OLS Zipf fits for sites that fit in memory, plus one three-model check.

Sites with more than 200,000 distinct strings are left incomplete. The
three-model MLE runs only when the support is at most 4,000, because the
existing fitter is not a full-corpus method above its category cap.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.distributions import fit_model, models_within_bic
from core.htpg_fit import fit_pdf_zipf
from core.occurrence_frequency import load_occurrence_counter
from data.maya_catalog import dataset_names
from experiments.split_stability import mass_cutoff


ROOT = Path(__file__).resolve().parents[1]
MAX_UNIQUE = 200_000
MLE_UNIQUE = 4_000


def main() -> int:
    rows = []
    for name in dataset_names():
        payload = next((ROOT / f"local_datasets/maya/{name}/extracted").rglob("*.pickle"))
        _meta, counts = load_occurrence_counter(payload)
        unique = len(counts)
        if unique > MAX_UNIQUE:
            counts.clear()
            rows.append({
                "site": name,
                "status": "incomplete",
                "reason": f"独特字符串 {unique} 超过 {MAX_UNIQUE}",
            })
            continue
        ordered = sorted(counts.values(), reverse=True)
        counts.clear()
        row = {"site": name, "unique_types": unique, "status": "ols_completed", "full_corpus_mle": False}
        try:
            fitted = fit_pdf_zipf(ordered)
            row["ols"] = {
                "alpha_rounded_3dp": fitted["alpha_rounded_3dp"],
                "cutoff_rank": fitted["cutoff_rank"],
                "log_r_squared": fitted["log_r_squared"],
                "fit_types": fitted["fit_types"],
                "min_frequency_exclusive": fitted["min_frequency_exclusive"],
            }
            row["mass_cutoff_0_5"] = mass_cutoff(ordered, 0.5)
        except ValueError as exc:
            row["status"] = "ols_not_applicable"
            row["reason"] = str(exc)
        if unique <= MLE_UNIQUE and row["status"] == "ols_completed":
            models = []
            for model_name in ("zipf", "cdf_zipf", "stretched_exponential"):
                fitted_model = fit_model(ordered, model_name)
                fitted_model.pop("pmf", None)
                fitted_model.pop("cdf", None)
                models.append(fitted_model)
            ordered_models = sorted(models, key=lambda item: item["bic"])
            row["three_model_mle"] = {
                "support": unique,
                "category_cap": 100_000,
                "within_cap": True,
                "full_support_of_this_site": True,
                "independent_test": False,
                "models_within_bic_10": [item["id"] for item in models_within_bic(ordered_models)],
                "models": [
                    {
                        "id": item["id"],
                        "bic": item["bic"],
                        "mean_absolute_cdf_error": item["mean_absolute_cdf_error"],
                    }
                    for item in models
                ],
            }
            row["status"] = "ols_and_three_model"
        elif row["status"] == "ols_completed":
            row["three_model_mle"] = {
                "status": "incomplete",
                "reason": f"独特字符串 {unique} 超过这次的 {MLE_UNIQUE} 上限，避免把截断拟合写成全量比较",
            }
        rows.append(row)
        print(name, row["status"], flush=True)
    report = {
        "protocol": "research19-v1",
        "independent_test_on_19_sites": False,
        "rows": rows,
        "claim_supported": False,
        "plaintext_retained": False,
    }
    output = ROOT / "reports" / "research19" / "fit_small.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
