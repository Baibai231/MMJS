"""Identity-preserving state. Failed responses keep the old password and risk."""
from collections import Counter
from dataclasses import dataclass, replace
from core.corpus import counts_hash
from core.uniformity import concentration


@dataclass
class Account:
    identifier: str
    password: str
    notifications: int = 0
    modifications: int = 0
    edit_cost: float = 0.
    outcome: str = 'untouched'


class Population:
    def __init__(self, words, prefix='account'):
        self.accounts = [Account(f'{prefix}-{i}', word) for i, word in enumerate(words)]
        if not self.accounts:
            raise ValueError('账户不能为空')

    def clone(self):
        other = object.__new__(Population)
        other.accounts = [replace(a) for a in self.accounts]
        return other

    @property
    def total(self):
        return len(self.accounts)

    def counts(self):
        return Counter(a.password for a in self.accounts)

    def fingerprint(self):
        return counts_hash(self.counts())

    def ledger(self):
        n = self.total
        affected = sum(a.notifications > 0 for a in self.accounts)
        changed = sum(a.modifications > 0 for a in self.accounts)
        events = sum(a.modifications for a in self.accounts)
        return {'accounts': n, 'affected': affected, 'affected_rate': affected/n,
                'changed': changed, 'changed_rate': changed/n, 'modification_events': events,
                'event_rate': events/n, 'edit_cost': sum(a.edit_cost for a in self.accounts)/n,
                'nonresponse': sum(a.outcome == 'nonresponse' for a in self.accounts),
                'failed_to_comply': sum(a.outcome == 'failed_to_comply' for a in self.accounts)}

    def apply(self, outcomes):
        ids = [row['index'] for row in outcomes]
        if len(set(ids)) != len(ids):
            raise ValueError('同一动作不能重复选择账户')
        # Validate the entire transaction before mutating any account.
        for row in outcomes:
            i = row['index']
            if not 0 <= i < self.total or self.accounts[i].notifications:
                raise ValueError('无效或已干预的账户')
            a = self.accounts[i]
            if row['old'] != a.password or not isinstance(row['new'], str):
                raise ValueError('响应不属于当前账户状态')
            if row['status'] not in ('changed', 'nonresponse', 'failed_to_comply'):
                raise ValueError('未知响应状态')
            if (row['new'] != row['old']) != (row['status'] == 'changed'):
                raise ValueError('响应状态与实际修改不一致')
        for row in outcomes:
            a = self.accounts[row['index']]
            a.password = row['new']
            a.notifications += 1
            a.modifications += row['status'] == 'changed'
            a.edit_cost += row['edit_cost']
            a.outcome = row['status']
        assert sum(self.counts().values()) == self.total


def distribution_summary(counts):
    result = concentration(Counter(counts), full_curve=True)
    n = sum(counts.values())
    result['hhi'] = sum((c/n)**2 for c in counts.values())
    return result
