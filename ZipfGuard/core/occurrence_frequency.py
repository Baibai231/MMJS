"""Turn a local password file into a descending frequency vector.

Plaintext is counted in memory and then discarded. The returned object has
only integer frequencies plus file-level audit fields. Pickle payloads are
accepted because that is how MAYA stores a formatted corpus, but only a
sequence of plain strings may be unpickled. A Git LFS pointer is rejected.
"""
from __future__ import annotations

import builtins
import hashlib
import pickle
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Iterable

from core.counted_corpus import CorpusFormatError, ensure_not_lfs_pointer, parse_counted_line


class _SequenceUnpickler(pickle.Unpickler):
    """Load a list or tuple of strings. Other globals are rejected."""

    _ALLOWED = {
        ("builtins", "list"),
        ("builtins", "tuple"),
        ("builtins", "dict"),
        ("builtins", "set"),
        ("builtins", "frozenset"),
        ("builtins", "str"),
        ("builtins", "int"),
        ("builtins", "float"),
        ("builtins", "bytes"),
        ("collections", "OrderedDict"),
    }

    def find_class(self, module: str, name: str):
        if (module, name) not in self._ALLOWED:
            raise CorpusFormatError(f"拒绝 pickle 类型 {module}.{name}")
        if module == "collections" and name == "OrderedDict":
            return OrderedDict
        return getattr(builtins, name)


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _frequencies_from_counter(counter: Counter) -> list[int]:
    frequencies = sorted((int(count) for count in counter.values() if count > 0), reverse=True)
    counter.clear()
    return frequencies


def frequencies_from_occurrences(path: str | Path) -> dict:
    meta, counts = load_occurrence_counter(path)
    frequencies = _frequencies_from_counter(counts)
    meta["frequencies"] = frequencies
    meta["unique_types"] = len(frequencies)
    meta["occurrence_total"] = int(sum(frequencies))
    meta["top1_count"] = int(frequencies[0])
    if len(frequencies) < 3:
        raise CorpusFormatError("有效类别少于 3，不能拟合")
    return meta


def load_occurrence_counter(path: str | Path) -> tuple[dict, Counter[str]]:
    """Return audit fields and a counter whose keys are still plaintext.

    The caller has to finish the aggregate and then ``clear()`` the counter.
    This function does not write those keys anywhere.
    """
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    ensure_not_lfs_pointer(source)
    digest = _digest_file(source)
    with source.open("rb") as handle:
        header = handle.read(16)
    stats: Counter[str] | None = None
    if header.startswith(b"\x80"):
        mode, rows, stats, counts = _counter_from_pickle(source)
        skipped = int(stats["non_string"] + stats["empty"] + stats["internal_control"])
    else:
        mode, rows, skipped, counts = _counter_from_text(source)
    if not counts:
        raise CorpusFormatError("有效类别少于 3，不能拟合")
    meta = {
        "format": mode,
        "source_name": source.name,
        "source_bytes": source.stat().st_size,
        "source_sha256": digest,
        "rows_read": rows,
        "rows_skipped": skipped,
        "unique_types": len(counts),
        "occurrence_total": int(sum(counts.values())),
        "top1_count": int(max(counts.values())),
        "plaintext_retained": False,
    }
    if stats is not None:
        meta["preprocess_version"] = PREPROCESS_VERSION
        meta["trailing_lf_removed"] = int(stats["removed_lf"])
        meta["trailing_crlf_removed"] = int(stats["removed_crlf"])
        meta["rows_without_record_terminator"] = int(stats["no_record_terminator"])
        meta["rows_excluded_internal_controls"] = int(stats["internal_control"])
        meta["rows_excluded_empty"] = int(stats["empty"])
        meta["rows_excluded_non_string"] = int(stats["non_string"])
    return meta, counts


PREPROCESS_VERSION = "maya-pickle-record-terminator-v1"


def _has_control(text: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in text)


def normalize_pickle_string(item: str) -> tuple[str | None, dict[str, int]]:
    """Drop one MAYA record terminator. Do not delete characters inside the value.

    The seven local MAYA pickles store each string with one trailing LF.
    A CR LF terminator is accepted the same way. Terminator removal is counted
    even when the remaining value is excluded for an internal control character.
    """
    flags = {
        "removed_lf": 0,
        "removed_crlf": 0,
        "no_record_terminator": 0,
        "empty": 0,
        "internal_control": 0,
    }
    if item.endswith("\r\n"):
        body = item[:-2]
        flags["removed_crlf"] = 1
    elif item.endswith("\n"):
        body = item[:-1]
        flags["removed_lf"] = 1
    else:
        body = item
        flags["no_record_terminator"] = 1
    if body == "":
        flags["empty"] = 1
        return None, flags
    if _has_control(body):
        flags["internal_control"] = 1
        return None, flags
    return body, flags


def _counter_from_pickle(source: Path) -> tuple[str, int, Counter[str], Counter[str]]:
    with source.open("rb") as handle:
        payload = _SequenceUnpickler(handle).load()
    if isinstance(payload, dict):
        raise CorpusFormatError("pickle 是字典而不是口令序列，拒绝猜测字段名")
    if not isinstance(payload, Iterable):
        raise CorpusFormatError("pickle 不是口令序列")
    counts: Counter[str] = Counter()
    stats: Counter[str] = Counter()
    rows = 0
    for item in payload:
        rows += 1
        if not isinstance(item, str):
            stats["non_string"] += 1
            continue
        text, flags = normalize_pickle_string(item)
        for key, value in flags.items():
            stats[key] += value
        if text is None:
            continue
        counts[text] += 1
    del payload
    return "maya_pickle_occurrence", rows, stats, counts


def _counter_from_text(source: Path) -> tuple[str, int, int, Counter[str]]:
    counts: Counter[str] = Counter()
    rows = skipped = counted_rows = plain_rows = 0
    with source.open("rb") as handle:
        for raw in handle:
            rows += 1
            parsed = parse_counted_line(raw)
            if parsed is not None:
                frequency, password = parsed
                try:
                    text = password.decode("utf-8")
                except UnicodeDecodeError:
                    skipped += 1
                    continue
                if not text:
                    skipped += 1
                    continue
                counts[text] += frequency
                counted_rows += 1
                continue
            try:
                text = raw.decode("utf-8").strip("\r\n")
            except UnicodeDecodeError:
                skipped += 1
                continue
            if not text:
                skipped += 1
                continue
            counts[text] += 1
            plain_rows += 1
    mode = "count_field" if counted_rows and not plain_rows else "duplicate_lines" if plain_rows else "mixed_or_empty"
    if counted_rows and plain_rows:
        raise CorpusFormatError("同一文件里既有带次数的行又有纯口令行，拒绝合并")
    return mode, rows, skipped, counts
