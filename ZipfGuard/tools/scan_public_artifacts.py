"""Reject public JSON that still carries guess strings or password fields."""
from __future__ import annotations

import json
import sys
from pathlib import Path


FORBIDDEN = {"password", "passwords", "plaintext", "top_guesses", "frequencies"}


def scan(path: Path) -> list[str]:
    problems = []
    if path.suffix != ".json":
        return problems
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"{path.name}: {type(exc).__name__}"]

    def walk(node: object, location: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).lower() in FORBIDDEN and not (key == "plaintext_retained" or str(key).endswith("_retained")):
                    if str(key).lower() in {"password", "passwords", "plaintext", "top_guesses"}:
                        problems.append(f"{path.name}:{location}/{key}")
                walk(value, f"{location}/{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node[:50]):
                walk(value, f"{location}[{index}]")

    walk(payload, "")
    return problems


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "reports")
    found = []
    if root.is_dir():
        for path in sorted(root.glob("*.json")):
            found.extend(scan(path))
    elif root.is_file():
        found.extend(scan(root))
    if found:
        print("\n".join(found))
        return 1
    print("no forbidden fields")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
