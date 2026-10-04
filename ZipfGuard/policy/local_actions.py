"""State-derived groups, local rules and deterministic partial-account selection."""
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import math
import re
from policy.open_policy import Rule
from policy.engine import extract_features
from policy.intervention_fragments import LENGTHS, Fragment, candidate_fragments
from core.htpg_features import lsd_structure


@lru_cache(maxsize=200000)
def features(word):
    f = extract_features(word)
    f['repeat_pattern'] = bool(re.search(r'(.{1,3})\1{2,}', word))
    f['structure'] = lsd_structure(word)
    return f


@dataclass(frozen=True)
class LocalRule:
    base: Rule
    repeat_pattern: bool = False
    fragment: Fragment | None = None

    def accepts(self, word):
        return (self.base.accepts(word)
                and not (self.repeat_pattern and features(word)['repeat_pattern'])
                and (self.fragment is None or self.fragment.accepts(word)))

    def summary(self):
        return {**self.base.summary(), 'block_repeat_pattern': self.repeat_pattern,
                **(self.fragment.summary() if self.fragment else {})}


@dataclass(frozen=True)
class Group:
    kind: str
    value: str
    label: str
    excluded_hot: frozenset = frozenset()

    def matches(self, word):
        if self.kind in ('all', 'random'):
            return True
        if self.kind == 'hot':
            return word == self.value
        return word not in self.excluded_hot and features(word)['structure'] == self.value


@dataclass(frozen=True)
class Action:
    group: Group
    rule: LocalRule
    rule_label: str
    indices: tuple
    eligible_count: int

    def public(self):
        return {'group': self.group.label, 'rule': self.rule_label,
                'rule_definition': self.rule.summary(), 'selected': len(self.indices),
                'eligible_group_accounts': self.eligible_count,
                'selected_group_fraction': len(self.indices)/self.eligible_count}


@dataclass(frozen=True)
class MultiAction:
    """Disjoint rule assignments for a cohort drawn before choosing rules."""
    components: tuple[Action, ...]
    label: str = '随机抽取账户'

    @property
    def indices(self):
        return tuple(i for action in self.components for i in action.indices)

    def public(self):
        return {'group': self.label, 'rule': f'{len(self.components)} 条规则分配给随机抽样账户',
                'rule_definition': {'min_length': min(a.rule.base.min_length
                                                    for a in self.components)},
                'selected': len(self.indices), 'eligible_group_accounts': len(self.indices),
                'selected_group_fraction': 1.,
                'components': [action.public() for action in self.components]}


def account_order(population, indices, seed):
    return sorted(indices, key=lambda i: hashlib.sha256(
        f'{seed}|selection|{population.accounts[i].identifier}'.encode()).digest())


def capacities(population, cfg):
    c = cfg['controller']
    per_round = math.floor(population.total*c['round_fraction'] + 1e-9)
    remaining = math.floor(population.total*c['total_fraction'] + 1e-9) - population.ledger()['adaptive_affected']
    return max(0, min(per_round, remaining))


