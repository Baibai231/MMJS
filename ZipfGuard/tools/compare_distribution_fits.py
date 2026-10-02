"""Generate a standalone fitting comparison without changing a policy run."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.cdf_fit_benchmark import intervention_fit_diagnostics
from web.cdf_fit_presentation import fit_chart_specs, render_fit_diagnostics
from web.presentation import STYLE
from tools.run_dynamic_study import svg_only


def compare_report(report_path, output=None):
    raw = report_path.read_bytes()
    report = json.loads(raw.decode('utf-8'))
    diagnostics = intervention_fit_diagnostics(report, compare_legacy=True)
    diagnostics['source_report_sha256'] = hashlib.sha256(raw).hexdigest()
    diagnostics['source_run_id'] = report['metadata']['run_id']
    diagnostics['source_hashes'] = {
        name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
            'core/cdf_sampling.py', 'experiments/cdf_fit_benchmark.py',
            'web/cdf_fit_presentation.py', 'tools/compare_distribution_fits.py')}
    report['distribution_fits'] = diagnostics
    directory = output or ROOT/'reports'/'cdf_fit'/report['metadata']['run_id']
    directory.mkdir(parents=True, exist_ok=True)
    content = json.dumps(diagnostics, ensure_ascii=False, indent=2, allow_nan=False)+'\n'
    (directory/'report.json').write_text(content, encoding='utf-8')
    (directory/'report.json.sha256').write_text(hashlib.sha256(content.encode()).hexdigest()+'  report.json\n', encoding='utf-8')
    body = render_fit_diagnostics(report, compare_legacy=True)
    prefix = '/api/intervention/figure/'+report['metadata']['run_id']+'/'
    body = body.replace(prefix, '')
    html = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>ZipfGuard · 分布拟合比较</title><style>'+STYLE+'</style><body><main>'
            +body+'</main></body></html>')
    (directory/'report.html').write_text(html, encoding='utf-8')
    for name, (_, series, opts) in fit_chart_specs(report, compare_legacy=True).items():
        (directory/(name+'.svg')).write_text(svg_only(series, **opts), encoding='utf-8')
    return diagnostics, directory


def main():
    parser = argparse.ArgumentParser(description='比较现有报告的分布拟合，不重跑或覆盖策略实验')
    parser.add_argument('report', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report, directory = compare_report(args.report, args.output)
    print(directory/'report.html')
    for row in report['results']:
        if row['status'] == 'complete':
            fit = row['fit']
            print(row['label'], 'users=', fit['users'], 'old=', fit['previous_models'][0]['ks'],
                  'new=', fit['sampling_fit']['replication']['mean_max_cdf_error'])


if __name__ == '__main__':
    main()
