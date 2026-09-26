"""Rebuild the small research19 report from current inputs.

``report`` only reads JSON already in the chosen root and fails if a required
file is missing. ``full`` also reruns the small generators. PassGPT and PassLLM
stay outside both flows:

    python -m experiments.research19_neural_eval
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _commands(flow: str) -> tuple[list[str], ...]:
    if flow == "report":
        modules = (
            "experiments.distribution_diagnostics",
            "experiments.research19_attack_matrix",
            "experiments.research19_charts",
        )
    elif flow == "full":
        modules = (
            "experiments.research19_preprocess_identity",
            "experiments.distribution_diagnostics",
            "experiments.research19_splits",
            "experiments.research19_smoke",
            "experiments.research19_frequency_matrix",
            "experiments.research19_transfer",
            "experiments.research19_quota",
            "experiments.research19_stability",
            "experiments.research19_attack_matrix",
            "experiments.research19_charts",
        )
    else:
        raise ValueError(flow)
    return tuple([sys.executable, "-m", module] for module in modules)


def _report(root: Path) -> int:
    from experiments.distribution_diagnostics import main as diagnostics_main
    from experiments.research19_attack_matrix import main as matrix_main
    from experiments.research19_charts import main as charts_main

    for step in (diagnostics_main, matrix_main, charts_main):
        code = step(root)
        if code:
            return code
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--flow", choices=("report", "full"), default="report")
    args = parser.parse_args(argv)
    if args.flow == "report":
        return _report(args.root)
    if args.root.resolve() != ROOT.resolve():
        raise SystemExit("full 流程只使用项目内数据和模型，不接受其它 root")
    for command in _commands("full"):
        completed = subprocess.run(command, cwd=ROOT, check=False)
        if completed.returncode != 0:
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
