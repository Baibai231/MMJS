"""Cost-capped choice among HTPG feature programs.

The hypothesis is that a validation-set attack risk, subject to a modification
cap, may beat IGR order. This file does not establish that hypothesis. Test
labels are not an input to the chooser.
"""
from __future__ import annotations

from itertools import combinations
from typing import Sequence

from policy.htpg_generator import PAPER_POLICIES


METHOD_ID = "validation-risk-under-modification-cap-v1"
COST_CAPS = (0.05, 0.10, 0.20)


def feature_programs(limit: int = 2) -> list[tuple[str, ...]]:
    """Empty plan, single features, and ordered pairs. At most ``limit`` features."""
    names = tuple(PAPER_POLICIES)
    programs: list[tuple[str, ...]] = [()]
    programs.extend((name,) for name in names)
    if limit >= 2:
        programs.extend(tuple(pair) for pair in combinations(names, 2))
    return programs


def select_under_cost(rows: Sequence[dict], cost_cap: float) -> dict:
    """Minimize validation risk among programs whose cost is within the cap.

    ``rows`` contain ``features``, ``validation_risk`` and ``modification_rate``.
    They must already have been scored without test labels.
    """
    if cost_cap < 0:
        raise ValueError("成本上限不能为负")
    feasible = [row for row in rows if float(row["modification_rate"]) <= cost_cap]
    if not feasible:
        return {
            "status": "unreachable",
            "features": [],
            "validation_risk": None,
            "modification_rate": None,
            "cost_cap": cost_cap,
            "hypothesis_established": False,
        }
    chosen = min(feasible, key=lambda row: (float(row["validation_risk"]), float(row["modification_rate"]), row["features"]))
    return {
        "status": "selected",
        "features": list(chosen["features"]),
        "validation_risk": chosen["validation_risk"],
        "modification_rate": chosen["modification_rate"],
        "cost_cap": cost_cap,
        "rejected": [
            {"features": list(row["features"]), "modification_rate": row["modification_rate"], "validation_risk": row["validation_risk"]}
            for row in rows
            if row is not chosen
        ],
        "hypothesis_established": False,
        "method": METHOD_ID,
    }
