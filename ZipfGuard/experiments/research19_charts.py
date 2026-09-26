"""Draw SVG charts from research19 JSON. The figures do not contain passwords."""
from __future__ import annotations

import json
from pathlib import Path

from experiments.research19_attack_matrix import select_budget_point


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "research19" / "figures"


COLORS = {
    "current_preprocess": "#3b6ea5",
    "current_preprocess_replaces_historical_audit": "#c47b00",
    "historical_audit_not_rescanned": "#8a8f98",
    "completed_small_site": "#3b6ea5",
}


def _bar(rows: list[tuple[str, float, str]], title: str, note: str, output: Path) -> None:
    width, height = 980, 78 + 28 * max(1, len(rows))
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="16" y="22" font-family="sans-serif" font-size="14">{title}</text>',
        f'<text x="16" y="44" font-family="sans-serif" font-size="12">{note}</text>',
    ]
    for index, (label, value, source) in enumerate(rows):
        y = 64 + index * 28
        bar = max(0.0, min(1.0, value)) * 420
        color = COLORS.get(source, "#9b2c2c")
        parts.append(f'<text x="16" y="{y + 14}" font-family="sans-serif" font-size="12">{label}</text>')
        parts.append(f'<rect x="220" y="{y}" width="{bar:.1f}" height="16" fill="{color}"/>')
        parts.append(f'<text x="{230 + bar:.1f}" y="{y + 13}" font-family="sans-serif" font-size="12">{value:.3f} [{source}]</text>')
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


def main(root: Path | None = None) -> int:
    root = Path(root) if root is not None else ROOT
    out = root / "reports" / "research19" / "figures"
    out.mkdir(parents=True, exist_ok=True)
    identity_path = root / "reports/research19/preprocess_identity.json"
    if not identity_path.is_file():
        raise ValueError(f"缺少 {identity_path}，拒绝画当前图。")
    frequency_path = root / "reports/research19/frequency_matrix.json"
    frequency = json.loads(frequency_path.read_text(encoding="utf-8"))
    frequency_rows = _require_rows(frequency, frequency_path, "points_axis")
    rows = []
    for row in frequency_rows:
        if row["status"] != "completed":
            continue
        axis = row.get("points_axis")
        if not axis:
            raise ValueError(f"{frequency_path}:{row['site']} 缺少 points_axis")
        point = select_budget_point(row.get("points"), budget=1000, axis=axis, label=f"{row['site']}")
        if point.get("incomplete") is True or point.get("cracked") is None or not point.get("total"):
            raise ValueError(f"{row['site']} 的 budget=1000 未完成，不能画成命中率")
        rows.append((row["site"], point["cracked"] / point["total"], "completed_small_site"))
    if not rows:
        raise ValueError(f"{frequency_path} 没有可画的完成预算，拒绝生成空图。")
    _bar(
        rows,
        "Train-frequency cracked rate at unique budget 1000",
        "Only completed small sites. Incomplete large sites are omitted, not drawn as zero.",
        out / "frequency_budget_1000.svg",
    )
    diagnostics_path = root / "reports/research19/distribution_diagnostics.json"
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    if diagnostics.get("identity_status") != "present" or diagnostics.get("sites_not_rescanned") is None:
        raise ValueError(f"{diagnostics_path} 没有当前身份，拒绝画重复率图。")
    diagnostic_rows = _require_rows(diagnostics, diagnostics_path, "count_source")
    repeats = []
    for row in diagnostic_rows:
        if row.get("repeat_ratio") is None or not row.get("occurrence_weighted_metric_available"):
            continue
        source = row.get("count_source")
        if source not in COLORS:
            raise ValueError(f"{row.get('name')} 的 count_source 无法在图上区分")
        repeats.append((row["name"], row["repeat_ratio"], source))
    if not repeats:
        raise ValueError(f"{diagnostics_path} 没有可加权的重复率，拒绝生成空图。")
    current = sum(source.startswith("current_preprocess") for _name, _value, source in repeats)
    historical = sum(source == "historical_audit_not_rescanned" for _name, _value, source in repeats)
    _bar(
        repeats,
        "Repeat ratio from occurrence counts",
        f"Blue/orange: {current} sites on the current preprocess. Gray: {historical} historical sites not reread. Not one pooled mean.",
        out / "repeat_ratio.svg",
    )
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
