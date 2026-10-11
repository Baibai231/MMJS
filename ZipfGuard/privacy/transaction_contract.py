"""Public transaction discipline, independent of any plaintext password.

The computation parties each apply this guard and sign their result. A website
alone cannot mint a three-party acceptance receipt. This in-memory prototype
does not provide durable distributed consensus or protection against disk rollback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import json


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')


@dataclass
class TransactionGuard:
    epoch: int = 0
    policy_version: int = 1
    query_limit: int = 4
    epsilon_cap: float = 1.0
    epsilon_spent: float = 0.0
    seen: dict = field(default_factory=dict)
    queries: dict = field(default_factory=dict)
    release_cache: dict = field(default_factory=dict)
    task_cap: int = 1
    tasks: dict = field(default_factory=dict)

    def before(self, request, digest, account_versions):
        nonce = request['nonce']
        if nonce in self.seen:
            old_digest, receipt = self.seen[nonce]
            return ('replay', receipt) if old_digest == digest else ('nonce_conflict', None)
        if request['op'] in ('register', 'change', 'eligibility', 'assign_task') and request['policy_version'] != self.policy_version:
            return 'stale_policy', None
        account = request.get('account')
        if request['op'] in ('change', 'login', 'eligibility', 'assign_task'):
            if account not in account_versions:
                return 'unknown_account', None
            if request['account_version'] != account_versions[account]:
                return 'stale_account', None
        if request['op'] in ('register', 'login', 'change', 'eligibility', 'assign_task'):
            key = (request['op'], account)
            count = self.queries.get(key, 0)
            if count >= self.query_limit:
                return 'query_limit', None
            self.queries[key] = count + 1
        return 'ready', None

    def receipt(self, request, digest, outcome, committed=False):
        # digest binds randomized envelopes, never a deterministic password hash.
        if committed:
            self.epoch += 1
        value = {'request_digest': digest, 'nonce': request['nonce'], 'account': request.get('account'),
                 'op': request['op'], 'outcome': outcome, 'committed': committed,
                 'epoch': self.epoch, 'policy_version': self.policy_version}
        # A conflicting reuse must never replace the original replay record.
        if request['nonce'] not in self.seen:
            self.seen[request['nonce']] = (digest, value)
        return value

    def reserve_release(self, snapshot, epsilon):
        if snapshot in self.release_cache:
            return 'cached', self.release_cache[snapshot]
        if self.epsilon_spent + epsilon > self.epsilon_cap + 1e-12:
            return 'budget_exhausted', None
        # Reserving before execution is conservative: failed releases never
        # give extra tries to select favourable noise samples.
        self.epsilon_spent += epsilon
        self.release_cache[snapshot] = None
        return 'reserved', None

    def finish_release(self, snapshot, output):
        self.release_cache[snapshot] = output

    def assign_task(self, account):
        if account in self.tasks:
            return 'task_already_assigned'
        if len(self.tasks) >= self.task_cap:
            return 'task_budget_exhausted'
        self.tasks[account] = 'pending'
        return 'task_assigned'

    def complete_task(self, account):
        if self.tasks.get(account) == 'pending':
            self.tasks[account] = 'completed'

    def export(self):
        return {'epoch': self.epoch, 'policy_version': self.policy_version,
                'epsilon_spent': self.epsilon_spent, 'epsilon_cap': self.epsilon_cap,
                'query_limit': self.query_limit,
                'seen': self.seen,
                'queries': [{'op': op, 'account': account, 'count': count} for (op, account), count in self.queries.items()],
                'release_cache': self.release_cache,
                'task_cap': self.task_cap, 'tasks': self.tasks}

    @classmethod
    def from_export(cls, saved):
        guard = cls(epoch=saved['epoch'], policy_version=saved['policy_version'],
                    query_limit=saved['query_limit'], epsilon_cap=saved['epsilon_cap'],
                    epsilon_spent=saved['epsilon_spent'], seen=saved['seen'],
                    release_cache=saved['release_cache'], task_cap=saved['task_cap'], tasks=saved['tasks'])
        guard.queries = {(row['op'], row['account']): row['count'] for row in saved['queries']}
        return guard


def randomized_request_digest(public_request, envelope_digests):
    # Inputs must be secret-key HMAC bindings from the separate parties.
    # Unsalted hashes of threshold-one external shares allow dictionary checks
    # given one party's own shares; never publish those hashes here.
    return hashlib.sha256(canonical({'public_request': public_request, 'randomized_envelopes': envelope_digests})).hexdigest()


def private_envelope_binding(local_key, action):
    if len(local_key) != 32:
        raise ValueError('local binding key must have 256 bits')
    return hmac.new(local_key, b'ZipfGuard/private-envelope/v1\x00' + canonical(action), hashlib.sha256).hexdigest()


def verify_joint_receipt(events, public_keys, expected_context):
    """Accept only all distinct known parties signing the same bound result.

    Fail closed for a missing party, duplicate signer, differing receipt,
    invalid signature or wrong caller-supplied transaction context.
    """
    if len(events) != len(public_keys):
        raise ValueError('incomplete receipt')
    signers = [event.get('party') for event in events]
    if len(set(signers)) != len(public_keys) or set(signers) != set(public_keys):
        raise ValueError('duplicate or unknown signer')
    receipt = events[0]['receipt']
    if any(event['receipt'] != receipt for event in events):
        raise ValueError('conflicting receipts')
    if any(receipt.get(key) != value for key, value in expected_context.items()):
        raise ValueError('wrong transaction context')
    for event in events:
        public_keys[event['party']].verify(bytes.fromhex(event['signature']), canonical(receipt))
    return receipt
