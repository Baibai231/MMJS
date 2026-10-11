"""Audit provenance, denominators, shared-profile comparisons and figure data."""
from __future__ import annotations

from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    study = ROOT / 'reports/privacy_joint_minimal_20261011'
    assets = ROOT / 'docs/privacy_joint_assets_20261011'
    manifest = json.loads((study / 'manifest.json').read_text('utf-8'))
    cfg = manifest['config']
    summary = json.loads((study / 'summary.json').read_text('utf-8'))
    checks = []
    assert digest(ROOT / 'configs/privacy_joint_minimal.json') == manifest['config_sha256']
    for name, value in manifest['implementation_sha256'].items():
        assert digest(ROOT / name) == value, name
    assert digest(Path(manifest['attack_prefix_path'])) == manifest['attack_prefix_sha256']
    assert digest(ROOT / cfg['source_study'] / 'private/partition.pkl.gz') == manifest['source_partition_sha256']
    checks.append('Configuration, implementation, source partition and frozen attacker hashes match')
    rows = read_csv(study / 'profile_results.csv')
    expected = len(cfg['epsilons']) * cfg['noise_repetitions'] * 3 * len(cfg['candidates'])
    assert len(rows) == expected
    keys = [(r['method'], r['epsilon'], r['repetition'], r['k'], r['j']) for r in rows]
    assert len(set(keys)) == expected
    by_key = {key: r for key, r in zip(keys, rows)}
    for r in rows:
        n = manifest['historical_occurrences']
        error = abs(float(r['predicted_users']) - float(r['true_users']))
        np.testing.assert_allclose(error, float(r['absolute_error_users']), atol=1e-10)
        np.testing.assert_allclose(error * 100 / n, float(r['absolute_error_pp']), atol=1e-10)
        assert 0 <= float(r['true_users']) <= n
        assert 0 <= float(r['redundant_exact_slots_fraction']) <= 1
        if r['method'] == 'C':
            assert float(r['overlap_users']) == 0 and float(r['redundant_exact_slots_fraction']) == 0
            match = by_key[('B', r['epsilon'], r['repetition'], r['k'], r['j'])]
            assert match['actual_shape_size'] == r['actual_shape_size']
            assert match['numeric_profile_ms'] == r['numeric_profile_ms']
    for epsilon in cfg['epsilons']:
        for method in ['A', 'B', 'C']:
            actual = np.mean([float(r['absolute_error_pp']) for r in rows if float(r['epsilon']) == epsilon and r['method'] == method])
            np.testing.assert_allclose(actual, summary['profile'][str(epsilon)][method]['absolute_error_pp'])
    checks.append('1080 unique profile outcomes: denominators, summary means and B/C shared-profile comparison match')
    references = read_csv(study / 'profile_references.csv')
    ref = {(r['method'], r['k'], r['j']): r for r in references}
    for k, j in cfg['candidates']:
        independent = ref[('O_independent', str(k), str(j))]
        residual = ref[('O_residual', str(k), str(j))]
        assert float(residual['true_users']) >= float(independent['true_users'])
        assert float(independent['absolute_error_pp']) < 1e-12
        assert float(residual['absolute_error_pp']) < 1e-12
    checks.append('Noiseless conditional representation is exact; residual coverage never below independent reference')
    registrations = read_csv(study / 'registration_results.csv')
    assert len(registrations) == 3 * cfg['noise_repetitions'] + 3
    for r in registrations:
        total = int(r['initial_users'])
        completed = int(r['completed_users'])
        assert completed + int(r['pending_users']) == total == cfg['registration_users']
        np.testing.assert_allclose(int(r['attack_hits']) / completed, float(r['hit_rate_per_completed']))
        np.testing.assert_allclose(int(r['attack_hits']) / total, float(r['hit_rate_per_initial']))
        eligible = (float(r['modification_rate']) <= manifest['cost_limits']['max_modification'] and
                    float(r['mean_extra_submissions']) <= manifest['cost_limits']['max_extra'] and int(r['pending_users']) == 0)
        assert eligible == (r['cost_eligible'] == 'True')
    for method, values in summary['registration'].items():
        selected = [r for r in registrations if r['method'] == method]
        for metric in ['hit_rate_per_completed', 'modification_rate', 'completed_users', 'pending_users', 'mean_extra_submissions']:
            np.testing.assert_allclose(np.mean([float(r[metric]) for r in selected]), values[metric])
    checks.append('63 registration outcomes: completed/initial denominators and unchanged cost constraints match')
    fig_manifest = json.loads((assets / 'figure_manifest.json').read_text('utf-8'))
    for name, value in fig_manifest['source_hashes'].items():
        assert digest(study / name) == value
    for row in read_csv(assets / 'figure_source_data.csv'):
        if row['figure'] == '1':
            selected = [r for r in rows if r['method'] == row['method'] and float(r['epsilon']) == float(row['epsilon']) and
                        (row['panel'] == '1' or int(r['j']) > 0)]
            per_repeat = defaultdict(list)
            for r in selected:
                per_repeat[r['repetition']].append(float(r[row['metric']]) * (100 if row['panel'] == '2' else 1))
            v = [np.mean(values) for values in per_repeat.values()]
        else:
            v = [100 * float(r[row['metric']]) for r in registrations if r['method'] == row['method']]
        np.testing.assert_allclose(np.mean(v), float(row['mean']))
        np.testing.assert_allclose(np.quantile(v, [0.1, 0.9]), [float(row['p10']), float(row['p90'])])
    checks.append('Figure means and empirical 10-90 percentiles match recorded outcomes')
    report = ROOT / 'docs/第三板块_隐私保护口令统计与联合策略报告.md'
    text = report.read_text('utf-8')
    assert text.count('```') % 2 == 0 and sum(line.strip() == '$$' for line in text.splitlines()) % 2 == 0
    assert '\\(' not in text and '\\[' not in text
    assert not any(word in text for word in ['待填', '待写', 'TODO'])
    for line in text.splitlines():
        if line.strip() != '$$':
            assert line.count('$') % 2 == 0
    for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', text):
        if target.startswith(('http://', 'https://', '#')):
            continue
        assert (report.parent / target).exists(), target
    checks.append('Markdown delimiters, artifact links and absence of result placeholders verified')
    result = {'status': 'passed', 'checks': checks, 'profile_rows': len(rows), 'registration_rows': len(registrations),
              'report_sha256': digest(report), 'audit_script_sha256': digest(Path(__file__)),
              'report_claim_boundary': 'This audit covers the original algorithm utility and ideal-function simulation; added real protocol evidence is audited separately in reports/privacy_protocol_20261011/evidence_audit.json'}
    (study / 'audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
