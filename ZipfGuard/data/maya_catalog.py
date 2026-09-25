"""Index of the MAYA password-benchmark corpora.

The files themselves are not in this repository. Each card records the public
Google Drive id used by williamcorrias/MAYA-Password-Benchmarking
(master, dataset script ``script/utils/download_raw_data.py``). A card is not
evidence that the local file has been downloaded or that its licence allows
redistribution. Frequency in these files is the number of repeated plaintext
rows after MAYA's formatter, unless a row is already ``count password``.
"""
from __future__ import annotations


MAYA_REPO = "https://github.com/williamcorrias/MAYA-Password-Benchmarking"
MAYA_PAPER = "10.1109/SP63933.2026.00081"

DATASETS = (
    {"name": "rockyou", "drive_id": "1XEsAf99H3DmH4ichbH-4yXkb0mSwoADY", "language": "en", "service": "gaming"},
    {"name": "myspace", "drive_id": "1mzPv6oL4RHPFPu_tLGCsnkAJi2pI7P1Z", "language": "en", "service": "social-net"},
    {"name": "phpbb", "drive_id": "1FrT2dRsSoAzk7Sxu0dwYP4Q2xnH0l5vz", "language": "en", "service": "forum"},
    {"name": "linkedin", "drive_id": "1QjWwHlp4UgclHMPodc_SuKOre1kSxP41", "language": "en", "service": "social-net"},
    {"name": "hotmail", "drive_id": "12jpP1jNqgSmP1ISMNHmQ1HjZJ3gZ9oNB", "language": "en", "service": "mail"},
    {"name": "mailru", "drive_id": "1up3rVwxxZ6YP9lMM6jR8IcMTjWGiv16t", "language": "ru", "service": "mail"},
    {"name": "yandex", "drive_id": "1-78sD5-kbcBgL4yuOSSSHsVXM0P3ww-H", "language": "ru", "service": "web-portal"},
    {"name": "yahoo", "drive_id": "1lnRzNGTW6_xOSatJWAd3s6foCe3PGLkH", "language": "en", "service": "web-portal"},
    {"name": "faithwriters", "drive_id": "1UYxr97VWNWCz46NdXeBoM8pjkV5pFahT", "language": "en", "service": "forum"},
    {"name": "hak5", "drive_id": "1ArVangnE6cXEWVh-zQoi-o5mmzkVK8BR", "language": "en", "service": "forum"},
    {"name": "000webhost", "drive_id": "1vZEY2FajxIgRsfVtzkVXdTcU99QCs3Mn", "language": "en", "service": "forum"},
    {"name": "singles", "drive_id": "18qXvKJ9L21r4h28Qc6vjow5zppGjAr6w", "language": "en", "service": "dating-sites"},
    {"name": "gmail", "drive_id": "14NB8L-ndEGogofn_kS_fB6fsbqJTfT42", "language": "ru", "service": "mail"},
    {"name": "zomato", "drive_id": "1YCm_S2o7YvNg0kH9R2KOr4dwVJ2iZFaw", "language": "en", "service": "social-net"},
    {"name": "taobao", "drive_id": "1Bm7jqUIXLzOd6eX0D9yE98Klkma6_tbw", "language": "zh", "service": "e-commerce"},
    {"name": "mate1", "drive_id": "13nIOWSpwiuTCAi5ZCkTXV7JC6pRLhnY9", "language": "en", "service": "dating-sites"},
    {"name": "twitter", "drive_id": "1MAvJL4Zolxc05jXzjezZQpmQP1BMdrMb", "language": "ru", "service": "social-net"},
    {"name": "ashleymadison", "drive_id": "1dkIApEcQDn5VKKXJ1gjzRzh01foiGvNG", "language": "en", "service": "social-net"},
    {"name": "libero", "drive_id": "1fVmlvbhAbc5J4PZ3iHqJ2moJoHljz1_U", "language": "it", "service": "mail"},
)


def dataset_names() -> tuple[str, ...]:
    return tuple(row["name"] for row in DATASETS)


def dataset_card(name: str) -> dict:
    matches = [row for row in DATASETS if row["name"] == name]
    if len(matches) != 1:
        known = ", ".join(row["name"] for row in DATASETS)
        raise KeyError(f"未知 MAYA 数据集 {name!r}。已知：{known}")
    card = dict(matches[0])
    card["source_repo"] = MAYA_REPO
    card["paper_doi"] = MAYA_PAPER
    card["frequency_meaning"] = (
        "MAYA 下载脚本把这些文件标成 formatted。"
        "若解压后是一行一条口令，频次等于相同字符串出现的行数；"
        "若一行以十进制次数加分隔符开头，则使用该次数。"
        "两种都不是已经核验过的自然人人数。"
    )
    return card
