"""Audit completed evidence, including budgets and source/checksum binding."""
import argparse
import hashlib
import json
import math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def audit(path):
    r=json.loads(path.read_text(encoding='utf-8'))
    assert r['status']=='complete'
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    assert path.with_suffix('.json.sha256').read_text().strip()==digest
    cfg=r['configuration']['intervention'];opt=r['configuration']['learning']
    n=cfg['data']['users'];c=cfg['controller']
    budget=math.floor(n*c['total_fraction']+1e-9)
    per_round=math.floor(n*c['round_fraction']+1e-9)
    for name,a in r['arms'].items():
        assert abs(a['initial_distance']-r['start_distance'])<1e-12
        ledger=a['ledger'];notified=ledger['adaptive_affected']
        assert 0<=notified<=budget
        assert notified==ledger['adaptive_notification_events']
        assert ledger['accounts']==n
        for attack in ('F','A1'):
            if a[attack] is None:continue
            assert a[attack]['budget_unit']=='raw_attempts_per_model'
            for point in a[attack]['minauto']:
                assert point['target_weight']==n and point['complete'] and not point['estimated']
                assert 0<=point['rate']<=1
                assert abs(point['rate']-point['hits']/n)<1e-12
                assert point['charged_attempts']<=2*point['budget']
        if name=='legacy':continue
        selected=0
        for row in a['rounds']:
            checks=row['audit']
            assert checks['screen_stream']!=checks['validation_stream']
            assert checks['simulation_calls'] <= (opt['shortlist']*opt['screening_repeats']+
                                                  opt['finalists']*opt['validation_repeats'])
            if row['issued']:
                size=row['action']['selected'];selected+=size
                assert 1<=size<=per_round
                assert row['gate']['accepted']
                assert row['gate']['individual_passed']==size
        assert selected==notified
    mismatches=[f for f,h in r['source_hashes'].items()
                if hashlib.sha256((ROOT/f).read_bytes()).hexdigest()!=h]
    return {'path':str(path.resolve()),'accounts':n,'seed':cfg['seed'],
            'arms':len(r['arms']),'budget_and_gate_audit':'PASS',
            'source_hash_mismatches':mismatches}


def main():
    p=argparse.ArgumentParser();p.add_argument('reports',nargs='+',type=Path)
    p.add_argument('--output',type=Path)
    args=p.parse_args();results=[audit(path) for path in args.reports]
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(results,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps(results,indent=2,ensure_ascii=False))


if __name__=='__main__':main()
