"""State/security boundary tests independent of MPC and dataset simulation."""
import json
import hashlib
import secrets
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'privacy_deps'))
sys.path.insert(0, str(ROOT))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from privacy.transaction_contract import TransactionGuard, canonical, verify_joint_receipt, private_envelope_binding


class ContractTests(unittest.TestCase):
    def test_conflicting_nonce_does_not_replace_original_replay(self):
        guard = TransactionGuard()
        request = {'op': 'change', 'account': 'a', 'nonce': 'one', 'account_version': 1, 'policy_version': 1}
        original = guard.receipt(request, 'randomized-envelope-1', 'password_changed', committed=True)
        self.assertEqual(guard.before(request, 'different-envelope', {'a': 2})[0], 'nonce_conflict')
        guard.receipt(request, 'different-envelope', 'nonce_conflict')
        self.assertEqual(guard.before(request, 'randomized-envelope-1', {'a': 2}), ('replay', original))
        self.assertEqual(guard.epoch, 1)

    def test_restart_budget_queries_and_tasks_survive_serialization(self):
        guard = TransactionGuard(query_limit=1, epsilon_cap=1, task_cap=1)
        req = {'op': 'login', 'account': 'a', 'nonce': 'x', 'account_version': 1, 'policy_version': 1}
        self.assertEqual(guard.before(req, 'd', {'a': 1})[0], 'ready')
        self.assertEqual(guard.reserve_release('s1', 1)[0], 'reserved')
        guard.finish_release('s1', {'counts': [1]})
        self.assertEqual(guard.assign_task('a'), 'task_assigned')
        restored = TransactionGuard.from_export(json.loads(json.dumps(guard.export())))
        self.assertEqual(restored.reserve_release('s1', 1), ('cached', {'counts': [1]}))
        self.assertEqual(restored.reserve_release('s2', 1)[0], 'budget_exhausted')
        self.assertEqual(restored.before(req, 'd', {'a': 1})[0], 'query_limit')
        self.assertEqual(restored.assign_task('a'), 'task_already_assigned')
        self.assertEqual(restored.assign_task('b'), 'task_budget_exhausted')
        restored.complete_task('a')
        self.assertEqual(restored.tasks, {'a': 'completed'})
        self.assertEqual(restored.assign_task('b'), 'task_budget_exhausted')

    def test_abort_reservation_cannot_buy_another_noise_sample(self):
        guard = TransactionGuard(epsilon_cap=1)
        self.assertEqual(guard.reserve_release('s', 1), ('reserved', None))
        self.assertEqual(guard.reserve_release('s', 1), ('cached', None))
        self.assertEqual(guard.reserve_release('other', 1), ('budget_exhausted', None))

    def test_receipt_requires_distinct_known_signers_and_bound_context(self):
        keys = {i: Ed25519PrivateKey.generate() for i in range(3)}
        public = {i: key.public_key() for i, key in keys.items()}
        receipt = {'nonce': 'once', 'account': 'a', 'op': 'register', 'outcome': 'registered'}
        events = [{'party': i, 'receipt': receipt, 'signature': key.sign(canonical(receipt)).hex()} for i, key in keys.items()]
        context = {'nonce': 'once', 'account': 'a', 'op': 'register'}
        self.assertEqual(verify_joint_receipt(events, public, context), receipt)
        for invalid in [events[:2], [events[0], events[0], events[2]], [dict(events[0], party=9), events[1], events[2]]]:
            with self.assertRaises(ValueError):
                verify_joint_receipt(invalid, public, context)
        with self.assertRaises(ValueError):
            verify_joint_receipt(events, public, dict(context, account='b'))
        forged = [dict(event, receipt=dict(receipt, outcome='forged')) for event in events]
        with self.assertRaises(Exception):
            verify_joint_receipt(forged, public, context)

    def test_single_share_distribution_independent_of_secret(self):
        # Exhaustive small-field mathematical check; not an empirical privacy proof.
        prime = 17
        for party in (1, 2, 3):
            for secret in range(prime):
                self.assertEqual(sorted((secret + party*slope) % prime for slope in range(prime)), list(range(prime)))
        for secret in range(prime):
            for slope in range(prime):
                y1, y2 = (secret+slope) % prime, (secret+2*slope) % prime
                self.assertEqual((2*y1-y2) % prime, secret)

    def test_unkeyed_share_hash_is_an_offline_verifier(self):
        # Concrete counterexample documenting why random shares alone do not
        # make their public unkeyed hashes safe. Inputs here are toy bitstrings.
        prime = 101
        secret = [1, 0, 1, 1]
        slopes = [13, 25, 37, 49]
        own = [(bit+slope) % prime for bit, slope in zip(secret, slopes)]
        other = [(bit+2*slope) % prime for bit, slope in zip(secret, slopes)]
        unkeyed = hashlib.sha256(canonical({'shares': other})).hexdigest()
        dictionary = [[0, 0, 0, 0], [1, 0, 1, 1], [1, 1, 1, 1]]
        matches = []
        for candidate in dictionary:
            guessed_other = [(2*value-bit) % prime for value, bit in zip(own, candidate)]
            if hashlib.sha256(canonical({'shares': guessed_other})).hexdigest() == unkeyed:
                matches.append(candidate)
        self.assertEqual(matches, [secret])
        key = secrets.token_bytes(32)
        action = {'shares': other}
        binding = private_envelope_binding(key, action)
        self.assertEqual(binding, private_envelope_binding(key, action))
        self.assertNotEqual(binding, private_envelope_binding(secrets.token_bytes(32), action))
        self.assertNotEqual(binding, unkeyed)
        with self.assertRaises(ValueError):
            private_envelope_binding(b'short', action)


if __name__ == '__main__':
    unittest.main()
