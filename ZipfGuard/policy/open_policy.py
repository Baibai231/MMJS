"""Training-only, frequency-aware rule discovery and weighted user response."""
from __future__ import annotations
import dataclasses
import hashlib
import math
import random
from collections import Counter
import numpy as np
from core.corpus import counts_hash
from policy.engine import extract_features
from experiments.open_config import FEATURES


@dataclasses.dataclass(frozen=True)
class Rule:
    name: str
    min_length: int = 0
    classes: int = 0
    deny: tuple = ()
    blocklist: frozenset = frozenset()
    origin: str = 'common'

    @property
    def complexity(self):
        return int(self.min_length>0)+int(self.classes>0)+len(self.deny)+int(bool(self.blocklist))

    def accepts(self, word):
        if len(word)<self.min_length or word in self.blocklist: return False
        if self.classes or self.deny:
            f=extract_features(word)
            if f['class_count']<self.classes: return False
            if any(f.get(key,False) for key in self.deny): return False
        return True

    def summary(self):
        return {'name':self.name,'min_length':self.min_length,'required_classes':self.classes,
                'deny_features':list(self.deny),'blocklist_size':len(self.blocklist),
                'blocklist_sha256':counts_hash(dict.fromkeys(self.blocklist,1)),
                'origin':self.origin,'rule_count':self.complexity}


def entropy(p):
    return -sum(v*math.log2(v) for v in p if v>0)


def discover_rules(train,cfg):
    total=sum(train.values()); ranked=sorted(train,key=lambda w:(-train[w],w))
    head=set(); mass=0
    for word in ranked:
        head.add(word);mass+=train[word]
        if mass>=cfg['discovery']['head_mass']*total: break
    scores=[]
    for feature in FEATURES:
        cells=np.zeros((2,2),dtype=float)
        for word,count in train.items(): cells[int(bool(extract_features(word).get(feature))),int(word in head)]+=count
        probs=cells/total; px=probs.sum(axis=1); py=probs.sum(axis=0)
        gain=entropy(py)-sum(px[x]*entropy(probs[x]/px[x]) for x in (0,1) if px[x]>0)
        ratio=gain/entropy(px) if entropy(px)>0 else 0.0
        pyes=probs[1,1]/px[1] if px[1] else 0.0
        pno=probs[0,1]/px[0] if px[0] else 0.0
        scores.append({'feature':feature,'support':float(px[1]),'information_gain_ratio':float(max(0,ratio)),
                       'head_rate_present':float(pyes),'head_rate_absent':float(pno),
                       'risk_direction_positive':bool(pyes>pno)})
    scores.sort(key=lambda s:(-s['information_gain_ratio'],s['feature']))
    allowed=[s['feature'] for s in scores if s['risk_direction_positive'] and s['support']>=cfg['discovery']['min_support']]
    chosen=allowed[:cfg['discovery']['max_features']]
    if cfg['discovery']['mode']=='none':
        # Match the guided candidate count; random ordering never uses feature scores.
        library=list(FEATURES);random.Random(cfg['seed']).shuffle(library)
        chosen=library[:len(chosen)]
    block=frozenset(ranked[:cfg['search']['blocklist_size']])
    rules=[Rule('baseline')]
    for n in cfg['search']['lengths']:
        rules.extend([Rule(f'length-{n}',n),Rule(f'complex-{n}',n,3),Rule(f'block-{n}',n,blocklist=block)])
    for feature in chosen:
        rules.append(Rule('deny-'+feature,deny=(feature,),origin='discovered'))
        for n in cfg['search']['lengths']:
            rules.append(Rule(f'length-{n}-deny-{feature}',n,deny=(feature,),origin='discovered'))
    if len(chosen)>1:
        rules.append(Rule('deny-combined',deny=tuple(chosen),origin='discovered'))
    rules=[r for r in rules if r.complexity<=cfg['search']['max_rules']]
    return rules,{'fit_split':'train','method':'frequency-weighted head feature information gain ratio',
                  'mode':cfg['discovery']['mode'],'label_source':'training frequency head (not attack rank)',
                  'training_sha256':counts_hash(train),'head_mass':mass/total,'head_categories':len(head),
                  'features':scores,'selected_features':chosen,'candidate_count':len(rules),
                  'attack_labels_used':False}


