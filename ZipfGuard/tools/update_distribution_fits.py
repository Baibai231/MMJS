"""Refresh only fitting diagnostics in a saved intervention report."""
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

from experiments.cdf_fit_benchmark import intervention_fit_diagnostics
from web.intervention_presentation import render_intervention_html, export_intervention_figures


def refresh(report_path):
    original = report_path.read_bytes()
    report = json.loads(original.decode('utf-8'))
    fits = intervention_fit_diagnostics(report)
    fits['metadata'] = {'source_report_sha256': hashlib.sha256(original).hexdigest(),
        'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'source_hashes': {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
            'core/cdf_sampling.py', 'core/distribution_analysis.py',
            'experiments/cdf_fit_benchmark.py', 'web/cdf_fit_presentation.py')}}
    report['distribution_fits'] = fits
    # Preserve the previous report for audit; policy observations are untouched.
    backup = report_path.with_name('report.before_default_cdf.json')
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
    args = parser.parse_args()
    result = refresh(args.report)
    print(result['metadata']['run_id'], result['distribution_fits']['fit_version'])
