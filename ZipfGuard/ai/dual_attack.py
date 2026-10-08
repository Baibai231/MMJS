"""Observed PCFG/author-OMEN prefixes, trained without target-account data.

Union budget B means B raw attempts per model (at most 2B total attempts).
Duplicate guesses consume attempts; an account hit by both is counted once.
Private candidate caches stay under reports/, never in public reports.
"""
from collections import Counter
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import bisect

from ai.pcfg_adapter import PCFGAttacker, PCFGConfig, EXPECTED_COMMIT
from ai.pcfg_monte_carlo import Grammar
from ai.research_models import (DEFAULTS, MODEL_ROOT, SOURCES, execute,
                                file_hash, omen_command, wsl_path)
from core.corpus import counts_hash

VERSION = 'pcfg-omen-observed-prefix-union-v1'
ROOT = Path(__file__).resolve().parents[1]


def omen_identity():
    source = MODEL_ROOT / 'upstream' / 'omen'
    files = [source / 'makefile', source / 'docs' / 'LICENSE', *sorted((source / 'src').glob('*'))]
    if not (source / 'src' / 'enumNG.c').is_file():
        raise RuntimeError('作者版 OMEN 源码不存在；请按 ATTACK_MODEL_INTEGRATION.md 安装')
    hashes = {str(p.relative_to(source)): file_hash(p) for p in files if p.is_file() and p.suffix != '.o'}
    binaries = {name: file_hash(source / name) for name in ('alphabetCreator', 'createNG', 'enumNG')}
    # Packaged sources need not retain .git. Never claim a verified revision
    # merely because Git finds the enclosing ZipfGuard repository.
    revision = None
    if (source / '.git').exists():
        result = execute(['git', '-C', source, 'rev-parse', 'HEAD'])
        if result.returncode == 0:
            revision = result.stdout.decode().strip()
    return {'url': SOURCES['omen']['url'], 'expected_upstream_commit': SOURCES['omen']['commit'],
            'detected_upstream_commit': revision, 'source_sha256': hashes,
            'binary_sha256': binaries, 'feedback_used': False}


def omen_prefix(counts, settings, budget, work):
    cfg = deepcopy(DEFAULTS)
    cfg['omen']['order'] = settings['order']
    cfg['wsl_distribution'] = settings['wsl_distribution']
    timeout = settings['timeout_seconds']
    supported = {w: n for w, n in counts.items()
                 if settings['order'] <= len(w) <= 19 and all(33 <= ord(c) <= 126 for c in w)}
    if not supported:
        raise ValueError('开发训练集没有 OMEN 声明范围内的样本')
    alphabet = ''.join(sorted({c for w in supported for c in w}))
    training = work / 'training.txt'
    with training.open('w', encoding='utf-8', newline='\n') as handle:
        for word, n in sorted(supported.items()):
            handle.write((word + '\n') * n)
    commands = [('alphabetCreator', ['--pwList', wsl_path(training), '--size', len(alphabet), '--output', 'alphabet']),
                ('createNG', ['--iPwdList', wsl_path(training), '-n', settings['order'], '-A', 'alphabet.alphabet'])]
    output = work / 'guesses.txt'
    with (work / 'omen.log').open('wb') as log:
        for binary, args in commands:
            result = execute(omen_command(cfg, binary, work, args, timeout), cwd=work,
                             timeout=timeout + 15, stdout=log, stderr=log)
            if result.returncode:
                raise RuntimeError(f'OMEN {binary} failed: {result.returncode}; see private omen.log')
        with output.open('wb') as handle:
            result = execute(omen_command(cfg, 'enumNG', work, ['-p', '-m', budget], timeout),
                             cwd=work, timeout=timeout + 15, stdout=handle, stderr=log)
    if result.returncode:
        raise RuntimeError(f'OMEN prefix incomplete (exit={result.returncode}); threshold cannot be certified')
    words = output.read_text(encoding='ascii').splitlines()
    if len(words) > budget or any(not w for w in words):
        raise ValueError('OMEN returned an invalid prefix')
    training.unlink()
    return words, {'raw_count': len(words), 'exhausted': len(words) < budget,
                   'min_length': settings['order'], 'max_length': 19, 'alphabet': alphabet,
                   'training_used': sum(supported.values()), 'training_total': sum(counts.values()),
                   'ordering': 'author OMEN quantized levels, stdout, no target feedback'}