def build_groups(population, risk, cfg):
    counts = population.counts()
    c = cfg['controller']
    ranked = sorted(counts, key=lambda w: (-counts[w], w))
    hot = frozenset(w for w in ranked[:c['popular_k']] if counts[w] >= 2)
    groups = [Group('hot', w, f'热门口令第 {i+1} 位账户')
              for i, w in enumerate(ranked[:c['popular_k']]) if w in hot]
    structures = sorted({features(a.password)['structure'] for a in population.accounts
                         if not a.adaptive_notifications and a.password not in hot})
    groups += [Group('structure', s, f'{s} 结构账户', hot) for s in structures]
    buckets = {g: [] for g in groups}
    hot_groups = {g.value: g for g in groups if g.kind == 'hot'}
    structure_groups = {g.value: g for g in groups if g.kind == 'structure'}
    for i, a in enumerate(population.accounts):
        if not a.adaptive_notifications:
            g = hot_groups.get(a.password) or structure_groups.get(features(a.password)['structure'])
            if g is not None:
                buckets[g].append(i)
    scores = {g: sum(risk.hit(population.accounts[i].password) for i in ids)
              for g, ids in buckets.items()}
    ordered = sorted((g for g in groups if scores[g] > 0), key=lambda g: (-scores[g], g.label))
    # Reserve opportunities for non-head structures as well as precise hotspots.
    limit = c['max_groups']
    structural = [g for g in ordered if g.kind == 'structure'][:max(1, limit//3)]
    selected = structural + [g for g in ordered if g not in structural][:limit-len(structural)]
    return [(g, buckets[g]) for g in selected], hot


def generate_actions(population, risk, cfg, *, fixed=False, minimum_length=0,
                     catalog_reference=None, round_id=1):
    cap = capacities(population, cfg)
    if cap <= 0:
        return []
    c = cfg['controller']
    minimum_length = max(minimum_length, c.get('policy_floor_minimum_length', 0))
    if fixed:
        rule = LocalRule(Rule('fixed-google-length-8', min_length=8))
        ids = [i for i, a in enumerate(population.accounts) if not a.adaptive_notifications and not rule.accepts(a.password)]
        # Fixed rule, risk-first targets: a stronger baseline than random selection.
        order = account_order(population, ids, cfg['seed'])
        order.sort(key=lambda i: -risk.hit(population.accounts[i].password))
        return [Action(Group('all', '', '固定长度要求的不合规账户'), rule,
                       '固定 Google 规则子集：至少 8 字符', tuple(order[:cap]), len(ids))] if ids else []
    groups, hot = build_groups(population, risk, cfg)
    actions = []
    fragments = candidate_fragments(
        hot=hot, development=catalog_reference,
        predictable_terms=c.get('predictable_terms', ['gitlab', 'devops']))
    for group, ids in groups:
        for fragment in fragments:
            if fragment.number in LENGTHS and LENGTHS[fragment.number] not in c['lengths']:
                continue
            rule = LocalRule(Rule(f'fragment-{fragment.number}',
                                  min_length=max(minimum_length, LENGTHS.get(fragment.number, 0))),
                             fragment=fragment)
            eligible = [i for i in ids if not rule.accepts(population.accounts[i].password)]
            if not eligible:
                continue
            order = account_order(population, eligible, cfg['seed'])
            sizes = {min(cap, len(order), max(1, math.floor(population.total*f + 1e-9)))
                     for f in c['batch_fractions']}
            for n in sorted(sizes):
                if n:
                    actions.append(Action(group, rule, f'第 {fragment.number} 条：{fragment.label}',
                                          tuple(order[:n]), len(eligible)))
    # Cross-structure choices use the same 18 rules as the random comparator.
    # Keeping its exact seeded cohort in this pool makes same-state comparisons
    # meaningful: targeted selection can always consider that random action.
    available = [i for i, a in enumerate(population.accounts) if not a.adaptive_notifications]
    random_order = sorted(available, key=lambda i: hashlib.sha256(
        f'{cfg["seed"]}|random-cohort|{round_id}|{population.accounts[i].identifier}'.encode()).digest())
    random_rank = {i: j for j, i in enumerate(random_order)}
    counts = population.counts()
    for fragment in fragments:
        if fragment.number in LENGTHS and LENGTHS[fragment.number] not in c['lengths']:
            continue
        rule = LocalRule(Rule(f'fragment-{fragment.number}',
                              min_length=max(minimum_length, LENGTHS.get(fragment.number, 0))),
                         fragment=fragment)
        eligible = [i for i in available if not rule.accepts(population.accounts[i].password)]
        if not eligible:
            continue
        sizes = {min(cap, len(eligible), max(1, math.floor(population.total*f + 1e-9)))
                 for f in c['batch_fractions']}
        ranked = sorted(eligible, key=lambda i: (-risk.hit(population.accounts[i].password),
                                                  -counts[population.accounts[i].password],
                                                  hashlib.sha256(f'{cfg["seed"]}|selection|{population.accounts[i].identifier}'.encode()).digest()))
        random_eligible = sorted(eligible, key=lambda i: random_rank[i])
        label = f'第 {fragment.number} 条：{fragment.label}'
        for size in sorted(sizes):
            actions.append(Action(Group('all', '', '跨结构高风险账户'), rule, label,
                                  tuple(ranked[:size]), len(eligible)))
            actions.append(Action(Group('random', '', '随机抽取账户'), rule, label,
                                  tuple(random_eligible[:size]), len(eligible)))
    return actions
