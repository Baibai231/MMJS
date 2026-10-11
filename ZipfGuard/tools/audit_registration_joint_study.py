"""Read-only denominator, data-role, frozen-selection and budget audit."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from experiments.registration_joint_data import load_private


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def audit(root, require_complete=False):
    root = Path(root).resolve()
    suite = json.loads((root/'suite_status.json').read_text('utf-8'))
    if require_complete:
        check(suite['state']=='complete','Suite has not completed')
    plan = json.loads((root/'suite_plan.json').read_text('utf-8'))
    result_count = screened = missing = 0
    studies = {}
    for name,cfg in plan.items():
        directory = root if name=='main' else root/name
        if not (directory/'manifest.json').exists():
            studies[name]={'state':'not_started'}
            continue
        manifest = json.loads((directory/'manifest.json').read_text('utf-8'))
        for filename,digest in manifest['implementation_sha256'].items():
            check(hashlib.sha256((ROOT/filename).read_bytes()).hexdigest()==digest,f'Implementation changed: {filename}')
        if not (directory/'private/partition.pkl.gz').exists():
            studies[name]={'state':'partitioning'}
            continue
        data = load_private(directory/'private/partition.pkl.gz')
        ids = [i for records in data['roles'].values() for i,_ in records]
        check(len(ids)==len(set(ids)),f'{name}: data-role occurrence overlap')
        for filename in (directory/'frozen_parameters').glob('*.json'):
            frozen = json.loads(filename.read_text('utf-8'))
            check(frozen['test_data_used'] is False,'Final data used in comparator selection')
        for filename in (directory/'frozen_recommendations').glob('*.json'):
            frozen = json.loads(filename.read_text('utf-8'))
            check(frozen['test_data_used'] is False,'Final data used in recommendation')
        count = 0
        for filename in (directory/'runs').rglob('result.json'):
            r = json.loads(filename.read_text('utf-8'))
            count += 1
            if r.get('cost_is_lower_bound'):
                check(r.get('registrations_executed_for_this_arm') is False,'Screening mislabeled as execution')
                check(r['A1_MC'] is None,'Screened rule has fabricated attack rate')
                screened += 1
                continue
            check(r['cost']['initial_users']==cfg['users'],f'{filename}: initial-user denominator mismatch')
            check(r['cost']['completed_users']+r['cost']['pending_users']==cfg['users'],'Completed/pending mass mismatch')
            check(sum(s['initial_users'] for s in r['cohort_costs'])==cfg['users'],'Cohort user total mismatch')
            first = r['timeline'][0]
            check(first['k']==first['j']==first['strength_threshold']==0,'First cohort did not share base')
            for key in ['A1_MC','F_MC']:
                mc = r.get(key)
                if mc:
                    check(mc['initial_users']==cfg['users'],'MC denominator mismatch')
                    check(mc['budget_unit']=='estimated_distinct_string_probability_rank','MC/raw budget mixed')
                    for b,hits in mc['hits'].items():
                        check(abs(mc['rates'][b]-hits/cfg['users'])<1e-12,'MC rates do not match hits')
                    check(0<=mc['outside_model_support_fraction']<=1,'Invalid support fraction')
            for model in ['pcfg','omen']:
                actual = r.get('actual_attacks',{}).get(model)
                if actual:
                    check(actual['budget_unit']=='actual_attempts_including_duplicates','Actual prefix budget mislabeled')
                    for p in actual['points']:
                        check(abs(p['rate_per_initial_user']-p['hits']/cfg['users'])<1e-12,'Prefix denominator mismatch')
                        check(p['charged_attempts']<=p['budget'],'Attempt budget exceeded')
            missing += bool(r.get('attack_failures'))
        studies[name]={'result_records':count,'data_role_overlap':False}
        result_count += count
    return {'suite_state':suite['state'],'studies':studies,'result_records':result_count,
            'screened_without_execution':screened,'records_with_explicit_missing_attacks':missing,
            'audit':'passed; partial-state audit does not certify completion'}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('root')
    parser.add_argument('--require-complete',action='store_true')
    args=parser.parse_args()
    print(json.dumps(audit(args.root,args.require_complete),ensure_ascii=False,indent=2))
