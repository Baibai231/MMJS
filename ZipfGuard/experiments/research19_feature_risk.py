"""Associate hak5 test features with one frequency-dictionary hit list.

The rates are descriptive. They are not a causal effect of editing a feature.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from core.htpg_features import HTPGFeatureExtractor
from core.occurrence_frequency import load_occurrence_counter
from experiments.research19_smoke import occurrence_split


ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ROOT / "local_datasets/maya/hak5/extracted/datasets/en/forum/hak5.pickle"
LEXICON = ROOT / "resources" / "htpg_reference_v1.json"
FLAGS = ("capital", "date", "keyboard", "lowercase", "word_type", "lastname")


def main() -> int:
    _meta, counts = load_occurrence_counter(PAYLOAD)
    split = occurrence_split(counts, seed=19)
    train_counts = Counter(split["train"])
    ordered = sorted(train_counts, key=lambda word: (-train_counts[word], word))
    guesses = set(ordered[:1000])
    extractor = HTPGFeatureExtractor.from_profile(LEXICON)
    groups = {"cracked": [], "uncracked": []}
    for password in split["test"]:
        groups["cracked" if password in guesses else "uncracked"].append(extractor.extract(password))
    rows = []
    for name in FLAGS:
        cracked_hits = sum(bool(getattr(item, name)) for item in groups["cracked"])
        uncracked_hits = sum(bool(getattr(item, name)) for item in groups["uncracked"])
        rows.append({
            "feature": name,
            "cracked_with_feature": cracked_hits,
            "cracked_total": len(groups["cracked"]),
            "uncracked_with_feature": uncracked_hits,
            "uncracked_total": len(groups["uncracked"]),
        })
    report = {
        "protocol": "research19-v1",
        "site": "hak5",
        "attacker": "train_frequency_open_dictionary",
        "budget": 1000,
        "lexicon": "resources/htpg_reference_v1.json",
        "causal_modification_effect": None,
        "rows": rows,
        "claim_supported": False,
        "plaintext_retained": False,
    }
    output = ROOT / "reports" / "research19" / "hak5_feature_association.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cracked": len(groups["cracked"]), "uncracked": len(groups["uncracked"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
