"""Finite, attack-blind response for existing accounts (not registration)."""
from policy.open_policy import edit_distance
from policy.user_response import _rng, _propose, weighted_pool, phrase_vocabulary

RESPONSE_PROTOCOL = 'existing-accounts-finite-response-rank-guard-v2'


class InterventionResponder:
    def __init__(self, reference, config, rank_model=None):
        self.reference = dict(reference)
        self.pool = weighted_pool(reference)
        self.vocabulary = phrase_vocabulary(reference)
        self.config = dict(config)
        self.rank_model = rank_model

    def stronger(self, old, new):
        """Only comparable fixed-model ranks can certify a local improvement."""
        if self.rank_model is None:
            return True
        before = self.rank_model.detail(old)['guess_count']
        after = self.rank_model.detail(new)['guess_count']
        return before is not None and after is not None and after > before

    def respond(self, population, action, seed, stream):
        if hasattr(action, 'components'):
            return [row for component in action.components
                    for row in self.respond(population, component, seed, stream)]
        rows = []
        for i in action.indices:
            account = population.accounts[i]
            old = account.password
            if action.rule.accepts(old):
                rows.append({'index': i, 'old': old, 'new': old,
                             'status': 'already_compliant', 'attempts': 0, 'edit_cost': 0.})
                continue
            rng = _rng(seed, f'{stream}|{account.identifier}')
            new, status, attempts = old, 'nonresponse', 0
            strength_rejections = 0
            if rng.random() >= self.config['nonresponse']:
                draw = rng.random()
                w = self.config['weights']
                kind = 'repair' if draw < w[0] else 'segment' if draw < w[0] + w[1] else 'reselect'
                status = 'failed_to_comply'
                for attempts in range(1, self.config['max_attempts'] + 1):
                    proposal = _propose(old, kind, rng, self.pool, self.vocabulary)
                    if proposal != old and action.rule.accepts(proposal):
                        if self.stronger(old, proposal):
                            new, status = proposal, 'changed'
                            break
                        strength_rejections += 1
            rows.append({'index': i, 'old': old, 'new': new, 'status': status,
                         'attempts': attempts,
                         'strength_rejections': strength_rejections,
                         'edit_cost': edit_distance(old, new)/max(1, len(old), len(new)) if new != old else 0.})
        return rows