class DualAttackIndex:
    dual_attack = True

    def __init__(self, grammar, streams, budget, metadata, *, exhausted=None):
        if set(streams) != {'pcfg', 'markov'} or budget < 1:
            raise ValueError('Two mandatory attack prefixes and a positive budget are required')
        self.grammar, self.budget, self.metadata = grammar, budget, metadata
        self.seed = metadata.get('seed', 42)
        self.ranks, self.raw_counts, self.exhausted = {}, {}, dict(exhausted or {})
        for name, words in streams.items():
            ranks = {}
            for i, word in enumerate(words, 1):
                if not isinstance(word, str) or not word or '\n' in word or '\r' in word:
                    raise ValueError('Invalid attack candidate')
                ranks.setdefault(word, i)
            self.ranks[name] = ranks
            self.raw_counts[name] = len(words)
            if len(words) > budget or (len(words) < budget and not self.exhausted.get(name)):
                raise ValueError('Incomplete attack prefix cannot certify the individual threshold')
        self.n = sum(self.raw_counts.values())
        self._cache = {}
        union = dict(self.ranks['pcfg'])
        for word, rank in self.ranks['markov'].items():
            union[word] = min(rank, union.get(word, rank))
        self.union_ranks = union
        self.sorted_ranks = {name: sorted(ranks.values()) for name, ranks in {**self.ranks, 'union': union}.items()}
        self.grammar.metadata = {**self.grammar.metadata, 'attack_method': VERSION,
                                 'markov': metadata['markov'], 'prefix_budget_per_model': budget}

    def supported(self, word):
        m = self.metadata['markov']
        return (self.grammar.probability(word) > 0 or
                (m['min_length'] <= len(word) <= m['max_length'] and
                 all(c in m['alphabet'] for c in word)))

    def query(self, word, rule=None):
        if rule is not None:
            raise ValueError('Intervention prefixes do not apply a whole-site policy mask')
        if word not in self._cache:
            rank = self.union_ranks.get(word)
            status = ('observed_hit' if rank is not None else
                      'beyond_tested_budget' if self.supported(word) else 'outside_model_support')
            self._cache[word] = {'guess_count': rank, 'status': status, 'estimated': False,
                                'rank_lower_bound': rank if rank is not None else self.budget + 1,
                                'pcfg_guess_count': self.ranks['pcfg'].get(word),
                                'markov_guess_count': self.ranks['markov'].get(word),
                                'budget_unit': 'raw_attempts_per_model', 'prefix_complete': True}
        return self._cache[word]

    def passes_threshold(self, word, budget):
        if budget > self.budget:
            raise ValueError('Individual threshold exceeds generated attack prefixes')
        detail = self.query(word)
        # Out-of-domain strings cannot pass by exploiting both models' limits.
        return (detail['status'] != 'outside_model_support' and
                (detail['guess_count'] is None or detail['guess_count'] > budget))

    def evaluate(self, counts, budgets):
        if max(budgets) > self.budget:
            raise ValueError('Evaluation exceeds attack prefix budget')
        total = sum(counts.values())
        outside = sum(n for w, n in counts.items() if self.query(w)['status'] == 'outside_model_support')
        curves = {}
        for name, ranks in {**self.ranks, 'union': self.union_ranks}.items():
            points = []
            for b in budgets:
                hits = sum(n for w, n in counts.items() if ranks.get(w, b + 1) <= b)
                attempts = (sum(min(b, n) for n in self.raw_counts.values()) if name == 'union'
                            else min(b, self.raw_counts[name]))
                points.append({'budget': b, 'hits': hits, 'rate': hits/total if total else 0.,
                               'target_weight': total, 'complete': True, 'estimated': False,
                               'status': 'observed_prefix', 'charged_attempts': attempts,
                               'unique_guesses': bisect.bisect_right(self.sorted_ranks[name], b),
                               'outside_model_support_weight': outside,
                               'low_sample_support_weight': 0, 'unresolved_weight': outside,
                               'pending_weight': 0})
            curves[name] = {'minauto': points, 'method': VERSION, 'attacker': name}
        return {'minauto': curves['union']['minauto'], 'attack_curves': curves,
                'method': VERSION, 'estimated': False, 'models': [
                    {'run': {'model': name, 'method': VERSION, 'estimated': False,
                             'raw_generated_count': self.raw_counts[name], 'exhausted': self.exhausted.get(name, False)},
                     'points': curves[name]['minauto']} for name in self.ranks],
                'budget_unit': 'raw_attempts_per_model', 'union_budget_multiplier': 2,
                'denominator': 'all original accounts, frequency weighted',
                'interpretation': 'Union of observed hits; B attempts per model, at most 2B total; duplicates charged; no target filtering'}


