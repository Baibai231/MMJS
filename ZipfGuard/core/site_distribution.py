"""Zipf fit and HTPG feature scores for one local occurrence file.

The counter's keys exist only while ranks and features are computed. The
returned report keeps fit parameters and feature scores. It does not keep the
frequency vector or any password.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

from core.htpg_features import HTPGFeatureExtractor
from core.htpg_fit import fit_pdf_zipf
from core.htpg_igr import IGRAccumulator
from core.occurrence_frequency import load_occurrence_counter


_SAFE_MODE = re.compile(r"^[A-Za-z0-9+._-]{1,24}$")


def analyze_occurrence_file(
    path: str | Path,
    extractor: HTPGFeatureExtractor,
    *,
    min_frequency_exclusive: int = 3,
    max_feature_types: int = 400_000,
) -> dict:
    meta, counts = load_occurrence_counter(path)
    frequencies = sorted((int(count) for count in counts.values()), reverse=True)
    fit = fit_pdf_zipf(frequencies, min_frequency_exclusive=min_frequency_exclusive)
    feature_status = "computed"
    features = None
    if len(counts) > max_feature_types:
        feature_status = "skipped_unique_cap"
        counts.clear()
    else:
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        counts.clear()
        cutoff = int(fit["cutoff_rank"])
        accumulator = IGRAccumulator()
        for rank, (text, frequency) in enumerate(ranked, start=1):
            accumulator.add(
                extractor.extract(text).to_dict(),
                is_head=rank <= cutoff,
                frequency=frequency,
            )
        del ranked
        features = _public_features(accumulator.scores())
    meta["plaintext_retained"] = False
    meta["frequency_vector_retained_in_report"] = False
    return {
        "audit": meta,
        "fit": fit,
        "protocol": f"frequency_gt_{min_frequency_exclusive}",
        "rank_tie_break": "higher count, then UTF-8 lexicographic text; text is not stored",
        "feature_status": feature_status,
        "max_feature_types": max_feature_types,
        "features": features,
        "lexicon": extractor.metadata(),
        "plaintext_retained": False,
    }


def _public_features(report: Mapping[str, object]) -> dict:
    rows = []
    for row in report["features"]:  # type: ignore[index]
        published = {
            "feature": row["feature"],
            "rank_unique": row["rank_unique"],
            "rank_frequency": row["rank_frequency"],
            "igr_unique": row["igr_unique"],
            "igr_frequency": row["igr_frequency"],
            "status_unique": row["status_unique"],
            "status_frequency": row["status_frequency"],
            "missing_unique": row["missing_unique"],
            "missing_frequency": row["missing_frequency"],
        }
        for side in ("head", "tail"):
            summary = row[f"{side}_summary_unique"]
            if not isinstance(summary, dict):
                continue
            if "mean" in summary:
                published[f"{side}_mean_unique"] = summary["mean"]
            mode = summary.get("mode")
            if isinstance(mode, str) and _SAFE_MODE.fullmatch(mode):
                published[f"{side}_mode_unique"] = mode
            elif mode is not None and "mean" not in summary:
                published[f"{side}_mode_withheld"] = True
        rows.append(published)
    return {
        "igr_version": report["igr_version"],
        "primary_weighting": report["primary_weighting"],
        "rows": report["rows"],
        "head_rows": report["head_rows"],
        "tail_rows": report["tail_rows"],
        "frequency_total": report["frequency_total"],
        "head_frequency": report["head_frequency"],
        "order_unique": [row["feature"] for row in rows],
        "features": rows,
    }