@dataclasses.dataclass
class Response:
    initial: dict
    final: dict
    summary: dict
    # outcome records: original, final or None, attempts, modified, multiplicity
    records: list
    scenario: str
    rule: Rule
    config: dict


def response_for(initial,rule,scenario,cfg,*,pool,seed,split):
    n=sum(initial.values()); accepted={w:c for w,c in initial.items() if rule.accepts(w)}
    a=sum(accepted.values())/n if n else 0.0
    if scenario=='R0':
        limit=cfg['r0_max_total_attempts']
        completion=(1-(1-a)**limit if limit else 1.0) if a else 0.0
        # Expected extra attempts until success or total-attempt cap.
        extra=((1-(1-a)**limit)/a-1 if limit else (1-a)/a) if a else (limit-1 if limit else None)
        return Response(dict(initial),accepted,
                        {'initial_weight':n,'initial_accept_rate':a,'rejection_rate':1-a,'cost':1-a,
                         'completion_rate':completion,'mean_extra_attempts':extra,'mean_edit_distance':None,
                         'mean_length_increase':None,'feasible_response':bool(a),
                         'final_support':len(accepted),'risk_denominator':'R0 conditional accepted distribution',
                         'cost_definition':'initial rejection fraction; extra attempts analytic',
                         'behavior':'independent reselection from split empirical distribution',
                         'max_total_attempts':limit or None},[],scenario,rule,dict(cfg))
    if scenario not in ('R1','R2'): raise ValueError('unknown response')
    if not pool: raise ValueError('响应重选池需要训练数据')
    words=sorted(pool); cumulative=np.cumsum([pool[w] for w in words]); pooltotal=float(cumulative[-1])
    records=[];final=Counter();attempt_sum=completed=edits=length_delta=0.0
    for word,count in sorted(initial.items()):
        for occurrence in range(int(count)):
            token=f'{seed}|{split}|{word}|{occurrence}'.encode('utf-8')
            rng=random.Random(int.from_bytes(hashlib.sha256(token).digest()[:8],'big'))
            target=word if rule.accepts(word) else None; attempts=0
            if target is None:
                for attempt in range(cfg['max_attempts']):
                    if scenario=='R2' and rng.random()<cfg['abandon_probability']: break
                    attempts+=1
                    # Pre-registered repair sequence is policy independent.
                    if scenario=='R2' and rng.random()<cfg['reselect_probability']:
                        proposal=words[min(int(np.searchsorted(cumulative,rng.random()*pooltotal,side='right')),len(words)-1)]
                    else:
                        proposals=(word+'!',word[:1].upper()+word[1:]+'1!',word+'@7',word+'2026!',word+'-'+word)
                        proposal=proposals[min(attempt,len(proposals)-1)]
                    if rule.accepts(proposal): target=proposal;break
            modified=target is not None and target!=word
            records.append((word,target,attempts,modified,1))
            attempt_sum+=attempts
            if target is not None:
                final[target]+=1;completed+=1
                edits+=edit_distance(word,target);length_delta+=max(0,len(target)-len(word))
    return Response(dict(initial),dict(final),
                    {'initial_weight':n,'initial_accept_rate':a,'rejection_rate':1-a,'cost':1-a,
                     'completion_rate':completed/n if n else 0,'mean_extra_attempts':attempt_sum/n if n else 0,
                     'mean_edit_distance':edits/n if n else 0,'mean_length_increase':length_delta/n if n else 0,
                     'modification_rate':sum(r[3] for r in records)/n if n else 0,
                     'feasible_response':bool(final),'final_support':len(final),
                     'risk_denominator':'completed accounts','cost_definition':'initial rejection fraction; all initial users denominator',
                     'behavior':'fixed repair' if scenario=='R1' else 'repair/reselection/abandonment mixture',
                     'reselection_pool':'train only'},records,scenario,rule,dict(cfg))


def edit_distance(a,b):
    row=list(range(len(b)+1))
    for i,ca in enumerate(a,1):
        nxt=[i]
        for j,cb in enumerate(b,1): nxt.append(min(nxt[-1]+1,row[j]+1,row[j-1]+(ca!=cb)))
        row=nxt
    return row[-1]
