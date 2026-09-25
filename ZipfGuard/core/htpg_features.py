"""HTPG section III-D features, with explicit operational definitions.

This implements extraction, not IGR, recommendations or attack evaluation.
See docs/HTPG_FEATURES.md for reproduction limits and dictionary provenance.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import unicodedata

FEATURE_VERSION = "htpg-operational-v1"
FEATURE_NAMES = ("lsd_structure", "length", "capital", "date", "keyboard",
                 "specplace", "lowercase", "word_type", "lastname")


class LexiconMatcher:
    """Aho-Corasick: one scan for two substring dictionaries, without regex explosion."""

    def __init__(self, emotional_words: list[str], surnames: list[str]):
        self.edges: list[dict[str, int]] = [{}]
        self.fail = [0]
        self.flags = [0]
        for terms, flag in ((emotional_words, 1), (surnames, 2)):
            for term in terms:
                state = 0
                for char in term.lower():
                    if char not in self.edges[state]:
                        self.edges[state][char] = len(self.edges)
                        self.edges.append({})
                        self.fail.append(0)
                        self.flags.append(0)
                    state = self.edges[state][char]
                self.flags[state] |= flag
        queue = deque(self.edges[0].values())
        while queue:
            state = queue.popleft()
            for char, child in self.edges[state].items():
                queue.append(child)
                fallback = self.fail[state]
                while fallback and char not in self.edges[fallback]:
                    fallback = self.fail[fallback]
                self.fail[child] = self.edges[fallback].get(char, 0)
                self.flags[child] |= self.flags[self.fail[child]]

    def match(self, value: str) -> int:
        state = found = 0
        for char in value.lower():
            while state and char not in self.edges[state]:
                state = self.fail[state]
            state = self.edges[state].get(char, 0)
            found |= self.flags[state]
            if found == 3:
                break
        return found


@dataclass(frozen=True)
class FeatureVector:
    lsd_structure: str
    length: int
    capital: bool
    date: bool
    keyboard: bool
    specplace: str
    lowercase: bool
    word_type: bool | None
    lastname: bool | None

    def to_dict(self) -> dict:
        return {name: getattr(self, name) for name in FEATURE_NAMES}


def char_class(char: str) -> str:
    return "L" if char.isalpha() else "D" if char.isdecimal() else "S"


def lsd_structure(value: str) -> str:
    runs: list[str] = []
    previous = ""
    length = 0
    for char in value:
        current = char_class(char)
        if previous and current != previous:
            runs.append(f"{previous}{length}")
            length = 0
        previous = current
        length += 1
    if previous:
        runs.append(f"{previous}{length}")
    return "".join(runs)


# An operational date grammar, NOT an author-supplied HTPG recognizer.
# Whole digit runs only. Six-digit dates intentionally excluded as ambiguous.
_DATE_CANDIDATE = re.compile(
    r"(?<![0-9])(?:[0-9]{4}[-/.][0-9]{1,2}[-/.][0-9]{1,2}|"
    r"[0-9]{1,2}[-/.][0-9]{1,2}[-/.][0-9]{4}|[0-9]{8}|[0-9]{4})(?![0-9])"
)


def _valid_date(year: int, month: int, day: int) -> bool:
    if not 1900 <= year <= 2099:
        return False
    try:
        date(year, month, day)
        return True
    except ValueError:
        return False


def _token_is_date(token: str) -> bool:
    parts = re.split(r"[-/.]", token)
    if len(parts) == 3:
        if len(set(re.findall(r"[-/.]", token))) != 1:
            return False
        a, b, c = map(int, parts)
        possibilities = [(a, b, c)] if len(parts[0]) == 4 else [(c, b, a), (c, a, b)]
    elif len(token) == 4:
        return 1900 <= int(token) <= 2099
    else:
        possibilities = [(int(token[:4]), int(token[4:6]), int(token[6:])),
                         (int(token[4:]), int(token[2:4]), int(token[:2])),
                         (int(token[4:]), int(token[:2]), int(token[2:4]))]
    return any(_valid_date(*candidate) for candidate in possibilities)


def date_spans(value: str) -> list[tuple[int, int]]:
    """Spans accepted by the same date grammar as ``contains_date``."""
    return [match.span() for match in _DATE_CANDIDATE.finditer(value) if _token_is_date(match.group())]


def contains_date(value: str) -> bool:
    return bool(date_spans(value))


_ROWS = (("`1234567890-=", 0.0), ("qwertyuiop[]\\", 0.25),
         ("asdfghjkl;'", 0.5), ("zxcvbnm,./", 1.0))
_KEYS = {key: (offset + x, y) for y, (row, offset) in enumerate(_ROWS) for x, key in enumerate(row)}
_SHIFT = str.maketrans('~!@#$%^&*()_+{}|:"<>?', '`1234567890-=[]\\;\',./')


def keyboard_walk_span(value: str, minimum: int = 4) -> tuple[int, int] | None:
    """First walk span from the same detector as ``contains_keyboard_walk``."""
    if minimum < 3:
        raise ValueError("keyboard minimum must be >=3")
    previous = None
    window: list[int] = []
    for index, char in enumerate(value):
        mapped = char.lower().translate(_SHIFT)
        position = _KEYS.get(mapped)
        adjacent = (position is not None and previous is not None and position != previous
                    and abs(position[0] - previous[0]) <= 1.25
                    and abs(position[1] - previous[1]) <= 1)
        window = window[-(minimum - 1):] + [index] if adjacent else [index]
        distinct = {value[item].lower().translate(_SHIFT) for item in window}
        if position is not None and len(window) >= minimum and len(distinct) >= 3:
            return window[0], window[-1] + 1
        previous = position
    return None


def contains_keyboard_walk(value: str, minimum: int = 4) -> bool:
    """Physical US-QWERTY neighbor walk, >=4 keys, >=3 distinct keys.

    Shift/case do not change key positions. Repeating the same key breaks a walk.
    Direction changes are allowed; this is a heuristic feature, not a risk verdict.
    """
    return keyboard_walk_span(value, minimum) is not None


class HTPGFeatureExtractor:
    def __init__(self, emotional_words: list[str] | None = None,
                 surnames: list[str] | None = None):
        for terms in (emotional_words, surnames):
            if terms is not None and (not isinstance(terms, list) or not terms or any(not isinstance(x, str) or not x for x in terms)):
                raise ValueError("provided lexicons must contain nonempty strings")
        self.word_available = emotional_words is not None
        self.surname_available = surnames is not None
        self.word_terms = [term.lower() for term in (emotional_words or [])]
        self.surname_terms = [term.lower() for term in (surnames or [])]
        self.matcher = LexiconMatcher(emotional_words or [], surnames or [])
        self.lexicon_metadata: dict = {"profile_id": None, "sha256": None}

    @classmethod
    def from_profile(cls, path: str | Path) -> "HTPGFeatureExtractor":
        raw = Path(path).read_bytes()
        profile = json.loads(raw)
        if profile.get("schema_version") != 1:
            raise ValueError("unsupported lexicon profile")
        extractor = cls(profile["word_type"]["terms"], profile["lastname"]["terms"])
        extractor.lexicon_metadata = {
            "profile_id": profile["profile_id"], "sha256": hashlib.sha256(raw).hexdigest(),
            "word_type_terms": len(profile["word_type"]["terms"]),
            "lastname_terms": len(profile["lastname"]["terms"]),
            "sources": profile.get("sources", []),
            "selection": {key: profile[key].get("selection") for key in ("word_type", "lastname")},
        }
        return extractor

    def metadata(self) -> dict:
        return {"feature_version": FEATURE_VERSION, "unicode_version": unicodedata.unidata_version,
                "normalization": "none; codepoint length; Unicode alpha/decimal/upper/lower",
                "lexicons": self.lexicon_metadata,
                "lexicon_matching": "case-insensitive substring, no token boundary",
                "date": "whole digit run: year 1900..2099, valid YYYYMMDD/DDMMYYYY/MMDDYYYY or separated dates",
                "keyboard": "US-QWERTY physical neighbors; minimum 4; at least 3 distinct keys; shift mapped",
                "specplace": "none or ordered head+middle+tail set; multi-location extension",
                "lowercase": "isolated boundary lowercase; singleton lowercase is true",
                "word_type_available": self.word_available, "lastname_available": self.surname_available}

    def extract(self, value: str) -> FeatureVector:
        if not isinstance(value, str) or not value:
            raise ValueError("feature extraction requires a nonempty Unicode string")
        positions = set()
        for i, char in enumerate(value):
            if char_class(char) == "S":
                if i == 0:
                    positions.add("head")
                if i == len(value) - 1:
                    positions.add("tail")
                if 0 < i < len(value) - 1:
                    positions.add("middle")
        found = self.matcher.match(value)
        return FeatureVector(
            lsd_structure(value), len(value), any(c.isupper() for c in value),
            contains_date(value), contains_keyboard_walk(value),
            "+".join(p for p in ("head", "middle", "tail") if p in positions) or "none",
            ((value[0].islower() and (len(value) == 1 or not value[1].islower()))
             or (value[-1].islower() and (len(value) == 1 or not value[-2].islower()))),
            bool(found & 1) if self.word_available else None,
            bool(found & 2) if self.surname_available else None,
        )
