"""Small-budget entry points.

This does not launch neural evaluation, the 19-site fit, or the confirmation
experiment. PassLLM and PassGPT stay in ``experiments.research19_neural_eval``.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMMANDS = (
    [sys.executable, "-m", "experiments.distribution_diagnostics"],
    [sys.executable, "-m", "experiments.research19_splits"],
    [sys.executable, "-m", "experiments.research19_smoke"],
    [sys.executable, "-m", "experiments.research19_frequency_matrix"],
    [sys.executable, "-m", "experiments.research19_transfer"],
    [sys.executable, "-m", "experiments.research19_quota"],
    [sys.executable, "-m", "experiments.research19_stability"],
    [sys.executable, "-m", "experiments.research19_charts"],
)


def main() -> int:
    for command in COMMANDS:
        completed = subprocess.run(command, cwd=ROOT, check=False)
        if completed.returncode != 0:
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
