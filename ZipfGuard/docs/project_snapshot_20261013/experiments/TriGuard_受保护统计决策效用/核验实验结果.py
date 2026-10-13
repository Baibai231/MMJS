from pathlib import Path
import csv,json,hashlib,sys
import numpy as np
out=Path(sys.argv[1]); root=Path(sys.argv[2]); summary=json.loads((out/'结果汇总.json').read_text('utf-8')); manifest=json.loads((out/'manifest.json').read_text('utf-8'))
with (out/'逐次决策结果.csv').open(encoding='utf-8-sig',newline='') as h:rows=list(csv.DictReader(h))
expected=len(summary['settings']['epsilons'])*summary['settings']['noise_repetitions']*len(summary['settings']['quota_pairs'])*3
assert len(rows)==expected==720
keys=[(float(r['epsilon']),int(r['repetition']),int(r['k']),int(r['j']),r['method']) for r in rows];assert len(set(keys))==len(rows)
by=dict(zip(keys,rows))
for key,r in by.items():
 e,rep,k,j,method=key; n=int(r['population']);truth=int(r['true_users']);extra=int(r['extra_users'])
 assert n==10000 and 0<=truth<=n
 assert truth==int(r['structure_users'])+extra
 np.testing.assert_allclose(float(r['coverage_pct']),100*truth/n)
 np.testing.assert_allclose(float(r['absolute_error_pp']),100*abs(float(r['predicted_users'])-truth)/n)
 assert int(r['actual_exact_size'])<=k and len(json.loads(r['selected_shapes']))<=j
 assert r['selected_shapes']==by[(e,rep,k,j,'marginal')]['selected_shapes']
 if method=='TriGuard':assert int(r['overlap_users'])==0 and int(r['redundant_slots'])==0
 assert truth<=int(by[(e,rep,k,j,'exact_reference')]['true_users'])
for s in summary['summary']:
 subset=[r for r in rows if float(r['epsilon'])==s['epsilon'] and r['method']==s['method']]
 assert len(subset)==80
 for source,target in [('coverage_pct','mean_coverage_pct'),('extra_users','mean_extra_users'),('absolute_error_pp','mean_absolute_error_pp')]:
  np.testing.assert_allclose(np.mean([float(r[source]) for r in subset]),s[target],rtol=0,atol=1e-10)
profiles=json.loads((out/'匿名测试快照.json').read_text('utf-8'))
assert len(profiles)==60
for p in profiles:
 assert set(p)=={'epsilon','repetition','counts','spectra','global_frequencies'}
 assert np.asarray(p['spectra']).shape==(9,3)
 assert all(isinstance(x,(int,float)) for key in ('counts','spectra','global_frequencies') for x in np.asarray(p[key]).flat)
 np.testing.assert_allclose(sum(p['counts']),10000,atol=1e-8)
for relative,expected_hash in manifest['implementation_sha256'].items():
 assert hashlib.sha256((root/relative).read_bytes()).hexdigest()==expected_hash
report={'passed':True,'raw_decisions':len(rows),'paired_conditions':len(rows)//3,'protected_snapshot_pairs':len(profiles),'checks':['Unique experiment conditions and correct denominators','Identical blocked structures in every paired comparison','Direct-union decomposition and exact-reference upper bound','CSV/JSON summary reconciliation','Fixed-dimension numeric-only administrator inputs','Recorded implementation hashes match'],'artifacts':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [out/'逐次决策结果.csv',out/'结果汇总.json',out/'匿名测试快照.json']}}
(out/'独立核验.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
