"""Recount attacks and replay all five paired target/developer responses."""
from pathlib import Path
from collections import Counter
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_registration_five_pareto import (ARMS, digest, online_with_edits, make_mechanism,
    UnlimitedPreferences, base_rule, StaticMechanism, JointRule, CompositionRule,
    load_private, developer_stream, attach_base_cost, summed_cost, write_json, counts_hash)
from tools.run_registration_exchange_pareto import rule_summary
from experiments.registration_exchange_pareto import generate_candidates, select_nominee, confirm_nominee
from experiments.registration_joint_pipeline import preview_records
from experiments.registration_dual_pipeline import ActualDualIndex, risk
from ai.pcfg_monte_carlo import Grammar


def audit(root):
    manifest = json.loads((root/'manifest.json').read_text('utf-8'))
    cfg = manifest['config']; seed = cfg['seeds'][0]; n = cfg['users']
    for relative, expected in manifest['source_hashes'].items():
        assert digest(ROOT/relative) == expected, relative
    data = load_private(root/'private/partition.pkl.gz')
    partition_sha = digest(root/'private/partition.pkl.gz')
    records = data['roles'][f'test_{seed}']; size = n//cfg['batches']
    identifiers = {i for i,_ in records}
    assert len(identifiers) == n
    for source in data['metadata']['excluded_partition_sources']:
        assert digest(source['path']) == source['sha256']
        old = load_private(source['path'])
        assert not identifiers.intersection(i for rows in old['roles'].values() for i,_ in rows)
    response = UnlimitedPreferences([w for _,w in data['roles']['train']],cfg['minimum'],cfg['weights'],cfg['response_mode'],None)
    cohorts = [records[t*size:(t+1)*size] for t in range(cfg['batches'])]
    dev = developer_stream(data['roles']['train'],'raw',seed,cfg['batches'],data['median_length'])
    literature = base_rule(data,'literature')
    segreti = load_private(root/'runs'/str(seed)/'segreti_struct_m_nohint/blacklist.pkl.gz')['rule']
    baseline = JointRule(segreti.minimum,segreti.common,segreti.exact,segreti.shapes,segreti.k,len(segreti.shapes))
    base_counts = response.simulate(data['roles']['train'],JointRule(baseline.minimum,baseline.common),seed)['counts']
    base_key = counts_hash(base_counts)
    frozen_prefixes = {m:load_private(root/'private/attacks'/str(seed)/'bidirectional_exchange/prefixes'/f'{base_key}-{m}-{max(cfg["budgets"])}.pkl.gz') for m in ('pcfg','omen')}
    frozen_ranks = dict(frozen_prefixes['pcfg']['ranks'])
    for word,rank in frozen_prefixes['omen']['ranks'].items():
        frozen_ranks[word] = min(rank,frozen_ranks.get(word,rank))
    audits = []
    for arm in ARMS:
        path = root/'runs'/str(seed)/arm
        result = json.loads((path/'result.json').read_text('utf-8'))
        saved = load_private(path/'state.pkl.gz')
        assert result['status']=='complete' and not result['attack_failures']
        assert result['partition_sha256']==partition_sha and saved['t']==cfg['batches']
        assert len(saved['attempts'])==n and sum(saved['history'].values())==n
        assert abs(sum(saved['attempts'])/n-result['cost']['mean_extra_submissions'])<1e-12
        assert abs(sum(a>3 for a in saved['attempts'])/n-result['cost']['more_than_three_fraction'])<1e-12
        assert summed_cost(saved['costs'])==result['cost']
        base = CompositionRule(minimum=8,github=True) if arm=='github_core' else literature
        rule = baseline if arm=='bidirectional_exchange' else segreti if arm=='segreti_struct_m_nohint' else base
        history, dev_history, costs, attempts = Counter(),Counter(),[],[]
        mechanism=dev_mechanism=None
        for t in range(cfg['batches']):
            if arm=='bidirectional_exchange' and t:
                candidate_audit=json.loads((path/f'candidate_audit_{t+1:02}.json').read_text('utf-8'))
                recent=sum(saved['final_batches'][max(0,t-cfg['window']):t],Counter())
                recent_initial=cohorts[max(0,t-cfg['window']):t]
                previews=preview_records(data['roles']['preview_select'],recent_initial,seed,t,'select',cfg['preview_users'],cfg['recent_weight'])
                candidates,screening=generate_candidates(baseline,rule,recent,previews,response,frozen_ranks,cfg,seed,lambda _:None)
                assert screening==candidate_audit['screening']
                assert [rule_summary(c['rule']) for c in candidates]==[c['rule'] for c in candidate_audit['candidates']]
                winner=select_nominee(candidate_audit['candidates'],cfg)
                nominee=winner['number'] if winner else None
                assert nominee==candidate_audit['nominee']
                if nominee is not None:
                    chosen,status=confirm_nominee(candidate_audit['confirmation'],nominee,cfg)
                else:
                    chosen=next(c['number'] for c in candidate_audit['candidates'] if 'hold' in c['roles'])
                    status='hold_no_selection_double_improvement'
                assert status==candidate_audit['status']
                rule=candidates[chosen]['rule']
                assert rule_summary(rule)==candidate_audit['chosen']
            if arm in ('schechter_strong','malone'):
                if t==0:
                    actual=online_with_edits(cohorts[t],StaticMechanism(base),response,seed,True)
                    development=online_with_edits(dev[t],StaticMechanism(base),response,seed,True)
                    mechanism=make_mechanism(arm,base,actual,cfg)
                    dev_mechanism=make_mechanism(arm,base,development,cfg)
                else:
                    actual=online_with_edits(cohorts[t],mechanism,response,seed)
                    development=online_with_edits(dev[t],dev_mechanism,response,seed)
            else:
                actual=response.simulate(cohorts[t],rule,seed,distances=True)
                development=response.simulate(dev[t],rule,seed)
            attach_base_cost(actual,response.simulate(cohorts[t],base,seed))
            assert actual['counts']==saved['final_batches'][t]
            assert actual['attempts']==saved['attempts'][t*size:(t+1)*size]
            assert actual['summary']==saved['costs'][t]
            assert development['counts']==saved['dev_batches'][t]
            assert rule_summary(rule)==saved['timeline'][t]
            history.update(actual['counts']);dev_history.update(development['counts'])
            costs.append(actual['summary']);attempts.extend(actual['attempts'])
        assert history==saved['history'] and dev_history==saved['dev_history']
        key=counts_hash(dev_history)
        assert key==result['A1_training_sha256'] and sum(dev_history.values())==result['A1_training_occurrences']==60000
        cache_root=root/'private/attacks'/str(seed)/arm
        prefixes={m:load_private(cache_root/'prefixes'/f'{key}-{m}-{max(cfg["budgets"])}.pkl.gz') for m in ('pcfg','omen')}
        for prefix in prefixes.values():
            assert prefix['complete'] and prefix['raw_emissions']==max(cfg['budgets'])
        recounts=[]
        for name,curve in result['actual_attacks'].items():
            for point in curve['points']:
                per=point['budget']//2 if name=='union_equal_total_B' else point['budget']
                models=(name,) if name in prefixes else ('pcfg','omen')
                hits=sum(count for word,count in history.items() if any(prefixes[m]['ranks'].get(word,per+1)<=per for m in models))
                assert point['hits']==hits and abs(hits/n-point['rate_per_initial_user'])<1e-12
                if name.startswith('union'):
                    assert point['per_model_budget']==per and point['max_total_attempts']==2*per
                recounts.append({'metric':name,'budget':point['budget'],'hits':hits,'denominator':n})
        grammar=Grammar(**load_private(cache_root/'grammars'/f'{key}.pkl.gz'))
        index=ActualDualIndex(grammar,prefixes,max(cfg['budgets']))
        assert risk(index,history,cfg['budgets'],n)==result['final_dual_risk']
        audits.append({'arm':arm,'result_sha256':digest(path/'result.json'),'cost_replay':'passed',
                       'target_and_developer_replay_batches':cfg['batches'],'attack_recounts':recounts,
                       'extra_submission_sum':sum(attempts),'mean_edit_distance':result['cost']['mean_edit_distance']})
        print(f'{arm}: replay and all 16 attack points passed',flush=True)
    first=load_private(root/'runs'/str(seed)/ARMS[0]/'state.pkl.gz')
    other=load_private(root/'runs'/str(seed)/'segreti_struct_m_nohint/state.pkl.gz')
    assert first['final_batches'][0]==other['final_batches'][0] and first['costs'][0]==other['costs'][0]
    output={'status':'passed','users_per_method':n,'method_count':len(ARMS),'partition_sha256':partition_sha,
            'fresh_occurrence_disjointness':'passed','source_hashes':'passed','baseline_first_batch':'passed',
            'v2_candidate_screening_and_confirmation_replay':'passed','methods':audits,'human_verification':'pending'}
    write_json(root/'aggregate_audit.json',output)
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('root',type=Path)
    audit(parser.parse_args().root.resolve())
