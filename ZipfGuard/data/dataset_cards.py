"""Dataset cards for corpora this project is allowed to describe.

Cards store source, counts and hashes. They do not store passwords.
"""
from __future__ import annotations


ROCKYOU_WITHCOUNT = {
    "dataset_id": "rockyou-withcount",
    "path_relative_to_zipfguard": "../rockyou-withcount.txt",
    "format": "frequency then password",
    "rows": 14_344_391,
    "frequency_total": 32_603_388,
    "sha256": "e9d22d5316e7668aba2e9fa1c22dfc6e5afd217a60defc8f3c7f68a4f48f84b9",
    "deduplicated": False,
    "frequency_meaning": "文件给出的次数。不是把 Rockyou.txt 的逐行出现再数一遍。",
    "cleaning": "空口令和非 UTF-8 行被单独计数，不从总量里悄悄删掉。",
    "paper_total": 32_510_281,
    "unexplained_gap": 93_107,
    "use_boundary": "只输出聚合统计。原始行不进入报告、网页或交付包。",
}

ROCKYOU_LINE_FILE = {
    "dataset_id": "Rockyou.txt",
    "path_relative_to_zipfguard": "../lab_basic_50_dicts/Rockyou.txt",
    "format": "one string per line",
    "frequency_meaning": "一行一次只表示文件里的重复，不能当成账户人数。",
    "use_boundary": "不能与 rockyou-withcount 的次数混用。",
}


def card(dataset_id: str) -> dict:
    if dataset_id == ROCKYOU_WITHCOUNT["dataset_id"]:
        return dict(ROCKYOU_WITHCOUNT)
    if dataset_id == ROCKYOU_LINE_FILE["dataset_id"]:
        return dict(ROCKYOU_LINE_FILE)
    from data.maya_catalog import dataset_card
    return dataset_card(dataset_id)
