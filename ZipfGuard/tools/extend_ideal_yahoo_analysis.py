"""Reproduce the source sample, simulate Yahoo, derive W1 on historical states.

Never relabel historical decisions as a fresh run of the current protocol.
Checkpoint the real control so rendering repairs do not repeat experiments.
"""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.registration import load_registration
from core.intervention_state import Population
from core.intervention_risk import InterventionRisk
from core.ideal_distribution import attach_ideal_analysis, METRIC
from core.distribution_analysis import sampling_diagnostics
from experiments.cdf_fit_benchmark import counts_from_summary
from experiments.intervention_pipeline import make_index
from experiments.intervention_site_controls import run_yahoo_control


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    content = args.source.read_bytes()
    report = json.loads(gzip.decompress(content) if args.source.suffix == '.gz' else content)
    cfg = report['config']
    parent_id = report['metadata']['run_id']
    files = ['tools/extend_ideal_yahoo_analysis.py', 'core/ideal_distribution.py',
             'experiments/intervention_site_controls.py', 'policy/yahoo_japan.py',
             'policy/intervention_response.py', 'policy/user_response.py',
             'ai/pcfg_monte_carlo.py', 'core/distribution_analysis.py']
    identity = {'extension_protocol': 'historical-ideal-yahoo-analysis-v1',
                'parent_run_id': parent_id,
                'parent_report_sha256': hashlib.sha256(content).hexdigest(),
                'sources': {f: hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in files},
                'metric': METRIC}
    run_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
    directory = ROOT/'reports'/'intervention'/run_id
    directory.mkdir(parents=True, exist_ok=True)
    checkpoint = directory/'yahoo.checkpoint.json'
    progress = lambda message: print(message, flush=True)
    started = time.perf_counter()
    if checkpoint.is_file():
        payload = json.loads(checkpoint.read_text(encoding='utf-8'))
        if payload['identity'] != identity:
            raise ValueError('Checkpoint identity mismatch')
        progress('复用已完成且来源核验一致的 Yahoo! 对照')
    else:
        data = cfg['data']
        source = Path(data['path'])
        if not source.is_absolute():
            source = ROOT/source
        dataset = load_registration(source, source_format=data['format'], encoding=data['encoding'],
                                    users=data['users'], development=data['development'],
                                    seed=cfg['seed'], cohort_size=data['users'], progress=progress)
        for key in ('source_sha256', 'registration_order_sha256', 'development_hashes'):
            if dataset['metadata'][key] != report['dataset'][key]:
                raise ValueError(f'历史样本来源不一致：{key}')
        initial = Population([w for batch in dataset['cohorts'] for w in batch])
        if initial.fingerprint() != report['baseline']['state_sha256']:
            raise ValueError('历史初始分布指纹不一致')
        train = dataset['development']['train']
        progress('重建同一固定 F 模型并核对历史原始命中及覆盖')
        evaluator = InterventionRisk(make_index(train, cfg), cfg['budgets'], cfg['risk_budget'])
        actual = evaluator.evaluate(initial.counts())
        fields = ('budget', 'rate', 'hits', 'target_weight', 'outside_model_support_weight',
                  'low_sample_support_weight')
        for old, new in zip(report['baseline']['risk']['minauto'], actual['minauto'], strict=True):
            if any(old[field] != new[field] for field in fields):
                raise ValueError('历史固定 F 模型评估无法复现，不能混合展示')
        reference_words = [w for w, n in sorted(train.items()) for _ in range(n)]
        control = run_yahoo_control(initial, reference_words, evaluator, cfg, progress)
        progress('拟合 Yahoo! JAPAN 终态分布并复核采样误差')
        fit = sampling_diagnostics(counts_from_summary(control['final']['distribution']), seed=cfg['seed'])
        payload = {'identity': identity, 'control': control, 'fit': fit,
                   'simulation_seconds': time.perf_counter()-started,
                   'reproduction_verified': True}
        checkpoint.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    report['site_controls'] = {'yahoo_japan': payload['control']}
    report['distribution_fits']['results'] = [
        row for row in report['distribution_fits']['results'] if row['key'] != 'yahoo_japan'] + [
        {'key': 'yahoo_japan', 'label': payload['control']['label'], 'status': 'complete', 'fit': payload['fit']}]
    attach_ideal_analysis(report)
    report['metadata']['run_id'] = run_id
    report['metadata']['analysis_extension'] = {
        **identity, 'dynamic_execution_reused': True,
        'parent_protocol': report['metadata']['protocol'],
        'reproduction_verified': payload['reproduction_verified'],
        'yahoo_simulation_seconds': payload['simulation_seconds'],
        'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    # Keep historical schema, config, decision metric and source manifest intact.
    from web.intervention_presentation import render_intervention_html, export_intervention_figures
    html = render_intervention_html(report)
    export_intervention_figures(report, directory)
    encoded = (json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode('utf-8')
    (directory/'report.html').write_text(html, encoding='utf-8')
    (directory/'report.json').write_bytes(encoded)
    (directory/'report.json.sha256').write_text(hashlib.sha256(encoded).hexdigest()+'  report.json\n', encoding='utf-8')
    if args.publish:
        published = ROOT/'published'/'intervention'
        snapshot = published/run_id
        snapshot.mkdir(parents=True, exist_ok=True)
        compressed = gzip.compress(encoded, mtime=0)
        (snapshot/'report.json.gz').write_bytes(compressed)
        (snapshot/'report.json.gz.sha256').write_text(hashlib.sha256(compressed).hexdigest()+'  report.json.gz\n', encoding='utf-8')
        for path in [directory/'report.html', *directory.glob('*.svg')]:
            (snapshot/path.name).write_bytes(path.read_bytes())
        manifest = {'run_id': run_id, 'users': cfg['data']['users'], 'google_start': True,
                    'dynamic_rounds': report['google_round_zipf']['experimental']['rounds_completed'],
                    'candidate_pool': cfg['controller']['candidate_pool'],
                    'comparison_arms': 5, 'endpoint_arms': 6,
                    'protocol': report['metadata']['protocol'], 'analysis_metric': METRIC,
                    'parent_run_id': parent_id, 'dynamic_execution_reused': True,
                    'site_controls': ['yahoo_japan']}
        (published/'latest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    progress('完成：'+str(directory))
    progress(json.dumps({'yahoo': payload['control']['target_audit'],
                         'distance': payload['control']['final']['ideal_distance']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
