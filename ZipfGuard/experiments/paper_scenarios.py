"""L1 driver for the HTPG paper scenarios. Missing sites stay incomplete.

This module does not invent cracked rates. It reads the site inventory and
refuses to relabel another corpus as 178, CSDN, or RenRen.
"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SITES = ROOT / "configs" / "paper_reproduction" / "sites.json"
FORBIDDEN_RENAMES = {
    "178": {"taobao", "hak5", "phpbb", "libero"},
    "CSDN": {"taobao", "yahoo", "000webhost"},
    "RenRen": {"myspace", "twitter", "linkedin"},
}


def load_sites(path: str | Path = DEFAULT_SITES) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def missing_sites(config: dict | None = None) -> list[str]:
    config = config if config is not None else load_sites()
    return [row["paper_name"] for row in config["sites"] if row["status"] == "missing"]


def assert_not_renamed(paper_name: str, local_name: str) -> None:
    banned = FORBIDDEN_RENAMES.get(paper_name, set())
    if local_name in banned:
        raise ValueError(f"不能把 {local_name} 改名为 {paper_name}")


def reproduction_plan(path: str | Path = DEFAULT_SITES) -> dict:
    """E1–E4 status. No password guesses are produced."""
    config = load_sites(path)
    missing = missing_sites(config)
    blocked = bool(missing) or not config["attacks"]["omen"].startswith("integrated")
    reason = "178、CSDN 或 RenRen 缺失，且作者 OMEN 未接入。不能填写图 7–10 或图 13 的命中率。"
    row = {
        "status": "incomplete",
        "reason": reason,
        "cracked_rate": None,
        "budget_completed": None,
        "paper_budget": "1e8",
    }
    return {
        "layer": "L1",
        "missing_sites": missing,
        "attacks": config["attacks"],
        "cross_site_note": config["cross_site"]["project_choice"],
        "experiments": {"E1": dict(row), "E2": dict(row), "E3": dict(row), "E4": dict(row)},
        "figures_not_reproduced": ["7", "8", "9", "10", "13"],
        "blocked": blocked,
        "plaintext_retained": False,
    }
