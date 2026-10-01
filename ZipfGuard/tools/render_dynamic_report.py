"""Refresh saved HTML and SVG figures without rerunning data or attacks."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_dynamic_study import export_figures
from web.dynamic_presentation import render_dynamic_html


def render_saved(report_path):
    path = Path(report_path).resolve()
    result = json.loads(path.read_text(encoding='utf-8'))
    if result.get('schema_version') != 'zipfguard-dynamic-v1-result':
        raise ValueError('不是分批实验报告')
    directory = path.parent
    figures = export_figures(result, directory)
    (directory / 'report.html').write_text(render_dynamic_html(result), encoding='utf-8')
    return figures


def main():
    parser = argparse.ArgumentParser(description='重新绘制已完成实验的报告和图像')
    parser.add_argument('report', type=Path)
    args = parser.parse_args()
    print(json.dumps({'figures': render_saved(args.report)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
