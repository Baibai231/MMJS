"""Upstream pure PCFG grammar, marginal string probabilities and pre-sampled ranks.

Rank(w) = 1 + E[1{X precedes w and is eligible}/P(X)], X ~ P.
Ties use Python string order. Multiple derivations of one string are summed.
This estimates distinct-string probability order, not the upstream parse queue.
"""
from __future__ import annotations
import bisect
import configparser
import hashlib
import importlib.util
import itertools
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

from ai.pcfg_adapter import PCFGAttacker, PCFGConfig, EXPECTED_COMMIT
from core.corpus import counts_hash

VERSION = 'upstream-pcfg-marginal-mc-v1'


def normalize(values):
    total = math.fsum(values.values())
    if not total or any(not math.isfinite(p) or p <= 0 for p in values.values()):
        raise ValueError('PCFG probability table is empty or invalid')
    return {w: p / total for w, p in values.items()}


class Grammar:
    def __init__(self, terminals, structures, metadata=None):
        self.terminals = {k: normalize(v) for k, v in terminals.items()}
        self.structures = [(tuple(tokens), p) for tokens, p in structures]
        total = math.fsum(p for _, p in self.structures)
        if total <= 0:
            raise ValueError('PCFG contains no supported structures')
        self.structures = [(t, p / total) for t, p in self.structures]
        self.metadata = metadata or {}
        self.draws = {}
        for key, table in self.terminals.items():
            words = sorted(table)
            self.draws[key] = (words, list(itertools.accumulate(table[w] for w in words)))
        self.structure_cdf = list(itertools.accumulate(p for _, p in self.structures))
        self.by_length = defaultdict(list)
        self.lengths = {k: sorted({len(w) for w in t}) for k, t in self.terminals.items()}
        for tokens, p in self.structures:
            lengths = {0}
            for token in tokens:
                lengths = {a + b for a in lengths for b in self.lengths[token]}
            for length in lengths:
                self.by_length[length].append((tokens, p))
        self.plans_by_length = {}
        for length, rows in self.by_length.items():
            plans = []
            for tokens, prior in rows:
                if all(len(self.lengths[token]) == 1 for token in tokens):
                    offset, slices = 0, []
                    for token in tokens:
                        end = offset + self.lengths[token][0]
                        slices.append((token, offset, end))
                        offset = end
                    plans.append((slices, tokens, prior))
                else:
                    plans.append((None, tokens, prior))
            self.plans_by_length[length] = plans
        self.cache = {}

    def probability(self, word):
        if word in self.cache:
            return self.cache[word]
        values = []
        segment_cache = {}

        def segment(token, start, end):
            key = (token, start, end)
            if key not in segment_cache:
                segment_cache[key] = self.terminals[token].get(word[start:end], 0.)
            return segment_cache[key]

        for fixed_slices, tokens, prior in self.plans_by_length.get(len(word), ()):
            if fixed_slices is not None:
                probability = prior
                for token, start, end in fixed_slices:
                    probability *= segment(token, start, end)
                    if probability == 0.:
                        break
                values.append(probability)
                continue
            positions = {0: prior}
            for token in tokens:
                next_positions = defaultdict(float)
                for start, prefix in positions.items():
                    for length in self.lengths[token]:
                        end = start + length
                        if end <= len(word):
                            prob = segment(token, start, end)
                            if prob:
                                next_positions[end] += prefix * prob
                positions = next_positions
                if not positions:
                    break
            values.append(positions.get(len(word), 0.))
        probability = math.fsum(values)
        self.cache[word] = probability
        return probability

    def sample(self, rng):
        i = bisect.bisect_right(self.structure_cdf, rng.random() * self.structure_cdf[-1])
        tokens = self.structures[min(i, len(self.structures)-1)][0]
        result = []
        for token in tokens:
            words, cumulative = self.draws[token]
            i = bisect.bisect_right(cumulative, rng.random() * cumulative[-1])
            result.append(words[min(i, len(words)-1)])
        return ''.join(result)

    def payload(self):
        return {'terminals': self.terminals, 'structures': self.structures,
                'metadata': self.metadata}

    @classmethod
    def fit(cls, counts, *, runtime_root=None, timeout=180):
        config = PCFGConfig.workspace_default(timeout_seconds=timeout)
        if runtime_root is not None:
            import dataclasses
            config = dataclasses.replace(config, runtime_root=Path(runtime_root))
        adapter = PCFGAttacker(config)
        adapter._ensure_available()
        backend = adapter._prepare_backend()
        train = [w for w, n in sorted(counts.items()) for _ in range(n)]
        key = adapter._training_key(train)
        name = adapter._ruleset_name(key)
        adapter._train(backend, train, name, key)
        spec = importlib.util.spec_from_file_location('zipfguard_grammar_io', backend / 'lib_guesser/grammar_io.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        raw, bases, info = module.load_grammar(name, str(adapter._ruleset_path(backend, name)),
                                             '4.8', False, False, 'Grammar')
        tables = {k: normalize({w: row['prob'] for row in rows for w in row['values']})
                  for k, rows in raw.items() if rows and k not in ('M', 'E', 'W')}
        # Merge capitalization derivations, including Unicode expansions, once.
        terminals = {k: v for k, v in tables.items() if not k.startswith(('A', 'C'))}
        for k, alpha in tables.items():
            if not k.startswith('A'):
                continue
            combined = defaultdict(float)
            for word, p in alpha.items():
                for mask, q in tables['C' + k[1:]].items():
                    if len(word) != len(mask):
                        raise ValueError('PCFG capitalization length mismatch')
                    value = ''.join(c.upper() if m == 'U' else c for c, m in zip(word, mask))
                    combined[value] += p * q
            terminals[k] = dict(combined)
        structures = []
        for base in bases:
            tokens = [t for t in base['replacements'] if not t.startswith('C')]
            if any(t not in terminals for t in tokens):
                raise ValueError('Unsupported PCFG structure: ' + repr(tokens))
            structures.append((tokens, base['prob']))
        return cls(terminals, structures, {'training_sha256': counts_hash(counts),
                   'training_occurrences': sum(counts.values()), 'upstream_commit': EXPECTED_COMMIT,
                   'pure_pcfg': True, 'ordering': 'marginal string probability; lexicographic ties'})


