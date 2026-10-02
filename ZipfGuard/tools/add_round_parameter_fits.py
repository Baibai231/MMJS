"""Backfill per-round shape diagnostics in an existing intervention report."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.cdf_fit_benchmark import intervention_round_parameter_diagnostics
from web.intervention_presentation import render_intervention_html, export_intervention_figures


def refresh(report_path):
    original = report_path.read_bytes()
    report = json.loads(original.decode('utf-8'))
    fits = intervention_round_parameter_diagnostics(report)
    if fits is None:
        raise ValueError('报告缺少已验证的共同 Google 起点或逐轮快照')
    fits['metadata'] = {'source_report_sha256': hashlib.sha256(original).hexdigest(),
                        'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    report['round_parameter_fits'] = fits
    backup = report_path.with_name('report.before_round_parameters.json')
    if not backup.exists():
        backup.write_bytes(original)
    content = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n'
    html = render_intervention_html(report)
    export_intervention_figures(report, report_path.parent)
    temp = report_path.with_suffix('.json.tmp')
    temp.write_text(content, encoding='utf-8')
    temp.replace(report_path)
    report_path.with_name('report.json.sha256').write_text(
        hashlib.sha256(report_path.read_bytes()).hexdigest()+'  report.json\n', encoding='utf-8')
    report_path.with_name('report.html').write_text(html, encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('report', type=Path)
    result = refresh(parser.parse_args().report)
    print(result['metadata']['run_id'], len(result['round_parameter_fits']['rows']))