def build_dual_index(counts, cfg):
    settings = cfg['attack_models']
    budget = max(max(cfg['budgets']), settings['threshold'])
    root = ROOT / 'reports' / 'intervention' / 'dual_attack_cache'
    root.mkdir(parents=True, exist_ok=True)
    identity = {'version': VERSION, 'training_sha256': counts_hash(counts), 'budget': budget,
                'settings': settings, 'pcfg_commit': EXPECTED_COMMIT, 'omen': omen_identity(),
                'implementation_sha256': file_hash(__file__),
                'pcfg_adapter_sha256': file_hash(ROOT / 'ai' / 'pcfg_adapter.py')}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    path = root / (key + '.json.gz')
    grammar = Grammar.fit(counts, runtime_root=ROOT / 'reports' / 'intervention' / 'pcfg_runtime',
                          timeout=cfg['pcfg']['timeout_seconds'])
    if path.exists():
        expected = path.with_suffix('.sha256').read_text().strip()
        if file_hash(path) != expected:
            raise ValueError('Attack prefix cache checksum mismatch')
        with gzip.open(path, 'rt', encoding='utf-8') as handle:
            data = json.load(handle)
        if data['identity'] != identity:
            raise ValueError('Attack cache identity mismatch')
    else:
        adapter = PCFGAttacker(PCFGConfig.workspace_default(
            generation_limit=budget, timeout_seconds=int(cfg['pcfg']['timeout_seconds'])))
        # Share the existing trained runtime without changing the source tree.
        from dataclasses import replace
        adapter = PCFGAttacker(replace(adapter.config, runtime_root=ROOT / 'reports' / 'intervention' / 'pcfg_runtime'))
        train = [w for w, n in sorted(counts.items()) for _ in range(n)]
        pcfg, pcfg_meta = adapter.generate_open(train)
        work = Path(tempfile.mkdtemp(prefix='omen-', dir=root))
        markov, markov_meta = omen_prefix(counts, settings, budget, work)
        data = {'identity': identity, 'streams': {'pcfg': list(pcfg), 'markov': markov},
                'exhausted': {'pcfg': pcfg_meta['source_stop'] == 'exhausted', 'markov': markov_meta['exhausted']},
                'metadata': {'seed': cfg['seed'], 'markov': markov_meta, 'sources': identity}}
        temporary = work / 'cache.json.gz'
        with gzip.open(temporary, 'wt', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=True)
        temporary.replace(path)
        path.with_suffix('.sha256').write_text(file_hash(path), encoding='ascii')
    return DualAttackIndex(grammar, data['streams'], budget, data['metadata'], exhausted=data['exhausted'])
