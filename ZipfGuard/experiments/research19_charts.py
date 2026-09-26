"""Draw SVG charts from research19 JSON. The figures do not contain passwords."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "research19" / "figures"


def _bar(rows: list[tuple[str, float]], title: str, output: Path) -> None:
    width, height = 760, 40 + 28 * max(1, len(rows))
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="16" y="22" font-family="sans-serif" font-size="14">{title}</text>',
    ]
    for index, (label, value) in enumerate(rows):
        y = 40 + index * 28
        bar = max(0.0, min(1.0, value)) * 420
        parts.append(f'<text x="16" y="{y + 14}" font-family="sans-serif" font-size="12">{label}</text>')
        parts.append(f'<rect x="180" y="{y}" width="{bar:.1f}" height="16" fill="#3b6ea5"/>')
        parts.append(f'<text x="{190 + bar:.1f}" y="{y + 13}" font-family="sans-serif" font-size="12">{value:.3f}</text>')
    parts.append("</svg>")
    output.write_text("\n".join(parts) + "\n", encoding="utf-8")


def _require_rows(payload: dict, path: Path, field: str) -> list[dict]:
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{path} 没有 rows。拒绝画成一张没有柱子的图。")
    checked = [row for row in rows if row.get("status") in (None, "completed")]
    if not checked or any(field not in row for row in checked):
        raise ValueError(f"{path} 缺少 {field}。这是旧结果，拒绝画成一张没有柱子的图。")
    return rows


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    frequency_path = ROOT / "reports/research19/frequency_matrix.json"
    frequency = json.loads(frequency_path.read_text(encoding="utf-8"))
    frequency_rows = _require_rows(frequency, frequency_path, "points_axis")
    rows = []
    for row in frequency_rows:
        if row["status"] != "completed":
            continue
        point = row["points"][-1]
        if point.get("cracked") is None or not point.get("total"):
            continue
        rows.append((row["site"], point["cracked"] / point["total"]))
    if not rows:
        raise ValueError(f"{frequency_path} 没有可画的完成预算，拒绝生成空图。")
    _bar(rows, "Train-frequency cracked rate at budget 1000", OUT / "frequency_budget_1000.svg")
    diagnostics_path = ROOT / "reports/research19/distribution_diagnostics.json"
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    diagnostic_rows = _require_rows(diagnostics, diagnostics_path, "occurrence_weighted_metric_available")
    repeats = [
        (row["name"], row["repeat_ratio"])
        for row in diagnostic_rows
        if row.get("repeat_ratio") is not None and row.get("occurrence_weighted_metric_available")
    ]
    if not repeats:
        raise ValueError(f"{diagnostics_path} 没有可加权的重复率，拒绝生成空图。")
    _bar(repeats, "Repeat ratio from occurrence counts", OUT / "repeat_ratio.svg")
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
