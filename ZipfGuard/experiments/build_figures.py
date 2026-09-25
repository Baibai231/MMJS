"""Write the four report figures as SVG. No password strings are plotted."""
from __future__ import annotations

import json
import math
from pathlib import Path


def _polyline(points: list[tuple[float, float]], width: int, height: int, pad: int = 36) -> str:
    if len(points) < 2:
        return ""
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 1e-9)
    span_y = max(max_y - min_y, 1e-9)

    def place(point: tuple[float, float]) -> str:
        x = pad + (point[0] - min_x) / span_x * (width - 2 * pad)
        y = height - pad - (point[1] - min_y) / span_y * (height - 2 * pad)
        return f"{x:.1f},{y:.1f}"

    return " ".join(place(point) for point in points)


def zipf_svg(fit: dict) -> str:
    constant = float(fit["C"])
    alpha = float(fit["alpha"])
    x0 = float(fit["curvature_x0"])
    ranks = [index for index in range(1, 4001, 20)]
    curve = [(math.log(rank), math.log(constant) - alpha * math.log(rank)) for rank in ranks]
    body = _polyline(curve, 640, 360)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">
<rect width="640" height="360" fill="#fff"/>
<text x="24" y="28" font-family="sans-serif" font-size="16">RockYou 拟合曲线，不是原始口令</text>
<polyline fill="none" stroke="#2463d9" stroke-width="2" points="{body}"/>
<text x="24" y="340" font-family="sans-serif" font-size="12">log rank → log count；α={alpha:.3f}；floor(x0)={int(math.floor(x0))}；预算语义：频数拟合</text>
</svg>
"""


def bars_svg(labels: list[str], values: list[float], title: str, note: str) -> str:
    width, height = 640, 360
    top = max(values) if values else 1
    top = max(top, 0.01)
    parts = []
    for index, (label, value) in enumerate(zip(labels, values)):
        bar_height = 220 * max(value, 0) / top
        x = 48 + index * 80
        y = 280 - bar_height
        parts.append(f'<rect x="{x}" y="{y:.1f}" width="48" height="{bar_height:.1f}" fill="#2463d9"/>')
        parts.append(f'<text x="{x}" y="300" font-family="sans-serif" font-size="11">{label}</text>')
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="{width}" height="{height}" fill="#fff"/>
<text x="24" y="28" font-family="sans-serif" font-size="16">{title}</text>
{''.join(parts)}
<text x="24" y="340" font-family="sans-serif" font-size="12">{note}</text>
</svg>
"""


def pareto_svg(points: list[tuple[float, float, str]]) -> str:
    plotted = _polyline([(point[0], point[1]) for point in points], 640, 360)
    labels = []
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">
<rect width="640" height="360" fill="#fff"/>
<text x="24" y="28" font-family="sans-serif" font-size="16">安全收益与修改率，合成用户</text>
<polyline fill="none" stroke="#2463d9" stroke-width="2" points="{plotted}"/>
<text x="24" y="340" font-family="sans-serif" font-size="12">横轴修改率，纵轴绝对百分点差。分母是测试用户。不是实测记忆率。</text>
</svg>
"""


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    output = root / "docs" / "figures"
    output.mkdir(parents=True, exist_ok=True)
    fit_path = root / "reports" / "htpg_rockyou_fit.json"
    feature_path = root / "reports" / "htpg_rockyou_features.json"
    grid_path = root / "reports" / "robustness_protocol.json"
    if fit_path.is_file():
        fit = json.loads(fit_path.read_text(encoding="utf-8"))["fit"]
        (output / "rockyou_zipf.svg").write_text(zipf_svg(fit), encoding="utf-8")
    if feature_path.is_file():
        rows = json.loads(feature_path.read_text(encoding="utf-8"))["features"]["igr"]["features"]
        ordered = sorted(rows, key=lambda row: row["rank_unique"])[:6]
        (output / "igr_order.svg").write_text(bars_svg(
            [row["feature"][:8] for row in ordered],
            [float(row["igr_unique"] or 0) for row in ordered],
            "RockYou 等权 IGR，前六项",
            "分母是不同口令。示例建议只用合成字符串，不在这张图里。",
        ), encoding="utf-8")
    curve_path = root / "reports" / "budget_curve.json"
    if curve_path.is_file():
        series = json.loads(curve_path.read_text(encoding="utf-8"))["series"]
        width, height, pad = 640, 360, 40
        colors = {
            "none": "#62728a",
            "modern_blocklist": "#2463d9",
            "paper_igr_unique": "#c47b00",
            "budget_cost": "#0b7a4b",
        }
        lines = []
        for name, color in colors.items():
            points = [(row["budget"], row["arms"][name]["closed_rate"]) for row in series]
            lines.append(
                f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{_polyline(points, width, height, pad)}"/>'
            )
        (output / "budget_comparison.svg").write_text(
            f"""<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">
