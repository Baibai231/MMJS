"""Append one actual hybrid run to a verified study, preserving existing arms."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_published_intervention import save_json, publish, audit_report
from experiments.intervention_hybrid import METHOD, VERSION, run_hybrid
from experiments.intervention_pipeline import make_index, snapshot
from core.registration import load_registration
from core.intervention_state import Population
from core.intervention_risk import InterventionRisk
from core.intervention_attack_area import curve_area
from core.ideal_distribution import attach_ideal_analysis
from core.distribution_analysis import sampling_diagnostics
from experiments.cdf_fit_benchmark import counts_from_summary
from web.intervention_presentation import render_intervention_html, export_intervention_figures


def comparison_summary(report):
    baseline = report['baseline']['ideal_distance']
    yahoo = report['site_controls']['yahoo_japan']
    hybrid = report['site_controls'][METHOD]
    distances = {}
    for key in ('empirical', 'fitted'):
        b, y, h = (state[key] for state in (baseline, yahoo['final']['ideal_distance'], hybrid['final']['ideal_distance']))
        improvement = b-y
        distances[key] = {'yahoo': y, 'hybrid': h, 'hybrid_minus_yahoo': h-y,
                         'yahoo_improvement_retained': (b-h)/improvement if improvement > 0 else None,
                         'within_exploratory_95pct': h <= y+.05*max(0, improvement)}
    attacks = {}
    for level in ('F', 'A1'):
        y, h = yahoo['attacks'][level], hybrid['attacks'][level]
        ya, ha = curve_area(y), curve_area(h)
        attacks[level] = {'yahoo_union_area': ya, 'hybrid_union_area': ha,
                         'area_gap': ha-ya, 'within_exploratory_half_pp_area': ha <= ya+.005,
                         'yahoo_union_endpoint': y['minauto'][-1]['rate'],
                         'hybrid_union_endpoint': h['minauto'][-1]['rate']}
    return {'distribution': distances, 'attacks': attacks,
            'interpretation': '预先给定的描述性工程容差；单次模拟，不是统计等效或网站上线标准'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--parent', default='3a83057341104cfc')
    parser.add_argument('--strong-fraction', type=float, default=.30)
    args = parser.parse_args()
    if not 0 < args.strong_fraction < 1:
        parser.error('strong-fraction must be strictly between 0 and 1')
    parent_path = ROOT/'reports'/'intervention'/args.parent/'report.json'
    parent_bytes = parent_path.read_bytes()
    report = json.loads(parent_bytes)
    cfg = report['config']
    # Existing decision code must still match the parent experiment, otherwise
    # comparisons silently mix controller versions.
    for file, expected in report['metadata']['source_hashes'].items():
        assert hashlib.sha256((ROOT/file).read_bytes()).hexdigest() == expected, file
    files = ['experiments/intervention_hybrid.py', 'tools/run_hybrid_intervention.py',
             'web/intervention_presentation.py', 'web/dual_intervention_presentation.py']
    hashes = {f: hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in files}
    extension = {'parent_run_id': args.parent, 'parent_report_sha256': hashlib.sha256(parent_bytes).hexdigest(),
                 'hybrid_protocol': VERSION, 'strong_fraction': args.strong_fraction,
                 'source_hashes': hashes, 'existing_arms_reused': True,
                 'hybrid_execution_reused': False,
                 'exploratory_tolerance': {'retained_W1_improvement': .95, 'union_log_area_gap': .005}}
    run_id = hashlib.sha256(json.dumps(extension, sort_keys=True).encode()).hexdigest()[:16]
    work = ROOT/'reports'/'intervention_jobs'/('hybrid-'+run_id)
    work.mkdir(parents=True, exist_ok=True)
    state = {'status': 'running', 'run_id': run_id, 'users': cfg['data']['users'],
             'protocol': VERSION, 'started_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    started = time.perf_counter()
    def progress(message):
        state.update(message=message, updated_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
        save_json(work/'status.json', state)
        save_json(ROOT/'reports'/'active_intervention.json', state)
        print(state['updated_at'], message, flush=True)
    def checkpoint(method, payload):
        save_json(work/(method+'.json'), payload)
    original_main = deepcopy(report['google_round_zipf'])
    original_arms = deepcopy(report['arms'])
    original_yahoo = deepcopy(report['site_controls']['yahoo_japan'])
    try:
        complete = work/'hybrid_complete.json'
        if complete.exists():
            progress('读取本次混合组已完成的计算记录，继续核验和发布')
            hybrid = json.loads(complete.read_text(encoding='utf-8'))
        else:
            progress('恢复同一 10 万账户抽样和固定 F 模型；只计算新增混合组')
            d = cfg['data']
            path = Path(d['path'])
            data = load_registration(path if path.is_absolute() else ROOT/path,
                source_format=d['format'], encoding=d['encoding'], users=d['users'],
                development=d['development'], seed=cfg['seed'], cohort_size=d['users'], progress=progress)
            assert data['metadata'] == report['dataset'], 'Dataset differs from parent'
            train = data['development']['train']
            reference_words = [w for w, count in sorted(train.items()) for _ in range(count)]
            initial = Population([w for batch in data['cohorts'] for w in batch])
            evaluator = InterventionRisk(make_index(train, cfg), cfg['budgets'], cfg['risk_budget'])
            start = snapshot(initial, evaluator, 0)
            assert start['account_state_sha256'] == report['baseline']['account_state_sha256']
            assert start['risk'] == report['baseline']['risk'], 'Frozen F differs from parent'
            hybrid = run_hybrid(initial, reference_words, evaluator, cfg, fraction=args.strong_fraction,
                                progress=progress, checkpoint=checkpoint)
            save_json(complete, hybrid)
        report['site_controls'][METHOD] = hybrid
        progress('拟合新增终态，核验 Yahoo 分组与动态阶段预算，生成对照图')
        for key in ('yahoo_japan', METHOD):
            if any(r['key'] == key for r in report['distribution_fits']['results']):
                continue
            arm = report['site_controls'][key]
            fit = sampling_diagnostics(counts_from_summary(arm['final']['distribution']), seed=cfg['seed'])
            report['distribution_fits']['results'].append({'key': key, 'label': arm['label'],
                                                         'status': 'complete', 'fit': fit})
        attach_ideal_analysis(report)
        report['hybrid_comparison'] = comparison_summary(report)
        report['metadata'].update(run_id=run_id, hybrid_extension=extension,
            created_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            extension_runtime_seconds=time.perf_counter()-started)
        # Only new diagnostics are permitted on preserved historical results.
        def without_ideal(value):
            if isinstance(value, dict):
                return {k: without_ideal(v) for k, v in value.items() if k != 'ideal_distance'}
            if isinstance(value, list):
                return [without_ideal(v) for v in value]
            return value
        assert without_ideal(original_main) == without_ideal(report['google_round_zipf'])
        assert without_ideal(original_arms) == without_ideal(report['arms'])
        assert without_ideal(original_yahoo) == without_ideal(report['site_controls']['yahoo_japan'])
        validation = audit_report(report)
        audit, design = hybrid['target_audit'], hybrid['hybrid_design']
        assert audit['strong_assigned'] <= int(cfg['data']['users']*args.strong_fraction+1e-9)
        assert audit['strong_noncompliant'] == 0
        assert audit['local_modified'] <= design['total_local_cap']
        assert audit['total_distinct_modified'] <= audit['strong_modified']+audit['baseline_modified']+audit['local_modified']
        assert hybrid['attacks']['A1'] is not None
        for row in hybrid['rounds']:
            guard = row['aggregate_attack_guard']
            assert guard['individual_passed'] == guard['individual_total'] == row['changed']
            assert row['changed'] <= design['round_cap']
        validation['hybrid'] = {'passed': True, 'original_arms_preserved': True,
                               'costs': audit, 'design': design}
        destination = ROOT/'reports'/'intervention'/run_id
        destination.mkdir(parents=True, exist_ok=True)
        (destination/'report.html').write_text(render_intervention_html(report), encoding='utf-8')
        export_intervention_figures(report, destination)
        save_json(destination/'validation.json', validation)
        save_json(destination/'report.json', report)
        (destination/'report.json.sha256').write_text(hashlib.sha256((destination/'report.json').read_bytes()).hexdigest()+'  report.json\n', encoding='utf-8')
        publish(report)
        state['status'] = 'complete'
        progress('混合组已完成，Yahoo! 结果区已更新')
        print(json.dumps(report['hybrid_comparison'], ensure_ascii=False, indent=2), flush=True)
    except BaseException as exc:
        state['status'] = 'failed'
        progress('混合实验未完成：'+str(exc))
        raise


if __name__ == '__main__':
    main()
