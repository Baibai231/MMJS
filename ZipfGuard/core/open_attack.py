"""Independent candidate generation and audited open-budget accounting.

No generator accepts target strings. Results retain all charged guesses locally;
public summaries contain counts/hashes only, never candidate lists.
"""
from __future__ import annotations
import dataclasses
import heapq
import math
import time
import numpy as np
from collections import Counter, defaultdict
from typing import Callable
from core.corpus import counts_hash
from experiments.open_config import PROTOCOL

BUDGET_UNIT = 'unique_policy_eligible_checks'
END = '\x03'
START = '\x02'


@dataclasses.dataclass
class GuessRun:
    model: str
    ranks: dict
    raw_ranks: dict
    stats: dict
    parameters: dict

    def complete_at(self, k):
        return self.stats['charged_count'] >= k or self.stats['stop_reason'] == 'exhausted'

    def summary(self):
        return {'model': self.model, 'protocol_id': PROTOCOL, 'budget_unit': BUDGET_UNIT,
                'stream_sha256': counts_hash(self.ranks), **self.stats, 'parameters': self.parameters}


class GenerationLimit(RuntimeError):
    def __init__(self, reason): self.reason = reason; super().__init__(reason)


def consume(model, stream, *, budget, raw_limit, timeout_seconds, accepts=lambda _: True,
            parameters=None, source_stop='exhausted'):
    start = time.perf_counter(); raw = filtered = duplicate = 0
    ranks, raw_ranks, seen = {}, {}, set()
    reason = 'exhausted'
    iterator = iter(stream)
    try:
        while True:
            if time.perf_counter()-start >= timeout_seconds: reason='timeout'; break
            if raw >= raw_limit: reason='raw_limit'; break
            try: word=next(iterator)
            except StopIteration: reason=source_stop; break
            if not isinstance(word,str) or not word or any(ord(c)<32 or ord(c)==127 for c in word):
                raise ValueError('生成器返回非有效单行候选')
            raw += 1
            if not accepts(word): filtered += 1; continue
            if word in seen: duplicate += 1; continue
            seen.add(word); ranks[word]=len(ranks)+1; raw_ranks[word]=raw
            if len(ranks)>=budget: reason='reached_budget'; break
    except GenerationLimit as exc: reason=exc.reason
    finally:
        close=getattr(iterator,'close',None)
        if close: close()
    return GuessRun(model,ranks,raw_ranks,
                    {'requested_budget':budget,'completed_budget':len(ranks),'charged_count':len(ranks),
                     'raw_generated_count':raw,'policy_filtered_count':filtered,'duplicate_count':duplicate,
                     'stop_reason':reason,'exhausted':reason=='exhausted',
                     'elapsed_seconds':round(time.perf_counter()-start,4)},dict(parameters or {}))


def frequency_stream(counts):
    yield from sorted(counts,key=lambda w:(-counts[w],w))


def dictionary_stream(counts):
    """Pre-registered train-derived mangling order; independent of policy/test."""
    words=sorted(counts,key=lambda w:(-counts[w],w))
    yield from words
    for kind in ('capitalize','upper','symbol','digit','year','symbol_digit'):
        for word in words:
            if kind=='capitalize': yield word[:1].upper()+word[1:]
            elif kind=='upper': yield word.upper()
            elif kind=='symbol': yield word+'!'
            elif kind=='digit': yield word+'1'
            elif kind=='year': yield word+'2026'
            else: yield word+'@7'


