"""See whether exported text equals a corpus password.

The corpus is streamed. A hit is reported as a file location and a hash prefix,
never as the password itself. Strings in the project vocabulary are ignored
because words such as feature names can also occur in a leak list.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.counted_corpus import ensure_not_lfs_pointer, parse_counted_line
from core.htpg_features import FEATURE_NAMES


VOCABULARY = set(FEATURE_NAMES) | {
    "none", "ok", "frequency", "unique", "head", "tail", "modern", "paper", "budget",
    "closed", "open", "synthetic", "rockyou", "zipf", "length", "date", "keyboard",
}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def artifact_strings(root: Path) -> dict[str, str]:
    """Map password-hash to a location. The plaintext is not kept."""
    found: dict[str, str] = {}

    def add(text: str, location: str) -> None:
        if len(text) < 6 or text in VOCABULARY or text.startswith("http"):
            return
        if len(text) >= 32 and all(character in "0123456789abcdef" for character in text.lower()):
            return
        found.setdefault(_digest(text), location)

    for path in sorted(root.rglob("*")):
        if path.suffix not in {".json", ".md", ".svg", ".html"} or not path.is_file():
            continue
        if "local_datasets" in path.parts:
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if path.suffix == ".json":
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue

            def walk(node: object, location: str) -> None:
                if isinstance(node, dict):
                    for key, value in node.items():
                        walk(value, f"{location}/{key}")
                elif isinstance(node, list):
                    for index, value in enumerate(node[:80]):
                        walk(value, f"{location}[{index}]")
                elif isinstance(node, str):
                    add(node, location)

            walk(payload, path.name)
        else:
            for line_number, line in enumerate(raw.splitlines(), start=1):
                add(line.strip(), f"{path.name}:{line_number}")
    return found


def scan_corpus(corpus: Path, artifacts: dict[str, str], *, max_hits: int = 20, max_rows: int = 2000) -> list[str]:
    """Compare artifact hashes with the first ``max_rows`` counted passwords.

    The counted file is frequency-sorted, so this is the head of the list.
    Short tokens are not compared; they collide with ordinary words.
    """
    ensure_not_lfs_pointer(corpus)
    hits = []
    seen = 0
    with corpus.open("rb") as handle:
        for line_number, raw in enumerate(handle, start=1):
            parsed = parse_counted_line(raw)
            if parsed is None:
                continue
            _frequency, password = parsed
            try:
                text = password.decode("utf-8")
            except UnicodeDecodeError:
                continue
            seen += 1
            if len(text) >= 6:
                digest = _digest(text)
                if digest in artifacts:
                    hits.append(f"{artifacts[digest]} sha256={digest[:12]} corpus_line={line_number}")
            if len(hits) >= max_hits or seen >= max_rows:
                break
    return hits


def main() -> int:
    corpus = Path(sys.argv[1] if len(sys.argv) > 1 else "../rockyou-withcount.txt")
    roots = [Path(item) for item in (sys.argv[2:] or ["docs", "reports"])]
    artifacts: dict[str, str] = {}
    for root in roots:
        if root.is_dir():
            artifacts.update(artifact_strings(root))
    hits = scan_corpus(corpus, artifacts)
    if hits:
        print("\n".join(hits))
        return 1
    print(f"no corpus string in exported text ({len(artifacts)} hashed strings)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
