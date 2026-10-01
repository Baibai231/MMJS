"""State-derived groups, local rules and deterministic partial-account selection."""
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import math
import re
from policy.open_policy import Rule
from policy.engine import extract_features
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

    def accepts(self, word):
        return self.base.accepts(word) and not (self.repeat_pattern and features(word)['repeat_pattern'])

    def summary(self):
        return {**self.base.summary(), 'block_repeat_pattern': self.repeat_pattern}


@dataclass(frozen=True)
class Group:
    kind: str
    value: str
    label: str
    excluded_hot: frozenset = frozenset()

    def matches(self, word):
        if self.kind == 'all':
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


def account_order(population, indices, seed):
    return sorted(indices, key=lambda i: hashlib.sha256(
        f'{seed}|selection|{population.accounts[i].identifier}'.encode()).digest())


def capacities(population, cfg):
    c = cfg['controller']
    per_round = math.floor(population.total*c['round_fraction'] + 1e-9)
    remaining = math.floor(population.total*c['total_fraction'] + 1e-9) - population.ledger()['affected']
    return max(0, min(per_round, remaining))


def build_groups(population, risk, cfg):
    counts = population.counts()
    c = cfg['controller']
    ranked = sorted(counts, key=lambda w: (-counts[w], w))
    hot = frozenset(w for w in ranked[:c['popular_k']] if counts[w] >= 2)
    groups = [Group('hot', w, f'热门口令第 {i+1} 位账户')
              for i, w in enumerate(ranked[:c['popular_k']]) if w in hot]
    structures = sorted({features(a.password)['structure'] for a in population.accounts
                         if not a.notifications and a.password not in hot})
    groups += [Group('structure', s, f'{s} 结构账户', hot) for s in structures]
    buckets = {g: [] for g in groups}
    hot_groups = {g.value: g for g in groups if g.kind == 'hot'}
    structure_groups = {g.value: g for g in groups if g.kind == 'structure'}
    for i, a in enumerate(population.accounts):
        if not a.notifications:
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


def generate_actions(population, risk, cfg, *, fixed=False):
    cap = capacities(population, cfg)
    if cap <= 0:
        return []
    c = cfg['controller']
    if fixed:
        rule = LocalRule(Rule('fixed-google-length-8', min_length=8))
        ids = [i for i, a in enumerate(population.accounts) if not a.notifications and not rule.accepts(a.password)]
        # Fixed rule, risk-first targets: a stronger baseline than random selection.
        order = account_order(population, ids, cfg['seed'])
        order.sort(key=lambda i: -risk.hit(population.accounts[i].password))
        return [Action(Group('all', '', '固定长度要求的不合规账户'), rule,
                       '固定 Google 规则子集：至少 8 字符', tuple(order[:cap]), len(ids))] if ids else []
    groups, hot = build_groups(population, risk, cfg)
    actions = []
    for group, ids in groups:
        rules = [(LocalRule(Rule('deny-popular', blocklist=hot)), '避开本轮热门名单')]
        words = [population.accounts[i].password for i in ids]
        if any(features(w)['repeat_pattern'] for w in words):
            rules.append((LocalRule(Rule('deny-repeat'), True), '避免 1–3 字符片段连续重复三次'))
        for feature, label in [('sequential_digits', '避免连续数字模式'), ('keyboard_walk', '避免键盘连续模式')]:
            if any(features(w)[feature] for w in words):
                rules.append((LocalRule(Rule('deny-'+feature, deny=(feature,))), label))
        for length in c['lengths']:
            if any(len(w) < length for w in words):
                rules.append((LocalRule(Rule(f'local-length-{length}', min_length=length)), f'本批最低长度 {length} 字符'))
        for rule, label in rules:
            eligible = [i for i in ids if not rule.accepts(population.accounts[i].password)]
            if not eligible:
                continue
            order = account_order(population, eligible, cfg['seed'])
            sizes = {min(cap, len(order), max(1, math.floor(population.total*f + 1e-9)))
                     for f in c['batch_fractions']}
            for n in sorted(sizes):
                if n:
                    actions.append(Action(group, rule, label, tuple(order[:n]), len(eligible)))
    return actions