<rect width="640" height="360" fill="#fff"/>
<text x="24" y="28" font-family="sans-serif" font-size="16">长尾、种子 1、900 用户的自适应猜中率</text>
{''.join(lines)}
<text x="24" y="340" font-family="sans-serif" font-size="12">横轴离线猜测预算，纵轴封闭排序下的测试用户猜中率。开放生成不画在这张图里。灰无策略，蓝现代基线，橙论文，绿新方法。选择固定在预算 40。</text>
</svg>
""",
            encoding="utf-8",
        )
    shift_path = root / "reports" / "shift_head_grid.json"
    if shift_path.is_file():
        shifted = json.loads(shift_path.read_text(encoding="utf-8"))["mechanisms"]["head_shift"]["900"]["arms"]
        labels = ["modern", "paper", "budget", "per-pw"]
        keys = ["modern_blocklist", "paper_igr_unique", "budget_cost", "per_password"]
        values = [float(shifted[key]["mean"]) for key in keys]
        span = max(max(abs(value) for value in values), 0.01)
        parts = []
        for index, (label, value) in enumerate(zip(labels, values)):
            x = 70 + index * 120
            height = 120 * abs(value) / span
            y = 160 - height if value >= 0 else 160
            color = "#0b7a4b" if value >= 0 else "#9b2c2c"
            parts.append(f'<rect x="{x}" y="{y:.1f}" width="64" height="{height:.1f}" fill="{color}"/>')
            parts.append(f'<text x="{x}" y="310" font-family="sans-serif" font-size="12">{label}</text>')
        (output / "head_shift.svg").write_text(
            f"""<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">
<rect width="640" height="360" fill="#fff"/>
<text x="24" y="28" font-family="sans-serif" font-size="16">测试头部被替换后，900 用户的绝对百分点差</text>
<line x1="40" y1="160" x2="600" y2="160" stroke="#62728a"/>
{''.join(parts)}
<text x="24" y="340" font-family="sans-serif" font-size="12">五种子均值。零线以上是下降，以下是上升。分母是测试用户。离线预算 40。</text>
</svg>
""",
            encoding="utf-8",
        )
    if grid_path.is_file():
        grid = json.loads(grid_path.read_text(encoding="utf-8"))["grid"]
        cell = grid["mechanisms"]["long_tail"]["900"]["arms"]
        labels = ["none", "legacy", "modern", "paper", "new"]
        keys = ["none", "legacy_complexity", "modern_blocklist", "paper_igr_unique", "budget_cost"]
        (output / "budget_comparison.svg").write_text(bars_svg(
            labels,
            [float(cell[key]["mean"]) for key in keys],
            "长尾 900 用户、预算 40 的绝对百分点差",
            "五种子均值。分母是测试用户。离线猜测，不是登录次数。攻击器含频次、n-gram 和固定阶马尔可夫。",
        ), encoding="utf-8")
        points = [
            (float(cell[key]["modification_mean"]), float(cell[key]["mean"]), key) for key in keys
        ]
        (output / "pareto.svg").write_text(pareto_svg(points), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
