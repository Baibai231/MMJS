"""Streaming parser for ``frequency<sep>password`` corpora.

Password bytes are available to callers that must compute features, but this
module's audit result keeps only counts and hashes. A Git LFS pointer is
rejected before any row is treated as data.
"""
from __future__ import annotations

import hashlib
from array import array
from pathlib import Path
from typing import Iterator


LFS_PREFIX = b"version https://git-lfs.github.com/spec/v1"
PAPER_ROCKYOU_TOTAL = 32_510_281
PAPER_HEAD_RANKS = 1_171
PAPER_HEAD_MASS = 3_899_108


class CorpusFormatError(ValueError):
    """The file is not a usable counted corpus."""


def ensure_not_lfs_pointer(path: str | Path) -> None:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    with source.open("rb") as handle:
        head = handle.read(len(LFS_PREFIX))
    if head.startswith(LFS_PREFIX):
        raise CorpusFormatError(
            "输入仍是 Git LFS 指针，不是语料。请在仓库根目录执行 git lfs pull 后再运行。"
        )


def parse_counted_line(raw: bytes) -> tuple[int, bytes] | None:
    """Return ``(frequency, password_bytes)`` or ``None`` when the row is unusable.

    Leading spaces are indentation. Exactly one following space or tab is the
    separator. Later whitespace stays inside the password field.
    """
    stripped = raw[:-1] if raw.endswith(b"\n") else raw
    if stripped.endswith(b"\r"):
        stripped = stripped[:-1]
    if not stripped.strip():
        return None
    cursor = 0
    while cursor < len(stripped) and stripped[cursor] in (9, 32):
        cursor += 1
    start = cursor
    while cursor < len(stripped) and 48 <= stripped[cursor] <= 57:
        cursor += 1
    if cursor == start or cursor >= len(stripped) or stripped[cursor] not in (9, 32):
        return None
    frequency = int(stripped[start:cursor])
    if frequency <= 0:
        return None
    return frequency, stripped[cursor + 1:]


def looks_like_counted_corpus(path: str | Path) -> bool:
    """True when the first non-empty row is ``frequency + separator + rest``."""
    ensure_not_lfs_pointer(path)
    with Path(path).open("rb") as handle:
        for raw in handle:
            parsed = parse_counted_line(raw)
            if parsed is None and not raw.strip():
                continue
            return parsed is not None
    return False


def iter_counted_rows(path: str | Path) -> Iterator[tuple[int, int, bytes]]:
    """Yield ``(line_number, frequency, password_bytes)`` for valid rows."""
    ensure_not_lfs_pointer(path)
    with Path(path).open("rb") as handle:
        for line_number, raw in enumerate(handle, start=1):
            parsed = parse_counted_line(raw)
            if parsed is None:
                continue
            frequency, password = parsed
            yield line_number, frequency, password


def audit_counted_frequencies(path: str | Path) -> dict:
    """Read every frequency without retaining password text.

    The returned rank vector follows file order. A later increase is counted
    but does not reorder rows; callers that need a sorted Zipf rank must sort
    explicitly and record that choice.
    """
    source = Path(path)
    ensure_not_lfs_pointer(source)
    digest = hashlib.sha256()
    frequencies: array = array("Q")
    lines = blank = invalid = 0
    empty_password_rows = empty_password_mass = 0
    non_utf8_rows = non_utf8_mass = 0
    control_rows = 0
    order_increases = 0
    previous: int | None = None
    with source.open("rb") as handle:
        for raw in handle:
            lines += 1
            digest.update(raw)
            if not raw.strip():
                blank += 1
                continue
            parsed = parse_counted_line(raw)
            if parsed is None:
                invalid += 1
                continue
            frequency, password = parsed
            if previous is not None and frequency > previous:
                order_increases += 1
            previous = frequency
            if any(byte < 32 or byte == 127 for byte in password):
                control_rows += 1
            if not password.strip():
                empty_password_rows += 1
                empty_password_mass += frequency
            try:
                password.decode("utf-8")
            except UnicodeDecodeError:
                non_utf8_rows += 1
                non_utf8_mass += frequency
            frequencies.append(frequency)
    if len(frequencies) < 2:
        raise CorpusFormatError("带频次语料至少需要两行有效记录")
    total = int(sum(frequencies))
    prefix = PAPER_HEAD_RANKS
    prefix_mass = int(sum(frequencies[:prefix])) if len(frequencies) >= prefix else None
    return {
        "format": "frequency_then_password",
        "source_bytes": source.stat().st_size,
        "source_sha256": digest.hexdigest(),
        "lines": lines,
        "valid_rows": len(frequencies),
        "blank_rows": blank,
        "invalid_rows": invalid,
        "frequency_total": total,
        "paper_reported_total": PAPER_ROCKYOU_TOTAL,
        "total_minus_paper": total - PAPER_ROCKYOU_TOTAL,
        "empty_password_rows": empty_password_rows,
        "empty_password_mass": empty_password_mass,
        "non_utf8_rows": non_utf8_rows,
        "non_utf8_mass": non_utf8_mass,
        "control_byte_rows": control_rows,
        "order_increases": order_increases,
        "paper_prefix_ranks": prefix,
        "paper_prefix_mass": prefix_mass,
        "paper_prefix_mass_expected": PAPER_HEAD_MASS,
        "paper_prefix_mass_matches": prefix_mass == PAPER_HEAD_MASS,
        "frequencies": frequencies,
    }