class OpenNgram:
    """Weighted Markov model; best-first probability-ordered finite-length search.

    Lazy sibling insertion bounds memory without beam truncation. Hitting a
    frontier/expansion/time limit is reported as incomplete, not exhausted.
    """
    def __init__(self, counts, order=3, smoothing=.1):
        self.order=order; self.smoothing=smoothing
        self.length_counts=Counter()
        for word, weight in counts.items(): self.length_counts[len(word)]+=weight
        self.alphabet=tuple(sorted({c for w in counts for c in w}|{END}))
        self.counts=[defaultdict(Counter) for _ in range(order)]
        self.totals=[Counter() for _ in range(order)]
        for word, weight in counts.items():
            history=START*(order-1)
            for char in word+END:
                for depth in range(order):
                    context=history[-depth:] if depth else ''
                    self.counts[depth][context][char]+=weight
                    self.totals[depth][context]+=weight
                history+=char
        self.cache={}

    def transitions(self, history):
        context=history[-(self.order-1):] if self.order>1 else ''
        if context in self.cache: return self.cache[context]
        prior=self.smoothing*len(self.alphabet)
        choices=[]
        for char in self.alphabet:
            p=(self.counts[0][''][char]+self.smoothing)/(self.totals[0]['']+prior)
            for depth in range(1,self.order):
                key=history[-depth:]; total=self.totals[depth].get(key,0)
                if total: p=(self.counts[depth].get(key,{}).get(char,0)+prior*p)/(total+prior)
            choices.append((-math.log(p),char))
        result=tuple(sorted(choices)); self.cache[context]=result
        return result

    def char_transitions(self, history):
        choices=self.transitions(history)
        end_probability=next(math.exp(-cost) for cost,char in choices if char==END)
        normalizer=max(1e-300,1-end_probability)
        return tuple((max(0.,cost+math.log(normalizer)),char) for cost,char in choices if char!=END)

    def length_cost(self,length):
        support=max(64,max(self.length_counts,default=64))
        return -math.log((self.length_counts[length]+self.smoothing)/(sum(self.length_counts.values())+self.smoothing*support))

    def nll(self, counts):
        loss=chars=0.0
        for word, weight in counts.items():
            history=START*(self.order-1);loss+=weight*self.length_cost(len(word))
            for char in word:
                probs=dict((c,cost) for cost,c in self.char_transitions(history))
                loss+=weight*probs.get(char,-math.log(1e-12));history+=char
            chars+=weight*(len(word)+1)
        return loss/max(chars,1)

    def search_table(self, max_length, limits):
        """Exact Viterbi suffix bounds over the finite backoff-context automaton."""
        cached=getattr(self,'_search_table',None)
        if cached is not None and len(cached[-1])>max_length:return cached
        contexts={''}
        for depth in range(1,self.order):contexts.update(self.totals[depth])
        states=sorted(contexts);indices={s:i for i,s in enumerate(states)}
        alphabet=[c for c in self.alphabet if c!=END]
        # Bound dense automaton memory as well as the enumeration frontier.
        if len(states)*len(alphabet)>limits['max_frontier']*8:raise GenerationLimit('memory_limit')
        def canonical(history):
            for depth in range(min(self.order-1,len(history)),0,-1):
                suffix=history[-depth:]
                if suffix in contexts:return suffix
            return ''
        costs=np.empty((len(states),len(alphabet)));targets=np.empty(costs.shape,dtype=np.int32)
        started=time.perf_counter()
        for i,state in enumerate(states):
            if time.perf_counter()-started>limits['timeout_seconds']:raise GenerationLimit('timeout')
            lookup={c:cost for cost,c in self.char_transitions(state)}
            for j,char in enumerate(alphabet):
                costs[i,j]=lookup[char];targets[i,j]=indices[canonical(state+char)]
        suffix=np.zeros((max_length+1,len(states)))
        for remaining in range(1,max_length+1):
            if time.perf_counter()-started>limits['timeout_seconds']:raise GenerationLimit('timeout')
            suffix[remaining]=np.min(costs+suffix[remaining-1][targets],axis=1)
        start=indices[canonical(START*(self.order-1))]
        self._search_table=(alphabet,costs,targets,start,suffix)
        return self._search_table

    def generate(self, cfg, limits):
        # A* with exact unconstrained suffix bounds; policy pruning is optional.
        # Lazy siblings keep probability order without truncating a beam.
        started=time.perf_counter()
        if cfg['min_length']>cfg['max_length']:return
        alphabet,costs,targets,start,suffix=self.search_table(cfg['max_length'],limits)
        heap=[];serial=0;expanded=0;choice_cache={}
        def class_count(word):
            return sum((any('a'<=c<='z' for c in word),any('A'<=c<='Z' for c in word),
                        any(c.isdigit() for c in word),any(not ('a'<=c<='z' or 'A'<=c<='Z' or '0'<=c<='9') for c in word)))
        max_class_gain=max((class_count(c) for c in alphabet),default=1)
        def choices(state,remaining):
            key=(state,remaining)
            if key not in choice_cache:
                choice_cache[key]=sorted((float(costs[state,j]+suffix[remaining,targets[state,j]]),
                                         char,float(costs[state,j]),int(targets[state,j])) for j,char in enumerate(alphabet))
            return choice_cache[key]
        def push(prefix,base_cost,options,index,length):
            nonlocal serial
            if index<len(options):
                bound=options[index][0];serial+=1
                heapq.heappush(heap,(base_cost+bound,serial,prefix,base_cost,options,index,length))
        for length in range(cfg['min_length'],cfg['max_length']+1):
            push('',self.length_cost(length),choices(start,length-1),0,length)
        while heap:
            if time.perf_counter()-started>=limits['timeout_seconds']:raise GenerationLimit('timeout')
            if len(heap)>limits['max_frontier']:raise GenerationLimit('memory_limit')
            _,_,prefix,base_cost,options,index,length=heapq.heappop(heap)
            push(prefix,base_cost,options,index+1,length)
            _,char,edge_cost,state=options[index];cost=base_cost+edge_cost
            next_prefix=prefix+char
            needed=cfg.get('required_classes',0)-class_count(next_prefix)
            if needed>max_class_gain*(length-len(next_prefix)):continue
            if len(next_prefix)==length:yield next_prefix
            else:
                expanded+=1
                if expanded>limits['max_expansions']:raise GenerationLimit('expansion_limit')
                push(next_prefix,cost,choices(state,length-len(next_prefix)-1),0,length)


