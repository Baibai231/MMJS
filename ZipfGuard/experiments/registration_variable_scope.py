"""Variable risk coverage and isolated, reusable actual dual-model evaluation."""
from pathlib import Path
from dataclasses import dataclass
from collections import Counter
import hashlib
import shutil

from core.corpus import counts_hash
from policy.registration_joint import candidates, Preferences, stable_seed, VERSION as PREFERENCE_VERSION
from itertools import count
import random
from experiments.registration_joint_pilot import GradedRule, graded_candidates
from experiments.registration_joint_data import load_private
from experiments.registration_dual_pipeline import DualAttackCache, risk
from ai.pcfg_monte_carlo import Grammar


class UnlimitedPreferences(Preferences):
    """Identical preference stream, continued until an accepted proposal; no retry cap."""
    def proposals(self, original, identifier, seed):
        yield original
        rng = random.Random(stable_seed(seed, identifier, PREFERENCE_VERSION))
        suffixes = ('1', '!', '123', '2026', '2026!', '@1', '01', '1234', '7!', '123!')
        for attempt in count(1):
            if attempt == 1 and len(original) < self.minimum:
                word = original
            else:
                draw = rng.random()
                kind = ('reselect' if self.mode == 'independent' else
                        'repair' if self.mode == 'minimal' or draw < self.weights[0] else
                        'phrase' if draw < sum(self.weights[:2]) else 'reselect')
                if kind == 'reselect':
                    word = self.pool[rng.randrange(len(self.pool))]
                elif kind == 'phrase':
                    a = self.pool[rng.randrange(len(self.pool))]
                    b = self.pool[rng.randrange(len(self.pool))]
                    word = a[:4] + b[:4] + suffixes[rng.randrange(len(suffixes))]
                else:
                    op = rng.randrange(7)
                    tail = suffixes[rng.randrange(len(suffixes))]
                    word = (original + tail if op == 0 else tail + original if op == 1 else
                            original.capitalize() + tail if op == 2 else original.upper() + tail if op == 3 else
                            original[:len(original)//2] + tail + original[len(original)//2:] if op == 4 else
                            original[:-1] + tail if op == 5 else original + tail + tail)
            if len(word) < self.minimum:
                word += '1' * (self.minimum - len(word))
            yield word

    def simulate(self, *args, **kwargs):
        if kwargs.get('patience') is not None:
            raise ValueError('This policy has no per-user modification limit')
        result = super().simulate(*args, **kwargs)
        result['summary']['terminal_state'] = 'continue_until_accepted_no_retry_cap'
        return result



@dataclass(frozen=True)
class ScopedRule(GradedRule):
    risk_cutoff: int = 0

    def summary(self):
        return {**super().summary(), 'risk_membership_budget_per_model': self.risk_cutoff}


def scoped_candidates(base, recent, hold, ks, js, memberships):
    # Keep existing exact/whole-structure options; add selective restrictions
    # for every predeclared coverage level. Duplicate behavior is evaluated once.
    output = candidates(base, recent, hold, ks, js)
    seen = {r.signature() for r, _ in output}
    for cutoff, guessed in sorted(memberships.items()):
        for r, is_hold in graded_candidates(base, recent, hold, ks, js, guessed):
            if not isinstance(r, GradedRule) or r.signature() in seen:
                continue
            rule = ScopedRule(minimum=r.minimum, common=r.common, exact=r.exact,
                              shapes=r.shapes, k=r.k, j=r.j, strength=r.strength,
                              index=r.index, soft_shapes=r.soft_shapes, guessed=r.guessed,
                              risk_cutoff=cutoff if r.soft_shapes else 0)
            seen.add(rule.signature())
            output.append((rule, is_hold))
    return output


class ReusingDualAttackCache(DualAttackCache):
    def __init__(self, directory, cfg, log, extra_roots=()):
        super().__init__(directory, cfg, log)
        self.old_roots = list(map(Path, extra_roots)) + [Path(p) for p in cfg.get('reuse_attack_roots', [])]

    def raw(self, counts, budget, model):
        key = counts_hash(counts)
        destination = self.root/'prefixes'/f'{key}-{model}-{budget}.pkl.gz'
        if not destination.exists():
            for old in self.old_roots:
                source = old/'prefixes'/destination.name
                if source.exists():
                    prefix = load_private(source)
                    if not prefix['complete'] or prefix['raw_emissions'] != budget:
                        raise ValueError('Invalid reusable attack prefix')
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                    self.log(f'Reused identical {model} training prefix: {key[:12]}')
                    break
        return super().raw(counts, budget, model)

    def grammar(self, counts):
        key = counts_hash(counts)
        destination = self.root/'grammars'/f'{key}.pkl.gz'
        if not destination.exists():
            for old in self.old_roots:
                source = old/'grammars'/destination.name
                if source.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                    break
        return super().grammar(counts)


def score_training_group(task):
    root = Path(task['root'])/'jobs'/task['key']
    cache = ReusingDualAttackCache(root, task['cfg'], lambda m: None, [task['root']])
    cfg = task['cfg']
    index = cache.index(task['counts'], purpose='selection')
    budget = cfg['objective_budget']
    h = risk(index, task['history'], [budget])
    n = sum(task['history'].values())
    size = task['cohort_size']
    output = []
    for item in task['items']:
        p = risk(index, item['preview_counts'], [budget], cfg['preview_users'])
        frozen = sum(v for w, v in item['preview_counts'].items() if w in task['frozen_ranks'])/cfg['preview_users']
        output.append({'number': item['number'], 'score': {
            'predicted_A1': (n*h['rates'][str(budget)]+size*p['rates'][str(budget)])/(n+size),
            'predicted_outside': (n*h['outside_model_support_fraction']+size*p['outside_model_support_fraction'])/(n+size),
            'frozen_rate': frozen, 'history_actual_dual': h, 'preview_actual_dual': p,
            'training_sha256': task['key'],
            'training_role': 'train only; structure reweighting; no preview or target plaintext'}})
    return {'root': str(root), 'key': task['key'], 'items': output}


def promote_group(source_root, destination_root, key, budget):
    # Parent alone mutates shared cache; workers have independent runtime folders.
    for folder, names in [('prefixes', [f'{key}-{m}-{budget}.pkl.gz' for m in ('pcfg', 'omen')]),
                          ('grammars', [f'{key}.pkl.gz'])]:
        for name in names:
            source, destination = Path(source_root)/folder/name, Path(destination_root)/folder/name
            if not source.exists():
                raise RuntimeError('Missing completed candidate evidence')
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if hashlib.sha256(destination.read_bytes()).digest() != hashlib.sha256(source.read_bytes()).digest():
                    # gzip metadata can differ; compare the underlying evidence.
                    if load_private(destination) != load_private(source):
                        raise RuntimeError('Conflicting evidence for identical training')
            else:
                shutil.copy2(source, destination)
