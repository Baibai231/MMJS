"""Read-only audit of the three-arm output and its shareable artifacts."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
UNRELATED_CHANGED = {
    'experiments/htpg_iteration.py',
    'experiments/reproduce_htpg_baseline.py',
}


def verified_json(path):
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == path.with_suffix(path.suffix + '.sha256').read_text().strip()
    return json.loads(raw)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--report', type=Path, default=ROOT/'reports/contextual/seed42_100000_yahoo/comparison.json')
    p.add_argument('--markdown', type=Path, default=ROOT/'docs/十万账户_动态策略与Yahoo对照.md')
    args = p.parse_args()
    result = verified_json(args.report)
    assert result['status'] == 'complete'
    local = verified_json(args.report.parent/'local/report.json')
    yahoo = verified_json(args.report.parent/'yahoo.json')
    assert local == result['local'] and yahoo == result['yahoo']
    n = result['configuration']['intervention']['data']['users']
    assert n == 100000 and len(local['arms']) == 2 and set(local['arms']) == {'legacy', 'bandit'}
    assert yahoo['target_audit']['noncompliant_remaining'] == 0
    assert yahoo['final']['ledger']['accounts'] == n
    for arm in list(local['arms'].values()) + [dict(F=yahoo['attacks']['F'], A1=yahoo['attacks']['A1'])]:
        if 'ledger' in arm:
            assert arm['ledger']['accounts'] == n
            assert arm['ledger']['adaptive_affected'] <= .2*n
        for name in ('F', 'A1'):
            for point in arm[name]['minauto']:
                assert point['target_weight'] == n and point['complete'] and not point['estimated']
                assert point['charged_attempts'] <= 2*point['budget']
                assert abs(point['rate']-point['hits']/n) < 1e-12
    mismatches = []
    for path, recorded in local['source_hashes'].items():
        if hashlib.sha256((ROOT/path).read_bytes()).hexdigest() != recorded:
            mismatches.append(Path(path).as_posix())
    assert set(mismatches) <= UNRELATED_CHANGED, f'Additional source hash changes: {mismatches}'
    body = args.markdown.read_text(encoding='utf-8')
    assert len(re.findall(r'^```', body, re.M)) % 2 == 0
    assert len(re.findall(r'^\$\$$', body, re.M)) % 2 == 0
    assert body.replace('$$', '').count('$') % 2 == 0
    assert body.count('**') % 2 == 0
    for path in re.findall(r'!?\[[^]]+\]\(([^)]+)\)', body):
        if '://' not in path:
            assert (args.markdown.parent/path).is_file(), path
    for ext in ('png', 'svg'):
        assert (ROOT/'docs/figures'/f'comparison_100k.{ext}').stat().st_size > 10000
    print(json.dumps({'status': 'PASS', 'accounts': n, 'arms': ['legacy', 'bandit', 'yahoo_japan'],
                      'source_hash_mismatches': mismatches,
                      'source_hash_scope': 'Only two independent historical HTPG scripts differ',
                      'markdown_and_figure': 'PASS'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