def select_ngram(train, tuning, cfg):
    trials=[]
    for order in cfg['orders']:
        for alpha in cfg['smoothing']:
            model=OpenNgram(train,order,alpha)
            trials.append((model.nll(tuning),order,alpha,model))
    score,order,alpha,model=min(trials,key=lambda x:x[:3])
    return model,{'order':order,'smoothing':alpha,'tuning_nll':score,
                  'selection_split':'tuning','training_sha256':counts_hash(train),
                  'alphabet_source':'train only','min_length':cfg['min_length'],
                  'max_length':cfg['max_length'],'generation':'length-conditioned A* with exact suffix bounds; no beam truncation'}


def points_for_runs(runs, targets, budgets):
    """Union membership is known if hit, or if every model completed that K."""
    total=sum(targets.values())
    result=[]
    for k in budgets:
        hits=unresolved=0.0
        complete=all(r.complete_at(k) for r in runs)
        for word,weight in targets.items():
            if any(r.ranks.get(word,math.inf)<=k for r in runs): hits+=weight
            elif not complete: unresolved+=weight
        valid=bool(total) and bool(runs)
        known=valid and not unresolved
        result.append({'budget':k,'rate':hits/total if known else None,
                       'lower_bound':hits/total if valid else None,
                       'upper_bound':(hits+unresolved)/total if valid else None,
                       'hits':hits,'unresolved_weight':unresolved,'target_weight':total,
                       'complete':known, 'all_models_completed':complete})
    return result


def evaluate_runs(runs, targets, budgets):
    models = []
    for run in runs:
        row = {'run': run.summary(), 'points': points_for_runs([run], targets, budgets)}
        support = run.parameters.get('support')
        if support:
            # Coverage is measured by the evaluator, never supplied to training.
            outside = sum(weight for word, weight in targets.items() if not (
                support['min_length'] <= len(word) <= support['max_length'] and
                all(33 <= ord(c) <= 126 for c in word)))
            row['support_coverage'] = {'target_weight': sum(targets.values()),
                                      'outside_declared_support_weight': outside,
                                      'denominator_reduced': False}
        models.append(row)
    return {'minauto': points_for_runs(runs, targets, budgets), 'models': models}
