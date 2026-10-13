"""Protected-statistics decision utility: production numeric adapter, offline oracle.

No production state is read or changed. Passwords remain in the evaluator; the
administrator adapter receives only numeric protected profiles. Seeded noise is
for reproducible testing, not production privacy randomness.
"""
from __future__ import annotations
import argparse, csv, gzip, hashlib, json, pickle, platform, sys, time
from collections import Counter
from decimal import Decimal, localcontext, ROUND_CEILING
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from adapters.live_engines import registration_candidates
from privacy.service.compute import SHAPES, SHAPE_VERSION
from policy.privacy_joint_profile import (simplex_projection, decreasing_projection,
    choose_shapes, choose_positions, predict_conditional, predict_marginal)
from policy.registration_joint import JointRule, Preferences, stable_seed
from tools.run_privacy_joint_study import IdealHiddenState

METHODS = ('marginal', 'TriGuard', 'exact_reference')

def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def save_json(p, value):
    Path(p).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')

def write_csv(p, rows):
    with Path(p).open('w', newline='', encoding='utf-8-sig') as h:
        w=csv.DictWriter(h, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

def q_parameter(epsilon, sensitivity=4):
    with localcontext() as context:
        context.prec=100
        return int(((-Decimal(str(epsilon))/Decimal(sensitivity)).exp()*Decimal(1<<64)).to_integral_value(rounding=ROUND_CEILING))

def geometric(rng, q, shape):
    # The same rational Bernoulli continuation probability as Computer.noisy.
    values=np.zeros(shape, dtype=np.int64); active=np.arange(values.size)
    flat=values.reshape(-1)
    while len(active):
        active=active[rng.bit_generator.random_raw(len(active)) < np.uint64(q)]
        flat[active]+=1
        if np.any(flat[active] >= 1<<20):
            raise OverflowError('bounded noise overflow: no release')
    return values

def party_noise(rng, q, shape, parties):
    out=np.zeros(shape, dtype=np.int64)
    for _ in range(parties):
        out+=geometric(rng,q,shape)-geometric(rng,q,shape)
    return out

def post_conditional(counts, raw):
    spectra=np.stack([decreasing_projection(row) for row in raw])
    for i,line in enumerate(spectra):
        if line.sum()>counts[i] and line.sum()>0:
            line*=counts[i]/line.sum()
    return spectra

def exact_metrics(state, selected_shapes, positions=None, global_ranks=None):
    if global_ranks is None:
        words={state.owners[s][r] for s,r in positions}
        requested=len(positions)
    else:
        words={state.global_owners[r] for r in global_ranks}
        requested=len(global_ranks)
    words.discard(None)
    shape_users=sum(int(state.shape_counts[s]) for s in selected_shapes)
    overlap=sum(state.counts[w] for w in words if state.word_shape[w] in selected_shapes)
    extra=sum(state.counts[w] for w in words if state.word_shape[w] not in selected_shapes)
    # Independent direct union count; never use the noisy prediction as truth.
    union=sum(n for w,n in state.counts.items() if w in words or state.word_shape[w] in selected_shapes)
    assert union==shape_users+extra
    return dict(true_users=union,structure_users=shape_users,extra_users=extra,
        overlap_users=overlap,actual_exact_size=len(words),selected_slots=requested,
        redundant_slots=sum(state.word_shape[w] in selected_shapes for w in words),
        padded_slots=requested-len(words))

def get_candidate(profile, k, j):
    candidate=registration_candidates(profile, {'minimum':8,'maximum':64},k,j)[-1]
    assert candidate['decision']=='hold' and candidate['eligible'] is False
    policy=candidate['policy']
    return tuple(policy['shapes']),tuple(tuple(p) for p in policy['positions']),candidate['coverage_estimate']

def fixture_checks():
    checks=[]
    fixtures=[(Counter({'aaaaaaaa':80,'bbbbbbbb':20,'11111111':60,'22222222':40}),100,160),
              (Counter({'aaaaaaaa':60,'bbbbbbbb':40,'11111111':80,'22222222':20}),180,180)]
    for number,(counts,expected_baseline,expected_triguard) in enumerate(fixtures):
        state=IdealHiddenState(counts,SHAPES,3)
        profile={'counts':state.shape_counts.tolist(),'spectra':state.spectra.tolist(),'population':200,'snapshot':f'fixture-{number}'}
        shapes,positions,prediction=get_candidate(profile,1,1)
        assert exact_metrics(state,shapes,positions=positions)['true_users']==expected_triguard
        assert prediction==expected_triguard
        assert exact_metrics(state,shapes,global_ranks=(0,))['true_users']==expected_baseline
        assert all(s not in shapes for s,r in positions)
    checks.append('Two analytically known fixtures: same marginal populations/spectra, different overlap; 100->160 and 180->180 union counts')
    return checks

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',default='configs/protected_statistics_decision_utility.json')
    args=parser.parse_args(); config_path=ROOT/args.config; cfg=json.loads(config_path.read_text('utf-8'))
    output=Path(cfg['output']);output.mkdir(parents=True,exist_ok=True)
    start=time.perf_counter();save_json(output/'status.json',{'state':'running'})
    checks=fixture_checks()
    source=ROOT/cfg['source_study']; partition=source/'private/partition.pkl.gz'
    assert digest(partition)==cfg['expected_partition_sha256'],'Frozen source partition mismatch'
    with gzip.open(partition,'rb') as h: data=pickle.load(h)
    source_cfg=json.loads((source/'manifest.json').read_text('utf-8'))['config']
    train=data['roles']['train'];history_records=data['roles'][cfg['history_role']][:cfg['history_users']]
    assert len(train)==cfg['training_users'] and len(history_records)==cfg['history_users']
    assert not ({i for i,w in train}&{i for i,w in history_records})
    train_words=[w for i,w in train]; training_counts=Counter(train_words)
    common=frozenset(w for w,n in sorted(training_counts.items(),key=lambda p:(-p[1],p[0]))[:source_cfg['common_size']])
    base=JointRule(source_cfg['minimum'],common)
    preferences=Preferences(train_words,base.minimum,source_cfg['weights'],source_cfg['response_mode'],source_cfg['candidate_limit'])
    history_result=preferences.simulate(history_records,base,cfg['response_seed'])
    assert history_result['summary']['pending_users']==0
    state=IdealHiddenState(history_result['counts'],SHAPES,cfg['spectrum_length'])
    n=sum(state.counts.values()); assert n==cfg['history_users']
    for k,j in cfg['quota_pairs']: assert 0<k<=cfg['spectrum_length'] and 0<j<=len(SHAPES)
    implementation=[Path(__file__),ROOT/'adapters/live_engines.py',ROOT/'policy/privacy_joint_profile.py',ROOT/'privacy/service/compute.py',ROOT/'policy/registration_joint.py',ROOT/'tools/run_privacy_joint_study.py']
    manifest={'version':'protected-decision-utility-v1','config':cfg,'config_sha256':digest(config_path),
        'source_partition_sha256':digest(partition),'source_corpus_sha256':data['metadata']['source_sha256'],
        'implementation_sha256':{str(p.relative_to(ROOT)):digest(p) for p in implementation},
        'python':platform.python_version(),'numpy':np.__version__,'structure_catalogue':list(SHAPES)+['OTHER'],
        'shape_version':SHAPE_VERSION,'population':n,'historical_preprocessing':history_result['summary'],
        'known_structure_coverage':float(1-state.shape_counts[-1]/n),
        'spectrum_length':cfg['spectrum_length'],'comparison':'same protected counts, selected shapes, quota and total nominal epsilon',
        'epsilon_allocation':'80 percent to core statistics; remaining 20 percent reserved for rule-count coordinates, not used in this decision test',
        'baseline_core_allocation':'half to sensitivity-2 structure counts and half to sensitivity-2 global frequencies',
        'noise':'three independent seeded test contributions, each the difference of two exact-rational geometric samples; same q and bound as deployed Computer.noisy',
        'release_scope':'Synthetic protected snapshots generated offline; production adapter called directly; no HTTP/MPC/cryptographic protocol claims',
        'reference_scope':'Unnoised optimal complete-password selection for the SAME selected structures in each paired comparison',
        'uncertainty_scope':'noise repetitions on one fixed cohort, not population sampling or real-user behavior',
        'password_strings_in_public_outputs':False}
    save_json(output/'manifest.json',manifest)
    rows=[]; profiles=[]
    exact_profile={'counts':state.shape_counts,'spectra':state.spectra}
    for epsilon in cfg['epsilons']:
        core_epsilon=epsilon*cfg['core_budget_fraction'];q=q_parameter(core_epsilon)
        for repetition in range(cfg['noise_repetitions']):
            def rng(role):return np.random.default_rng(stable_seed(cfg['noise_seed'],epsilon,repetition,role))
            # Counts use identical noise/postprocessing across both methods.
            counts=simplex_projection(state.shape_counts+party_noise(rng('counts'),q,state.shape_counts.shape,cfg['noise_parties']),n)
            spectra=post_conditional(counts,state.spectra+party_noise(rng('conditional'),q,state.spectra.shape,cfg['noise_parties']))
            frequencies=decreasing_projection(state.global_frequencies+party_noise(rng('marginal'),q,state.global_frequencies.shape,cfg['noise_parties']))
            if frequencies.sum()>n:frequencies*=n/frequencies.sum()
            profile={'counts':counts.tolist(),'spectra':spectra.tolist(),'population':n,'snapshot':f'utility-{epsilon}-{repetition}'}
            marginal={'counts':counts,'frequencies':frequencies}
            profiles.append(dict(epsilon=epsilon,repetition=repetition,counts=counts.tolist(),spectra=spectra.tolist(),global_frequencies=frequencies.tolist()))
            for k,j in cfg['quota_pairs']:
                shapes,positions,prediction=get_candidate(profile,k,j)
                assert shapes==choose_shapes(counts,j,len(SHAPES))
                assert all(s not in shapes for s,r in positions)
                global_ranks=tuple(i for i,v in enumerate(frequencies) if v>=2)[:k]
                reference_positions=choose_positions(exact_profile,k,shapes,residual=True)
                predictions={'marginal':predict_marginal(marginal,global_ranks,shapes,n),
                    'TriGuard':prediction,'exact_reference':predict_conditional(exact_profile,reference_positions,shapes)}
                metrics={'marginal':exact_metrics(state,shapes,global_ranks=global_ranks),
                    'TriGuard':exact_metrics(state,shapes,positions=positions),
                    'exact_reference':exact_metrics(state,shapes,positions=reference_positions)}
                # Verify the reference independently against all strings, including those beyond the per-bucket prefix.
                outside=sorted((v for w,v in state.counts.items() if state.word_shape[w] not in shapes and v>=2),reverse=True)
                assert metrics['exact_reference']['extra_users']==sum(outside[:k])
                assert metrics['TriGuard']['overlap_users']==0 and metrics['TriGuard']['redundant_slots']==0
                for method in METHODS:
                    metric=metrics[method]
                    assert metric['actual_exact_size']<=k and len(shapes)<=j
                    assert metric['true_users']<=metrics['exact_reference']['true_users']
                    predicted=min(n,max(0,float(predictions[method])))
                    selected=global_ranks if method=='marginal' else positions if method=='TriGuard' else reference_positions
                    rows.append(dict(epsilon=epsilon,repetition=repetition,k=k,j=j,method=method,population=n,
                        selected_shapes=json.dumps(shapes),selected_positions=json.dumps(selected),**metric,
                        predicted_users=predicted,coverage_pct=100*metric['true_users']/n,
                        extra_coverage_pp=100*metric['extra_users']/n,
                        absolute_error_pp=100*abs(predicted-metric['true_users'])/n,
                        reference_gap_users=metrics['exact_reference']['true_users']-metric['true_users']))
        print(f'epsilon={epsilon}: 20 protected snapshots and paired decisions completed',flush=True)
    write_csv(output/'逐次决策结果.csv',rows);save_json(output/'匿名测试快照.json',profiles)
    summary_rows=[];pair_rows=[]
    for epsilon in cfg['epsilons']:
        for method in METHODS:
            subset=[r for r in rows if r['epsilon']==epsilon and r['method']==method]
            # Equal weighting of the four quota combinations; uncertainty is across repeated releases.
            rep_means=[np.mean([r['coverage_pct'] for r in subset if r['repetition']==rep]) for rep in range(cfg['noise_repetitions'])]
            summary_rows.append(dict(epsilon=epsilon,method=method,comparisons=len(subset),
                mean_coverage_pct=float(np.mean(rep_means)),p10_coverage_pct=float(np.quantile(rep_means,.1)),p90_coverage_pct=float(np.quantile(rep_means,.9)),
                mean_extra_users=float(np.mean([r['extra_users'] for r in subset])),
                mean_absolute_error_pp=float(np.mean([r['absolute_error_pp'] for r in subset])),
                mean_redundant_slots=float(np.mean([r['redundant_slots'] for r in subset])),
                mean_actual_exact_size=float(np.mean([r['actual_exact_size'] for r in subset])),
                mean_padded_slots=float(np.mean([r['padded_slots'] for r in subset])),
                mean_reference_gap_users=float(np.mean([r['reference_gap_users'] for r in subset]))))
        for k,j in cfg['quota_pairs']:
            subset=[r for r in rows if r['epsilon']==epsilon and r['k']==k and r['j']==j]
            by={(r['repetition'],r['method']):r for r in subset}
            gains=[by[(rep,'TriGuard')]['true_users']-by[(rep,'marginal')]['true_users'] for rep in range(cfg['noise_repetitions'])]
            pair_rows.append(dict(epsilon=epsilon,k=k,j=j,mean_gain_users=float(np.mean(gains)),min_gain_users=min(gains),max_gain_users=max(gains),
                positive_repetitions=sum(g>0 for g in gains),equal_repetitions=sum(g==0 for g in gains),negative_repetitions=sum(g<0 for g in gains)))
    summary={'settings':cfg,'population':n,'raw_rows':len(rows),'protected_snapshot_pairs':len(profiles),'summary':summary_rows,'quota_pairs':pair_rows,'elapsed_seconds':time.perf_counter()-start}
    save_json(output/'结果汇总.json',summary);write_csv(output/'汇总指标.csv',summary_rows);write_csv(output/'分名额覆盖收益.csv',pair_rows)
    checks+=['All production candidates use only numeric profiles and retain the automatic hold gate',
        'Both methods share exactly the same structure-count release and selected shapes',
        'Actual coverage checked by independent plaintext union counting',
        'Exact reference checked against all outside-structure password frequencies',
        'No TriGuard complete-password slot overlaps a blocked structure',
        'All methods obey the same k/j limits; empty padded slots are recorded']
    save_json(output/'audit.json',{'passed':True,'checks':checks,'raw_rows':len(rows),'expected_rows':len(cfg['epsilons'])*cfg['noise_repetitions']*len(cfg['quota_pairs'])*len(METHODS)})
    save_json(output/'status.json',{'state':'complete','elapsed_seconds':time.perf_counter()-start})
    print(json.dumps(summary_rows,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
