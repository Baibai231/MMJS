"""Reproduce the paper Zipf fit, curvature cut, IGR, and suggestion baseline.

The report contains parameters, masses, feature scores, and suggestions for
caller-supplied synthetic strings. It does not write corpus passwords.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.counted_corpus import PAPER_HEAD_MASS, PAPER_ROCKYOU_TOTAL, audit_counted_frequencies
from core.htpg_features import HTPGFeatureExtractor
from core.htpg_fit import fit_pdf_zipf
from core.htpg_igr import IGRAccumulator
from policy.htpg_generator import password_digest, suggest_for_password


def _public_audit(audit: dict) -> dict:
    published = {key: value for key, value in audit.items() if key != "frequencies"}
    published["frequency_vector_retained_in_report"] = False
    published["plaintext_retained"] = False
    return published


def fit_report(path: str | Path) -> dict:
    audit = audit_counted_frequencies(path)
    frequencies = list(audit["frequencies"])
    if audit["order_increases"]:
        frequencies.sort(reverse=True)
    fit = fit_pdf_zipf(frequencies)
    total_gap = audit["frequency_total"] - PAPER_ROCKYOU_TOTAL
    empty_explains = audit["empty_password_mass"] == total_gap
    non_utf8_explains = audit["non_utf8_mass"] == total_gap
    return {
        "audit": _public_audit(audit),
        "fit": fit,
        "paper_comparison": {
            "local_total": audit["frequency_total"],
            "paper_total": PAPER_ROCKYOU_TOTAL,
            "unexplained_total_gap": None if empty_explains or non_utf8_explains else total_gap,
            "empty_password_mass_explains_gap": empty_explains,
            "non_utf8_mass_explains_gap": non_utf8_explains,
            "paper_head_mass": PAPER_HEAD_MASS,
            "local_file_order_prefix_1171": audit["paper_prefix_mass"],
            "prefix_1171_matches_paper_table": audit["paper_prefix_mass_matches"],
            "curvature_cutoff_rank": fit["cutoff_rank"],
            "curvature_cutoff_was_hardcoded": False,
            "rank_order": "sorted_descending" if audit["order_increases"] else "file_order",
        },
    }


def feature_report(
    path: str | Path, *, fit: dict, lexicon: str | Path | None,
    examples: list[str], file_order_is_rank: bool,
) -> dict:
    if not file_order_is_rank:
        raise ValueError("文件频次不是降序，不能在不保存口令的情况下按曲率名次贴头尾标签")
    cutoff = int(fit["cutoff_rank"])
    extractor = HTPGFeatureExtractor.from_profile(lexicon) if lexicon else HTPGFeatureExtractor(
        ["joy", "happy"], ["smith", "li"],
    )
    accumulator = IGRAccumulator()
    head_digests: set[str] = set()
    non_utf8_head = 0
    empty_skipped = 0
    for rank, (_line_number, frequency, password) in enumerate(_rows(path), start=1):
        is_head = rank <= cutoff
        if not password.strip():
            empty_skipped += 1
            continue
        try:
            text = password.decode("utf-8")
        except UnicodeDecodeError:
            if is_head:
                non_utf8_head += 1
            continue
        if is_head:
            head_digests.add(password_digest(password))
        accumulator.add(extractor.extract(text).to_dict(), is_head=is_head, frequency=frequency)
    report = accumulator.scores()
    suggestions = [
        suggest_for_password(example, head_digests=head_digests, igr_report=report, extractor=extractor)
        for example in examples
    ]
    return {
        "lexicon": extractor.metadata(),
        "non_utf8_head_rows_excluded_from_digest": non_utf8_head,
        "empty_password_rows_excluded_from_features": empty_skipped,
        "head_digest_count": len(head_digests),
        "igr": report,
        "synthetic_suggestion_examples": suggestions,
        "plaintext_retained": False,
    }


def _rows(path: str | Path):
    from core.counted_corpus import iter_counted_rows
    return iter_counted_rows(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="复现 HTPG 的 Zipf 曲率、IGR 与逐口令建议")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--with-features", action="store_true")
    parser.add_argument("--lexicon", type=Path, default=Path("resources/htpg_reference_v1.json"))
    parser.add_argument("--example", action="append", default=["cedar2026", "river-otter-lotus"])
    args = parser.parse_args()
    payload = fit_report(args.input)
    if args.with_features:
        payload["features"] = feature_report(
            args.input, fit=payload["fit"], lexicon=args.lexicon, examples=args.example,
            file_order_is_rank=payload["audit"]["order_increases"] == 0,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    comparison = payload["paper_comparison"]
    print(
        f"C={payload['fit']['C']:.6g} alpha={payload['fit']['alpha']:.6f} "
        f"x0={payload['fit']['curvature_x0']:.3f} cutoff={payload['fit']['cutoff_rank']} "
        f"prefix1171={comparison['local_file_order_prefix_1171']} "
        f"matches_paper_head={comparison['prefix_1171_matches_paper_table']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
