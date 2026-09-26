"""Shared CLI: open Min_auto by default, with explicit legacy and aggregate paths."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai.pcfg_adapter import PCFGConfig
from core.counted_corpus import CorpusFormatError, looks_like_counted_corpus
from core.data import load_count_json, write_count_json
from core.rockyou import aggregate_rockyou, aggregate_rockyou_withcount
from experiments.config import load_config
from experiments.open_config import load_open_config, validate_open_config
from experiments.open_pipeline import run_open_pipeline
from experiments.pipeline import render_markdown, run_pipeline, write_json, write_report
from web.presentation import report_html


def main() -> int:
    parser = argparse.ArgumentParser(description="ZipfGuard real-corpus open Min_auto experiment")
    parser.add_argument("--preset", choices=["open_quick", "open_full", "quick", "full"], default="open_quick")
    parser.add_argument("--config", type=Path, help="完整实验配置 JSON")
    parser.add_argument("--corpus", type=Path, help="开放主实验的本地真实口令语料")
    parser.add_argument("--format", choices=["password_with_count", "raw_occurrences", "unique_dictionary"])
    parser.add_argument("--encoding")
    parser.add_argument("--sample-size", type=int)
    parser.add_argument("--risk-budget", type=int)
    parser.add_argument("--response", choices=["R0", "R1", "R2"])
    parser.add_argument("--all-responses", action="store_true")
    parser.add_argument("--input", type=Path, help="聚合频次 JSON；只运行分布分析")
    parser.add_argument("--rockyou", type=Path, help="历史聚合入口；只保留 top-k 计数")
    parser.add_argument("--source-semantics", choices=["unknown", "frequency", "unique_dictionary"], default="unknown")
    parser.add_argument("--max-lines", type=int, default=None)
    parser.add_argument("--top-k", type=int, default=2_000)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--budgets", help="逗号分隔的每模型攻击预算")
    parser.add_argument("--bootstrap", type=int)
    parser.add_argument("--synthetic-size", type=int)
    parser.add_argument("--synthetic-exponent", type=float)
    parser.add_argument("--pcfg", choices=["off", "optional", "required"], nargs="?", const="optional")
    parser.add_argument("--pcfg-limit", type=int)
    parser.add_argument("--pcfg-timeout", type=int)
    parser.add_argument("--json-out", type=Path, default=Path("reports/open_demo.json"))
    parser.add_argument("--report-out", type=Path, default=Path("reports/open_demo.md"))
    parser.add_argument("--html-out", type=Path, default=Path("reports/open_demo.html"))
    args = parser.parse_args()

    selected_sources = sum(value is not None for value in (args.corpus, args.input, args.rockyou))
    if selected_sources > 1:
        parser.error("--corpus、--input 与 --rockyou 只能选择一个")

    if args.config:
        cfg = json.loads(args.config.read_text(encoding="utf-8"))
    elif args.preset.startswith("open_"):
        cfg = load_open_config(args.preset)
    else:
        cfg = load_config(preset=args.preset)

    if args.seed is not None:
        cfg["seed"] = args.seed
    if args.bootstrap is not None:
        cfg["bootstrap_repetitions"] = args.bootstrap
    if args.budgets:
        cfg["budgets"] = [int(value) for value in args.budgets.split(",")]

    if args.input or args.rockyou:
        legacy = cfg if cfg.get("schema_version") == "zipfguard-experiment-v1" else load_config()
        legacy["seed"] = cfg["seed"]
        legacy["bootstrap_repetitions"] = cfg["bootstrap_repetitions"]
        legacy["attackers"].pop("pcfg", None)
        if not legacy["attackers"]:
            legacy["attackers"] = {"frequency": "required"}
        if args.input:
            payload = load_count_json(args.input)
        else:
            try:
                counted = looks_like_counted_corpus(args.rockyou)
            except CorpusFormatError as exc:
                parser.error(str(exc))
            if counted:
                payload = aggregate_rockyou_withcount(args.rockyou, max_lines=args.max_lines, top_k=args.top_k)
            else:
                payload = aggregate_rockyou(
                    args.rockyou,
                    max_lines=1_000_000 if args.max_lines is None else args.max_lines,
                    top_k=args.top_k,
                    source_semantics=args.source_semantics,
                )
            write_count_json(payload, Path("demo_data/rockyou_counts.json"))
        result = run_pipeline(payload, config=legacy)
    elif cfg.get("schema_version") == "zipfguard-open-v2":
        if args.corpus:
            cfg["data"]["path"] = str(args.corpus.resolve())
        if args.format:
            cfg["data"]["format"] = args.format
        if args.encoding:
            cfg["data"]["encoding"] = args.encoding
        if args.sample_size is not None:
            cfg["data"]["sample_size"] = args.sample_size
        if args.risk_budget is not None:
            cfg["search"]["risk_budget"] = args.risk_budget
        if args.response:
            cfg["response"].update(primary=args.response, scenarios=[args.response])
        if args.all_responses:
            cfg["response"]["scenarios"] = ["R0", "R1", "R2"]
        if args.pcfg == "off":
            cfg["attackers"].pop("pcfg", None)
        elif args.pcfg:
            cfg["attackers"]["pcfg"] = args.pcfg
        if args.pcfg_limit is not None:
            cfg["pcfg"]["raw_limit"] = args.pcfg_limit
        if args.pcfg_timeout is not None:
            cfg["pcfg"]["timeout_seconds"] = args.pcfg_timeout
        result = run_open_pipeline(validate_open_config(cfg), progress=lambda message: print(message, flush=True))
    else:
        pcfg_enabled = args.pcfg not in (None, "off")
        pcfg_config = PCFGConfig.workspace_default(
            generation_limit=args.pcfg_limit if args.pcfg_limit is not None else cfg["pcfg"]["generation_limit"],
            timeout_seconds=args.pcfg_timeout if args.pcfg_timeout is not None else cfg["pcfg"]["timeout_seconds"],
        )
        cfg["pcfg"].update(
            generation_limit=pcfg_config.generation_limit,
            timeout_seconds=pcfg_config.timeout_seconds,
        )
        result = run_pipeline(
            config=cfg,
            seed=args.seed,
            bootstrap_repetitions=args.bootstrap,
            synthetic_size=args.synthetic_size,
            synthetic_exponent=args.synthetic_exponent,
            include_pcfg=pcfg_enabled,
            pcfg_config=pcfg_config,
        )

    write_json(result, args.json_out)
    write_report(result, args.report_out)
    args.html_out.parent.mkdir(parents=True, exist_ok=True)
    args.html_out.write_text(report_html(result), encoding="utf-8")
    print(render_markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
