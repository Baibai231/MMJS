"""Additional literature mechanisms and a matched website-policy experiment.

Segreti: StructMNoHint blacklist construction, adapted to shared base and model.
No claims to reproduce human feedback, memory, or the original training corpus.
"""
from collections import Counter
from dataclasses import dataclass, fields, replace
import hashlib
from pathlib import Path

from policy.registration_joint import JointRule, structure
from policy.registration_comparators import CompositionRule
from experiments.registration_joint_pilot import GradedRule, graded_candidates
from experiments.registration_joint_data import load_private, save_private
from experiments.registration_joint_pipeline import attach_base_cost, write_json


def github_accepts(word):
    return len(word)>=15 or (len(word)>=8 and any('a'<=c<='z' for c in word)
                            and any('0'<=c<='9' for c in word))


@dataclass(frozen=True)
class WebsiteGradedRule(GradedRule):
    def accepts(self,word):
        return github_accepts(word) and super().accepts(word)

    def signature(self):
        return super().signature()+('github_core',)

    def summary(self):
        return {**super().summary(),'github_core':True}


def website_candidates(base,recent,hold,ks,js,guessed):
    result=[]
    for rule,is_hold in graded_candidates(base,recent,hold,ks,js,guessed):
        if isinstance(rule,GradedRule):
            values={f.name:getattr(rule,f.name) for f in fields(GradedRule)}
            converted=WebsiteGradedRule(**values)
        else:
            values={f.name:getattr(rule,f.name) for f in fields(JointRule)}
            converted=CompositionRule(**values,github=True)
        result.append((converted,is_hold))
    return result


def segreti_rule(base,prefix,accepted_guesses,path):
    path=Path(path)
    if path.exists():
        saved=load_private(path)
        return saved['rule'],saved['metadata']
    admissible=sorted(((r,w) for w,r in prefix['ranks'].items() if base.accepts(w)))
    if len(admissible)<accepted_guesses:
        raise RuntimeError(f'Segreti blacklist requires {accepted_guesses} distinct compliant PCFG guesses, only {len(admissible)} available')
    selected=admissible[:accepted_guesses]
    shapes=frozenset(structure(w) for _,w in selected)
    rule=replace(base,shapes=shapes,j=len(shapes))
    metadata={'source':'Segreti et al. 2017 sections 3.3 and 3.4, StructMNoHint',
              'pcfg_distinct_compliant_guesses':accepted_guesses,'banned_structures':len(shapes),
              'last_raw_guess_rank':selected[-1][0],
              'blacklist_sha256':hashlib.sha256('\n'.join(sorted(shapes)).encode()).hexdigest(),
              'fixed_before_target_simulation':True,
              'adaptations':['Shared >=8 plus independent training top1000 blacklist replaces original 3c12',
                             'Shared base-conditioned developer training and installed PCFG replace original leak collection and implementation',
                             'Common simulated response replaces human choices; no feedback as StructMNoHint',
                             'Shared PCFG+OMEN evaluation replaces original multi-tool PGS'],
              'not_reproduced':['Human creation time, recall, sentiment','Original 2141-structure artifact','Feedback conditions'],
              'online_behavior':'Precomputed fixed blacklist, as in published experimental conditions; not rolling top-j'}
    save_private(path,{'rule':rule,'metadata':metadata})
    return rule,metadata


def simulate_static(cohorts,dev_cohorts,response,base,rule,seed,path,event):
    path=Path(path);checkpoint=path / 'state.pkl.gz'
    state=load_private(checkpoint) if checkpoint.exists() else {
        't':0,'history':Counter(),'dev_history':Counter(),'batches':[],'dev_batches':[],
        'costs':[],'timeline':[],'attempts':[],'decisions':[],'developer_pending':0}
    for t in range(state['t'],len(cohorts)):
        actual=response.simulate(cohorts[t],rule,seed)
        baseline=response.simulate(cohorts[t],base,seed)
        attach_base_cost(actual,baseline)
        developer=response.simulate(dev_cohorts[t],rule,seed)
        state['developer_pending']+=developer['summary']['pending_users']
        state['history'].update(actual['counts']);state['dev_history'].update(developer['counts'])
        state['batches'].append(actual['counts']);state['dev_batches'].append(developer['counts'])
        state['costs'].append(actual['summary']);state['attempts'].extend(actual['attempts'])
        state['timeline'].append({**rule.summary(),'batch':t+1})
        state['t']=t+1
        save_private(checkpoint,state)
        write_json(path / 'progress.json',{'completed_batches':t+1,'total_batches':len(cohorts),
                                         'completed_users':sum(state['history'].values()),'last_rule':state['timeline'][-1]})
        event(f'Static mechanism: batch {t+1}/{len(cohorts)}')
    if state['developer_pending']:
        raise RuntimeError('Incomplete developer response; attack unavailable, no silent deletion')
    return state
