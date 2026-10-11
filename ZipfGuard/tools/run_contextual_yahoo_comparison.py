"""Resumable three-arm evaluation; private samples stay in ignored reports/."""
import argparse
from collections import Counter
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
from experiments.contextual_config import validate_config
from experiments.contextual_pipeline import run_contextual, expand, write_json
from experiments.intervention_pipeline import make_index
from experiments.intervention_site_controls import run_yahoo_control
from experiments.cdf_fit_benchmark import counts_from_summary
from policy.contextual_controller import distance
from audit_contextual_results import audit


def save(path, value):
    write_json(path, value)
    path.with_suffix(path.suffix+'.sha256').write_text(
        hashlib.sha256(path.read_bytes()).hexdigest()+'\n', encoding='ascii')


def read_verified(path):
    assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_suffix(
        path.suffix+'.sha256').read_text().strip()
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', type=Path, default=ROOT/'configs/contextual_yahoo_100k.json')
    p.add_argument('--output-dir', type=Path, required=True)
    args = p.parse_args()
    cfg = json.loads(args.config.read_text(encoding='utf-8'))
    validated = validate_config(cfg)
    c = validated['intervention']; d = c['data']
    out = args.output_dir.resolve(); out.mkdir(parents=True, exist_ok=True)
    if (out/'comparison.json').exists():
        raise FileExistsError('Completed comparison exists; choose a new directory')
    log = lambda msg: print(msg, flush=True)
    source = Path(d['path']); source = (ROOT/source).resolve() if not source.is_absolute() else source
    stat = source.stat()
    binding = {'configuration': cfg, 'source': str(source),
               'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
    cached = out/'private_sample.json'
    if cached.exists():
        cache = read_verified(cached)
        assert cache['binding'] == binding, 'Sample/configuration binding differs'
        dataset = cache['dataset']; log('Resuming exact private sample')
    else:
        dataset = load_registration(source, source_format=d['format'], encoding=d['encoding'],
                                    users=d['users'], development=d['development'], seed=c['seed'],
                                    cohort_size=d['users'], progress=log)
        save(cached, {'binding': binding, 'dataset': dataset})
    train = Counter(dataset['development']['train'])
    index = make_index(train, c)
    local_path = out/'local'/'report.json'
    if local_path.exists():
        local = read_verified(local_path)
        assert not audit(local_path)['source_hash_mismatches'], 'Local source changed since run'
        assert local['dataset'] == dataset['metadata']
        expected = validate_config(cfg)
        actual = json.loads(json.dumps(local['configuration']))
        actual['intervention']['controller'].pop('policy_floor_minimum_length', None)
        assert actual == expected
    else:
        local = run_contextual(cfg, out/'local', progress=log, dataset=dataset, index=index)
    yahoo_path = out/'yahoo.json'
    raw = Population([w for batch in dataset['cohorts'] for w in batch], 'account')
    if yahoo_path.exists():
        yahoo = read_verified(yahoo_path)
    else:
        tick = time.perf_counter()
        yahoo = run_yahoo_control(raw, expand(train),
                                  InterventionRisk(index, c['budgets'], c['risk_budget']), c, log)
        yahoo['runtime_seconds'] = time.perf_counter()-tick
        values = counts_from_summary(yahoo['final']['distribution'])
        yahoo['final_distance'] = distance(dict(enumerate(int(v) for v in values)))
        save(yahoo_path, yahoo)
    assert yahoo['starting_state_sha256'] == raw.fingerprint()
    assert yahoo['target_audit']['noncompliant_remaining'] == 0
    assert yahoo['final']['ledger']['accounts'] == d['users']
    for attack in yahoo['attacks'].values():
        assert attack is not None
        for point in attack['minauto']:
            assert point['target_weight'] == d['users'] and point['complete'] and not point['estimated']
            assert abs(point['rate']-point['hits']/d['users']) < 1e-12
            assert point['charged_attempts'] <= 2*point['budget']
    local_audit = audit(local_path)
    assert not local_audit['source_hash_mismatches'], 'Local source changed since run'
    checks = {'local': local_audit, 'yahoo': 'PASS: population, compliance, attack accounting, checksum'}
    save(out/'comparison.json', {'status': 'complete', 'configuration': validated,
         'dataset': dataset['metadata'], 'local': local, 'yahoo': yahoo, 'audit': checks,
         'raw_start_hash': raw.fingerprint(),
         'scope': 'One paired raw sample; local methods share 8-character baseline and 20% notification cap; Yahoo whole-site migration from raw sample without local cap; simulated successful responses'})
    for name, arm in local['arms'].items():
        log(f"{name}: D={arm['final_distance']}, notified={arm['ledger']['adaptive_affected']}, A1={arm['A1']['minauto'][-1]['rate']}")
    log(f"Yahoo: D={yahoo['final_distance']}, changed={yahoo['final']['ledger']['changed']}, A1={yahoo['attacks']['A1']['minauto'][-1]['rate']}")


if __name__ == '__main__':
    main()
