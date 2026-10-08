"""Versioned, attack-blind simulated response for existing accounts."""
import re
from policy.open_policy import edit_distance
from policy.user_response import (_rng, _propose, weighted_pool, phrase_vocabulary,
                                  satisfy_site_rules, CHARACTER_GROUPS)
from policy.intervention_fragments import KEYBOARD, TRIPLE, sequence_four

RESPONSE_PROTOCOL = 'existing-accounts-all-notified-success-v4'


def complete_local_rule(old, proposal, rule, rng):
    """Construct a different compliant password, without consulting attack ranks."""
    if hasattr(rule, 'complete'):
        completed = rule.complete(proposal, rng)
        if completed != old and rule.accepts(completed):
            return completed
    base = getattr(rule, 'base', rule)
    fragment = getattr(rule, 'fragment', None)
    alphabet = ''.join(CHARACTER_GROUPS)
    word = proposal
    for _ in range(128):
        word = satisfy_site_rules(word, base, rng)
        if fragment:
            n = fragment.number
            required = {4: CHARACTER_GROUPS[0], 5: CHARACTER_GROUPS[0],
                        6: CHARACTER_GROUPS[1], 7: CHARACTER_GROUPS[2], 8: CHARACTER_GROUPS[2]}
            if n in required and not fragment.accepts(word):
                word += rng.choice(required[n])
            elif n == 9 and not fragment.accepts(word):
                word += rng.choice(CHARACTER_GROUPS[0])+rng.choice(CHARACTER_GROUPS[2])
            elif n == 10:
                while len(set(word)) < 4:
                    word += rng.choice(''.join(c for c in alphabet if c not in word))
            elif n == 11:
                word = TRIPLE.sub(lambda m: m.group(0)[:2]+rng.choice(
                    ''.join(c for c in alphabet if c != m.group(0)[0])), word)
            elif n == 12 and not fragment.accepts(word):
                word += rng.choice(''.join(c for c in alphabet if c not in word))
            elif n == 13:
                word = KEYBOARD.sub(lambda m: m.group(0)[:2]+'!'+m.group(0)[3:], word)
            elif n == 14:
                chars = list(word)
                for i in range(max(0, len(chars)-3)):
                    if sequence_four(''.join(chars[i:i+4])):
                        chars[i+2] = rng.choice('!@#$%&*?')
                word = ''.join(chars)
            elif n == 16:
                for term in fragment.terms:
                    word = re.sub(re.escape(term), lambda m: m.group(0)[:len(m.group(0))//2]+'!'+
                                  m.group(0)[len(m.group(0))//2+1:], word, flags=re.I)
            elif n in (15, 17, 18) and not fragment.accepts(word):
                word += rng.choice(CHARACTER_GROUPS[2]+'!@#')
        if word == old:
            word += rng.choice(alphabet)
        if word != old and rule.accepts(word):
            return word
        # Repair a bounded-length or other legacy rule by constructing a fresh
        # candidate. Exhaustion is an explicit error, never a failed account.
        if getattr(base, 'max_length', None) is not None or getattr(rule, 'repeat_pattern', False):
            length = max(base.min_length, 8)
            if base.max_length is not None:
                length = min(length, base.max_length)
            word = ''.join(rng.choice(alphabet) for _ in range(length))
        else:
            word += rng.choice(alphabet)
    raise ValueError('无法构造满足当前规则的不同口令；全员成功模式不能记录为修改失败')


class InterventionResponder:
    def __init__(self, reference, config, rank_model=None):
        self.reference = dict(reference)
        self.pool = weighted_pool(reference)
        self.vocabulary = phrase_vocabulary(reference)
        self.config = dict(config)
        self.rank_model = rank_model

    def stronger(self, old, new):
        """Diagnostic only; response acceptance is now a population-level decision."""
        if self.rank_model is None:
            return True
        before = self.rank_model.detail(old)['guess_count']
        after = self.rank_model.detail(new)['guess_count']
        return before is not None and after is not None and after > before

    def preview(self, population, action, seed, stream):
        return self.respond(population, action, seed, stream, record_edit_cost=False)

    def respond(self, population, action, seed, stream, *, record_edit_cost=True):
        if hasattr(action, 'components'):
            return [row for component in action.components
                    for row in self.respond(population, component, seed, stream, record_edit_cost=record_edit_cost)]
        rows = []
        for i in action.indices:
            account = population.accounts[i]
            old = account.password
            if self.config.get('mode') == 'all-notified-change-v1':
                rng = _rng(seed, f'{stream}|{account.identifier}')
                draw = rng.random()
                weights = self.config['weights']
                kind = 'repair' if draw < weights[0] else 'segment' if draw < weights[0]+weights[1] else 'reselect'
                proposal = old
                completed = False
                for attempts in range(1, self.config['max_attempts']+1):
                    proposal = _propose(old, kind, rng, self.pool, self.vocabulary)
                    if proposal != old and action.rule.accepts(proposal):
                        break
                else:
                    proposal = complete_local_rule(old, proposal, action.rule, rng)
                    completed = True
                if proposal == old or not action.rule.accepts(proposal):
                    raise AssertionError('全员修改模式产生了未成功或不合规的修改')
                rows.append({'index': i, 'old': old, 'new': proposal, 'status': 'changed',
                             'attempts': attempts, 'response_kind': kind,
                             'explicit_completion': completed,
                             'edit_cost': (edit_distance(old, proposal)/max(1, len(old), len(proposal)) if record_edit_cost else 0.)})
                continue
            if action.rule.accepts(old):
                rows.append({'index': i, 'old': old, 'new': old,
                             'status': 'already_compliant', 'attempts': 0, 'edit_cost': 0.})
                continue
            rng = _rng(seed, f'{stream}|{account.identifier}')
            new, status, attempts = old, 'nonresponse', 0
            if rng.random() >= self.config['nonresponse']:
                draw = rng.random()
                w = self.config['weights']
                kind = 'repair' if draw < w[0] else 'segment' if draw < w[0] + w[1] else 'reselect'
                status = 'failed_to_comply'
                for attempts in range(1, self.config['max_attempts'] + 1):
                    proposal = _propose(old, kind, rng, self.pool, self.vocabulary)
                    if proposal != old and action.rule.accepts(proposal):
                        new, status = proposal, 'changed'
                        break
            rows.append({'index': i, 'old': old, 'new': new, 'status': status,
                         'attempts': attempts,
                         'edit_cost': edit_distance(old, new)/max(1, len(old), len(new)) if record_edit_cost and new != old else 0.})
        return rows
