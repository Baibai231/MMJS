"""Add the audited Google-started ten-round Zipf comparison to a saved report."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.intervention_risk import InterventionRisk
from core.intervention_state import Population
from core.registration import load_registration
from experiments.intervention_config import validate_intervention_config
from experiments.intervention_pipeline import make_index, google_round_zipf_experiment
from policy.intervention_response import InterventionResponder
from web.intervention_presentation import render_intervention_html, export_intervention_figures


def add_comparison(report_path: Path) -> dict:
    started = time.perf_counter()
    original = report_path.read_bytes()
    report = json.loads(original.decode('utf-8'))
    cfg = validate_intervention_config(report['config'])
    data = cfg['data']
    source = Path(data['path'])
    if not source.is_absolute():
        source = ROOT / source
    dataset = load_registration(
        source, source_format=data['format'], encoding=data['encoding'],
        users=data['users'], development=data['development'], seed=cfg['seed'],
        cohort_size=data['users'],
    )
    words = [word for cohort in dataset['cohorts'] for word in cohort]
    if Population(words).fingerprint() != report['baseline']['state_sha256']:
        raise ValueError('重新读取的目标账户与原报告不一致')
    train = Counter(dataset['development']['train'])
    reference_words = [word for word, count in sorted(train.items()) for _ in range(count)]
    index = make_index(train, cfg)
    evaluator = InterventionRisk(index, cfg['budgets'], cfg['risk_budget'])
    responder = InterventionResponder(train, cfg['response'])
    comparison = google_round_zipf_experiment(
        Population(words), reference_words, evaluator, responder, cfg,
        report['arms']['fixed_google'],
        progress=lambda message: print(message, flush=True),
    )
    sources = [
        'experiments/intervention_pipeline.py',
        'web/intervention_presentation.py',
        'tools/add_google_round_zipf.py',
    ]
    comparison['metadata'] = {
        'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'runtime_seconds': time.perf_counter() - started,
        'base_report_sha256': hashlib.sha256(original).hexdigest(),
        'source_hashes': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                          for name in sources},
    }
    report['google_round_zipf'] = comparison
    content = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    report_path.write_text(content, encoding='utf-8')
    (report_path.parent / 'report.json.sha256').write_text(
        hashlib.sha256(content.encode()).hexdigest() + '  report.json\n', encoding='utf-8')
    (report_path.parent / 'report.html').write_text(
        render_intervention_html(report), encoding='utf-8')
    export_intervention_figures(report, report_path.parent)
    return comparison


def main() -> None:
    parser = argparse.ArgumentParser(description='补算 Google 起点十轮 Zipf 对照')
    parser.add_argument('report', type=Path)
    args = parser.parse_args()
    comparison = add_comparison(args.report.resolve())
    print(json.dumps({
        'requested_rounds': comparison['requested_rounds'],
        'completed_rounds': comparison['experimental']['rounds_completed'],
        'common_google_start_verified': comparison['common_google_start_verified'],
        'runtime_seconds': comparison['metadata']['runtime_seconds'],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
