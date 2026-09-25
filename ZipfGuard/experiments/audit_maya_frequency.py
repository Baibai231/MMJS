"""Fit the paper Zipf curve on one local MAYA corpus.

The report stores the dataset card, file hash, and fit parameters. It does not
store passwords. The method's validation weights are not refit on this file.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.htpg_fit import fit_pdf_zipf
from core.occurrence_frequency import frequencies_from_occurrences
from data.maya_catalog import dataset_card


def audit_file(path: str | Path, *, dataset_name: str | None, min_frequency_exclusive: int) -> dict:
    observed = frequencies_from_occurrences(path)
    frequencies = observed.pop("frequencies")
    fit = fit_pdf_zipf(frequencies, min_frequency_exclusive=min_frequency_exclusive)
    prefix = frequencies[:1171]
    card = dataset_card(dataset_name) if dataset_name else None
    return {
        "dataset": card,
        "audit": observed,
        "fit": fit,
        "prefix_1171_mass": int(sum(prefix)) if len(frequencies) >= 1171 else None,
        "prefix_1171_available": len(frequencies) >= 1171,
        "method_weights_refit": False,
        "plaintext_retained": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="对本地 MAYA 语料只做频次拟合")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--dataset", help="maya_catalog.py 中的名字")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-frequency", type=int, default=3)
    args = parser.parse_args()
    payload = audit_file(args.input, dataset_name=args.dataset, min_frequency_exclusive=args.min_frequency)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fit = payload["fit"]
    audit = payload["audit"]
    print(
        f"types={audit['unique_types']} total={audit['occurrence_total']} "
        f"alpha={fit['alpha']:.6f} x0={fit['curvature_x0']:.3f} cutoff={fit['cutoff_rank']} "
        f"head_mass={fit['head_mass']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
