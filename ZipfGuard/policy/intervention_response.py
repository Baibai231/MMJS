"""Finite, attack-blind response for existing accounts (not registration)."""
from policy.open_policy import edit_distance
from policy.user_response import _rng, _propose, weighted_pool, phrase_vocabulary

RESPONSE_PROTOCOL = 'existing-accounts-finite-response-v1'


class InterventionResponder:
    def __init__(self, reference, config):
        self.pool = weighted_pool(reference)
        self.vocabulary = phrase_vocabulary(reference)
        self.config = dict(config)

    def respond(self, population, action, seed, stream):
        rows = []
        for i in action.indices:
            account = population.accounts[i]
            old = account.password
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
                         'edit_cost': edit_distance(old, new)/max(1, len(old), len(new)) if new != old else 0.})
        return rows
