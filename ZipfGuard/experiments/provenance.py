"""Content identities and environment evidence; no plaintext data in manifests."""
import hashlib
import importlib.metadata
import platform
import subprocess
import sys
from pathlib import Path

from experiments.config import fingerprint

ROOT = Path(__file__).resolve().parents[1]


def git_output(*args):
    try:
        return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def manifest(config, dataset, attackers):
    sources = {str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
               for directory in ("core", "ai", "policy", "experiments", "web", "tools")
               for p in sorted((ROOT / directory).glob("*.py"))}
    sources["run_demo.py"] = hashlib.sha256((ROOT / "run_demo.py").read_bytes()).hexdigest()
    return {
        "config": config, "config_sha256": fingerprint(config),
        "dataset_version": "synthetic-grammar-v1" if "records" in dataset else "aggregate-counts-v1",
        "dataset_sha256": fingerprint(dataset),
        "candidate_space_version": "public-grammar-v1", "candidate_space_sha256": fingerprint(config["synthetic"]),
        "attacker_versions": {a.attacker_id: a.version for a in attackers},
        "git_commit": git_output("rev-parse", "HEAD"), "git_dirty": bool(git_output("status", "--porcelain")),
        "source_sha256": fingerprint(sources), "source_files": sources,
        "python": sys.version, "python_executable": sys.executable, "platform": platform.platform(),
        "dependencies": dict(sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions() if d.metadata["Name"])),
    }


def robustness_manifest(*, budget: int = 40, seeds: list[int] | None = None, sizes: list[int] | None = None) -> dict:
    """Hashes for the open-budget comparison. No generated passwords are included."""
    relative_paths = (
        "experiments/suggestion_compare.py",
        "experiments/robustness_protocol.py",
        "experiments/response_sensitivity.py",
        "experiments/htpg_iteration.py",
        "core/markov_substitute.py",
        "core/metrics.py",
        "core/htpg_fit.py",
        "core/htpg_igr.py",
        "core/htpg_features.py",
        "policy/htpg_generator.py",
        "core/attackers.py",
        "experiments/evaluation_validity.py",
        "experiments/provenance.py",
        "experiments/external_maya_validation.py",
        "experiments/research19_manifest.py",
        "resources/htpg_reference_v1.json",
    )
    files = {}
    for relative in relative_paths:
        files[relative] = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    joined = "".join(files[relative] for relative in relative_paths)
    return {
        "protocol": "robustness-v4",
        "budget": int(budget),
        "seeds": seeds,
        "sizes": sizes,
        "budget_is_not_login_attempts": True,
        "source_sha256": hashlib.sha256(joined.encode("utf-8")).hexdigest(),
        "source_files": files,
        "git_commit": git_output("rev-parse", "HEAD"),
        "git_dirty": bool(git_output("status", "--porcelain")),
        "python": sys.version,
        "platform": platform.platform(),
        "markov_substitute": "fixed-order-markov-not-omen",
        "passllm": "not_participating",
        "plaintext_retained": False,
    }