class MonteCarloIndex:
    def __init__(self, grammar, samples=100000, seed=42, *, sampled_counts=None,
                 sampled_probabilities=None):
        if type(samples) is not int or samples < 2:
            raise ValueError('Monte Carlo samples must be >= 2')
        self.grammar, self.n, self.seed = grammar, samples, seed
        rng = random.Random(seed)
        self.sampled_counts = (Counter(sampled_counts) if sampled_counts is not None else
                               Counter(grammar.sample(rng) for _ in range(samples)))
        if sum(self.sampled_counts.values()) != samples:
            raise ValueError('Sample count mismatch')
        if sampled_probabilities is not None:
            if set(sampled_probabilities) != set(self.sampled_counts) or any(
                    not math.isfinite(p) or p <= 0 for p in sampled_probabilities.values()):
                raise ValueError('Stored PCFG sample probabilities are invalid')
            grammar.cache.update(sampled_probabilities)
        self.tables = {}
        self._table(None)

    def _table(self, rule):
        key = json.dumps(rule.summary(), sort_keys=True) if rule else ''
        if key in self.tables:
            return self.tables[key]
        rows = sorted((-self.grammar.probability(w), w, n)
                      for w, n in self.sampled_counts.items() if rule is None or rule.accepts(w))
        keys, first, second, hits = [], [0.], [0.], [0]
        for negprob, word, count in rows:
            p = -negprob
            if p <= 0:
                raise ValueError('Sampler and scorer disagree')
            keys.append((negprob, word))
            first.append(first[-1] + count / p)
            second.append(second[-1] + count / (p*p))
            hits.append(hits[-1] + count)
        result = (keys, first, second, hits)
        self.tables[key] = result
        return result

    def query(self, word, rule=None):
        p = self.grammar.probability(word)
        if p <= 0:
            return {'status': 'outside_model_support', 'probability': 0., 'guess_count': None,
                    'standard_error': None, 'mc_hits': 0}
        if rule is not None and not rule.accepts(word):
            return {'status': 'policy_ineligible', 'probability': p, 'guess_count': None,
                    'standard_error': None, 'mc_hits': 0}
        keys, first, second, hits = self._table(rule)
        i = bisect.bisect_left(keys, (-p, word))
        mean = first[i] / self.n
        variance = max(0., (second[i] - first[i]**2/self.n) / (self.n-1))
        se = math.sqrt(variance / self.n)
        return {'status': 'estimated' if hits[i] >= 30 else 'low_sample_support',
                'probability': p, 'guess_count': 1. + mean,
                'standard_error': se if hits[i] else None, 'mc_hits': hits[i],
                'samples': self.n, 'tie_order': 'lexicographic',
                'rank_interval95': [max(1., 1. + mean - math.sqrt(math.log(40)/(2*self.n))/p),
                                    1. + mean + math.sqrt(math.log(40)/(2*self.n))/p],
                'interval_method': 'pointwise Hoeffding bound; bounded importance weights <= 1/P(target)',
                'interval_note': 'Monte Carlo standard error; zero hits does not prove rank 1'}

    def save(self, path, population=None):
        Path(path).write_text(json.dumps({'version': VERSION, 'grammar': self.grammar.payload(),
            'samples': self.n, 'seed': self.seed, 'sampled_counts': self.sampled_counts,
            'sampled_probabilities': {w: self.grammar.probability(w)
                                      for w in self.sampled_counts},
            'population': dict(population or {})}, ensure_ascii=False, allow_nan=False), encoding='utf-8')

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if data['version'] != VERSION:
            raise ValueError('Unsupported Monte Carlo artifact')
        grammar = Grammar(**data['grammar'])
        return cls(grammar, data['samples'], data['seed'],
                   sampled_counts=data['sampled_counts'],
                   sampled_probabilities=data.get('sampled_probabilities')), data['population']


def popularity(word, counts):
    count = counts.get(word, 0)
    if not count:
        return {'status': 'absent', 'count': 0, 'rank': None, 'tied_categories': 0, 'fraction': 0.}
    return {'status': 'observed', 'count': count,
            'rank': 1 + sum(n > count for n in counts.values()),
            'tied_categories': sum(n == count for n in counts.values()),
            'fraction': count / sum(counts.values()), 'rank_method': 'competition ranking by observed frequency'}


def popularity_table(counts):
    """Precompute tied popularity ranks in O(U log U), not once per user."""
    frequency_counts = Counter(counts.values())
    ranks, offset = {}, 1
    for count in sorted(frequency_counts, reverse=True):
        ranks[count] = offset
        offset += frequency_counts[count]
    total = sum(counts.values())
    return {word: {'status': 'observed', 'count': count, 'rank': ranks[count],
                   'tied_categories': frequency_counts[count], 'fraction': count/total,
                   'rank_method': 'competition ranking by observed frequency'}
            for word, count in counts.items()}
