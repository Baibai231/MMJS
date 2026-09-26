"""Recount the small MAYA sites with the current pickle preprocessor.

The other sites are listed as not rescanned. This script does not infer that
those files are free of internal control characters.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.htpg_features import HTPGFeatureExtractor
from core.htpg_fit import fit_pdf_zipf
from core.occurrence_frequency import PREPROCESS_VERSION, load_occurrence_counter
from core.site_distribution import analyze_occurrence_file
from data.maya_catalog import dataset_names
from experiments.research19_smoke import BUDGETS, frequency_attack, occurrence_split


ROOT = Path(__file__).resolve().parents[1]
RESCANNED = ("myspace", "phpbb", "hotmail", "faithwriters", "hak5", "singles", "twitter")
LEXICON = ROOT / "resources" / "htpg_reference_v1.json"


def _pickle(name: str) -> Path:
    return next((ROOT / f"local_datasets/maya/{name}/extracted").rglob("*.pickle"))


def _public_counts(meta: dict) -> dict:
    return {
        "preprocess_version": meta.get("preprocess_version", PREPROCESS_VERSION),
        "source_sha256": meta["source_sha256"],
        "rows_read": meta["rows_read"],
        "rows_skipped": meta["rows_skipped"],
        "occurrence_total": meta["occurrence_total"],
        "unique_types": meta["unique_types"],
        "trailing_lf_removed": meta.get("trailing_lf_removed"),
        "trailing_crlf_removed": meta.get("trailing_crlf_removed"),
        "rows_without_record_terminator": meta.get("rows_without_record_terminator"),
        "rows_excluded_internal_controls": meta.get("rows_excluded_internal_controls"),
        "rows_excluded_empty": meta.get("rows_excluded_empty"),
        "rows_excluded_non_string": meta.get("rows_excluded_non_string"),
        "plaintext_retained": False,
    }


def main() -> int:
    sites = {}
    twitter_detail = None
    for name in RESCANNED:
        payload = _pickle(name)
        meta, counts = load_occurrence_counter(payload)
        card = _public_counts(meta)
        ordered = sorted(counts.values(), reverse=True)
        try:
            fitted = fit_pdf_zipf(ordered)
            card["alpha_rounded_3dp"] = fitted["alpha_rounded_3dp"]
            card["cutoff_rank"] = fitted["cutoff_rank"]
            card["fit_version"] = fitted["fit_version"]
        except ValueError as exc:
            card["fit_status"] = "not_applicable"
            card["fit_reason"] = str(exc)
        if name == "twitter":
            split = occurrence_split(counts, seed=19)
            scored = frequency_attack(split["train"], split["test"], BUDGETS)
            card["split_rows"] = {part: len(rows) for part, rows in split.items()}
            card["frequency_attack"] = {
                "points_axis": scored["points_axis"],
                "points": scored["points"],
                "preprocess_version": PREPROCESS_VERSION,
            }
            features = analyze_occurrence_file(
                payload, HTPGFeatureExtractor.from_profile(LEXICON), max_feature_types=100_000,
            )
            card["feature_status"] = features["feature_status"]
            card["lexicon_language"] = "en"
            card["site_language_metadata"] = "ru"
            card["lexicon_matches_site_language"] = False
            card["causal_modification_effect"] = None
            twitter_detail = {
                "order_unique": (features.get("features") or {}).get("order_unique"),
            }
        counts.clear()
        sites[name] = card
        print(name, card["occurrence_total"], card["unique_types"], card.get("rows_excluded_internal_controls"), flush=True)
    report = {
        "preprocess_version": PREPROCESS_VERSION,
        "sites": sites,
        "twitter_feature_order_unique": None if twitter_detail is None else twitter_detail["order_unique"],
        "sites_not_rescanned": [name for name in dataset_names() if name not in sites],
        "sites_not_rescanned_reason": "本轮没有读取这些文件，不能推断它们没有内部控制字符。",
        "upstream_line_ending_definition": "not_independently_audited",
        "claim_supported": False,
        "plaintext_retained": False,
    }
    output = ROOT / "reports" / "research19" / "preprocess_identity.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("not_rescanned", len(report["sites_not_rescanned"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
