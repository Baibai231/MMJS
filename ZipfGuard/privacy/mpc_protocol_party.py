"""One computation party in the research protocol.

Only this party's randomized client shares enter this process over mutual TLS.
Passwords, per-account structures and hidden rule values are never opened.
MPyC is PASSIVE secure: this prototype is not malicious-server secure or PAKE.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal, localcontext, ROUND_CEILING
import hashlib
import json
from pathlib import Path
import secrets
import ssl
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'privacy_deps'))
sys.path.insert(0, str(ROOT))
from mpyc.runtime import mpc
from cryptography.hazmat.primitives import serialization
from policy.privacy_joint_profile import decreasing_projection, simplex_projection
from privacy.transaction_contract import TransactionGuard, canonical, randomized_request_digest, private_envelope_binding


def class_sequence(shape, length):
    import re
    codes = {'L': 1, 'U': 2, 'D': 3, 'S': 4}
    result = [codes[kind] for kind, count in re.findall(r'([LUDS])(\d+)', shape) for _ in range(int(count))]
    return result + [0] * (length - len(result))


def rational_geometric_parameter(epsilon, sensitivity, bits):
    # q is rounded UP, so the actual log-ratio bound is no larger than epsilon.
    denominator = 1 << bits
    with localcontext() as ctx:
        ctx.prec = 100
        q = (-Decimal(str(epsilon)) / Decimal(sensitivity)).exp()
        numerator = int((q * denominator).to_integral_value(rounding=ROUND_CEILING))
    return numerator, denominator


def discrete_laplace(numerator, denominator):
    def geometric():
        n = 0
        while secrets.randbelow(denominator) < numerator:
            n += 1
            if n >= 1 << 28:
                raise OverflowError('Noise outside bounded secure integer range; abort without release')
        return n
    return geometric() - geometric()


async def receive_bundle(cfg, directory):
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(directory / '.config' / f'party_{mpc.pid}.crt', directory / '.config' / f'party_{mpc.pid}.key')
    context.load_verify_locations(directory / '.config/mpyc_ca.crt')
    context.verify_mode = ssl.CERT_REQUIRED
    received = asyncio.get_running_loop().create_future()

    async def receive(reader, writer):
        try:
            size = int.from_bytes(await reader.readexactly(8), 'big')
            if not 0 < size <= 4_000_000:
                raise ValueError('invalid bundle size')
            body = await reader.readexactly(size)
            value = json.loads(body)
            peer = writer.get_extra_info('peercert')
            if not peer:
                raise ValueError('missing authenticated client certificate')
            if not received.done():
                received.set_result((value, body, peer))
            writer.write(b'OK')
            await writer.drain()
        except Exception as exc:
            if not received.done():
                received.set_exception(exc)
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(receive, '127.0.0.1', cfg['upload_base_port'] + mpc.pid, ssl=context)
    (directory / 'upload_ready.json').write_text(json.dumps({'ready': True, 'port': cfg['upload_base_port'] + mpc.pid}), encoding='utf-8')
    try:
        value, body, peer = await asyncio.wait_for(received, cfg['timeout_seconds'])
    finally:
        server.close()
        await server.wait_closed()
    (directory / 'received_randomized_envelope.json').write_bytes(body)
    return value, len(body), peer


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--experiment-config', required=True)
    parser.add_argument('--party-directory', required=True)
    args = parser.parse_args()
    cfg = json.loads(Path(args.experiment_config).read_text('utf-8'))
    directory = Path(args.party_directory)
    bundle, upload_bytes, client_certificate = await receive_bundle(cfg, directory)
    signing_key = serialization.load_pem_private_key((directory / 'receipt_signing.key').read_bytes(), password=None)
    secint = mpc.SecInt(cfg['secure_integer_bits'])
    field = mpc.SecFld(order=secint.field.order, signed=True)
    assert int(bundle['field_prime']) == secint.field.order
    actions = bundle['actions']
    requests = [item['request'] for item in actions]
    await mpc.start()
    protocol_start = time.perf_counter()
    public_digest = hashlib.sha256(canonical({'requests': requests, 'expected_checks': bundle['expected_checks']})).hexdigest()
    public_digests = await mpc.transfer(public_digest)
    if len(set(public_digests)) != 1:
        raise ValueError('Parties disagree on public session context')
    # A raw hash of another party's degree-one shares becomes an offline
    # password verifier to a party holding its own shares: a guess determines
    # the whole sharing polynomial. Each digest therefore uses a secret local
    # HMAC key that is never transferred. Preserve it across local reloads so
    # the identical randomized envelope still yields the same replay binding.
    binding_path = directory / 'envelope_binding.key'
    if not binding_path.exists():
        binding_path.write_bytes(secrets.token_bytes(32))
    binding_key = binding_path.read_bytes()
    assert len(binding_key) == 32
    per_request_digests = [private_envelope_binding(binding_key, item) for item in actions]
    all_request_digests = await mpc.transfer(per_request_digests)
    digest_list = [randomized_request_digest(request, [party[i] for party in all_request_digests]) for i, request in enumerate(requests)]
    local_flat = []
    ranges = []
    for action in actions:
        fields = {}
        for name, shares in action['fields'].items():
            start = len(local_flat)
            local_flat.extend(shares)
            fields[name] = (start, len(local_flat))
        ranges.append(fields)
    distributed = mpc.input([field(int(v)) for v in local_flat])
    # Shares y(1),y(2),y(3) are re-shared, not reconstructed in any process.
    bits = [2 * distributed[0][i] - distributed[1][i] for i in range(len(local_flat))]
    polynomial_errors = [distributed[2][i] - 2 * distributed[1][i] + distributed[0][i] for i in range(len(local_flat))]
    width = cfg['maximum_ascii_bytes']
    shape_patterns = [class_sequence(s, width) for s in cfg['structures']]
    guard = TransactionGuard(query_limit=cfg['query_limit_per_account'], epsilon_cap=cfg['privacy_lifetime_cap'],
                             task_cap=cfg['remediation_task_cap'])
    accounts, versions = {}, {}
    hidden_exact = [secint(0) for _ in range(width)]
    hidden_exact_active = secint(0)
    forbidden_shape = None
    events, diagnostics, public_profiles = [], [], []
    phase_timings = []

    async def secret_field(name, action_index):
        start, end = ranges[action_index][name]
        b = bits[start:end]
        errors = polynomial_errors[start:end] + [v * (v - 1) for v in b]
        # Random coefficients are generated inside MPC after input. Only a
        # zero/nonzero result is opened; malformed-client cancellation <=1/p.
        coefficients = mpc._randoms(field, len(errors))
        valid = bool(await mpc.is_zero_public(mpc.in_prod(errors, coefficients)))
        if not valid:
            return None
        # Retyping uses existing field shares. It never opens their secrets.
        boolean_shares = [secint(v.share) for v in b]
        return [mpc.sum([boolean_shares[8*i+k] * (1 << k) for k in range(8)]) for i in range(width)]

    def equal(left, right):
        return mpc.all([a == b for a, b in zip(left, right)])

    def characterize(chars):
        nonzero = [c != 0 for c in chars]
        valid = [(c == 0) + ((c >= 32) * (c <= 126)) for c in chars]
        valid += [1 - (1 - nonzero[i]) * nonzero[i+1] for i in range(width - 1)]
        valid += [mpc.sum(nonzero) >= cfg['minimum_ascii_bytes']]
        codes = []
        for c, present in zip(chars, nonzero):
            lower = (c >= 97) * (c <= 122)
            upper = (c >= 65) * (c <= 90)
            digit = (c >= 48) * (c <= 57)
            special = present - lower - upper - digit
            codes.append(lower + 2*upper + 3*digit + 4*special)
        matches = [mpc.all([a == b for a, b in zip(codes, pattern)]) for pattern in shape_patterns]
        shape_index = secint(len(shape_patterns)) - mpc.sum([bit * (len(shape_patterns)-i) for i, bit in enumerate(matches)])
        return mpc.all(valid), shape_index

    def policy_accept(chars):
        valid, shape_index = characterize(chars)
        exact_hit = equal(chars, hidden_exact) * hidden_exact_active
        shape_hit = secint(0) if forbidden_shape is None else shape_index == forbidden_shape
        return valid * (1 - exact_hit) * (1 - shape_hit)

    async def private_profile():
        rows = list(accounts.values())
        n = len(rows)
        metadata = [characterize(row)[1] for row in rows]
        equality = [[equal(a, b) for b in rows] for a in rows]
        frequencies = [mpc.sum(row) for row in equality]
        unique = [secint(1) if i == 0 else (mpc.sum(equality[i][:i]) == 0) for i in range(n)]
        shape_counts, spectra = [], []
        for s in range(len(shape_patterns) + 1):
            selected = [index == s for index in metadata]
            shape_counts.append(mpc.sum(selected))
            weighted = [frequencies[i] * unique[i] * selected[i] for i in range(n)]
            ordered = mpc.sorted(weighted, reverse=True)
            prefix = ordered[:cfg['spectrum_length']]
            prefix += [secint(0)] * (cfg['spectrum_length'] - len(prefix))
            spectra.extend(prefix)
        return shape_counts + spectra, metadata, frequencies, unique

    def sign_receipt(receipt):
        return {'receipt': receipt, 'party': mpc.pid, 'signature': signing_key.sign(canonical(receipt)).hex()}

    async def checkpoint():
        values = {}
        for account, chars in accounts.items():
            local = await mpc.gather(chars)
            values[account] = [int(v.value) for v in local]
        # Only this party's current shares are persisted, never mpc.output(chars).
        saved = {'field_prime': secint.field.order, 'account_versions': versions,
                 'credential_shares': values, 'transaction_guard': guard.export()}
        (directory / 'credential_share_checkpoint.json').write_text(json.dumps(saved), encoding='utf-8')
        return saved

    for index, action in enumerate(actions):
        request = action['request']
        op, account = request['op'], request.get('account')
        t0 = time.perf_counter()
        digest = digest_list[index]
        schema_keys = {'op', 'account', 'nonce', 'policy_version', 'account_version', 'phase'}
        if set(request) - schema_keys:
            receipt = guard.receipt(request, digest, 'unexpected_field')
            events.append(sign_receipt(receipt))
            continue
        status, old_receipt = guard.before(request, digest, versions)
        if status == 'replay':
            events.append(sign_receipt(old_receipt))
            continue
        if status != 'ready':
            receipt = guard.receipt(request, digest, status)
            events.append(sign_receipt(receipt))
            continue
        if op == 'profile':
            snapshot = f'profile:{guard.epoch}'
            release_status, cached = guard.reserve_release(snapshot, cfg['privacy_release_epsilon'])
            if release_status == 'cached':
                public_profiles.append(cached)
                outcome = 'profile_cached'
            elif release_status == 'budget_exhausted':
                outcome = 'budget_exhausted'
            else:
                protected, _, _, _ = await private_profile()
                expected = bundle['expected_checks'][request['phase']]
                matched = int(await mpc.output(mpc.all([value == truth for value, truth in zip(protected, expected)])))
                diagnostics.append({'check': 'secret_profile_matches_reference', 'phase': request['phase'], 'passed': bool(matched)})
                noise_type = mpc.SecInt(32)
                numerator, denominator = rational_geometric_parameter(cfg['privacy_release_epsilon'], 4, cfg['noise_uniform_bits'])
                local_noise = [noise_type(discrete_laplace(numerator, denominator)) for _ in protected]
                noise_inputs = mpc.input(local_noise)
                converted = mpc.convert(protected, noise_type)
                released = await mpc.output([v + mpc.sum([noise_inputs[p][i] for p in range(3)]) for i, v in enumerate(converted)], receivers=0)
                if mpc.pid == 0:
                    # Only already-noisy values are postprocessed outside MPC.
                    count_len = len(shape_patterns) + 1
                    noisy_counts = simplex_projection(released[:count_len], len(accounts))
                    spectra = [decreasing_projection(released[count_len+s*cfg['spectrum_length']:count_len+(s+1)*cfg['spectrum_length']]) for s in range(count_len)]
                    for s, row in enumerate(spectra):
                        if row.sum() > noisy_counts[s]:
                            row *= noisy_counts[s] / row.sum()
                    image = {'snapshot': snapshot, 'epsilon': cfg['privacy_release_epsilon'],
                             'public_account_count': len(accounts), 'structures': cfg['structures'] + ['OTHER'],
                             'counts': noisy_counts.tolist(), 'spectra': [row.tolist() for row in spectra],
                             'mechanism': 'sum of 3 independently contributed discrete-Laplace vectors'}
                else:
                    image = None
                image = await mpc.transfer(image, senders=0)
                guard.finish_release(snapshot, image)
                public_profiles.append(image)
                outcome = 'profile_released'
            receipt = guard.receipt(request, digest, outcome)
        elif op == 'refresh_policy':
            _, metadata, frequencies, unique = await private_profile()
            forbidden_shape = cfg['fixed_forbidden_shape_index']
            best = secint(0)
            hidden_exact = [secint(0) for _ in range(width)]
            for i, chars in enumerate(accounts.values()):
                score = frequencies[i] * unique[i] * (metadata[i] != forbidden_shape)
                take = score > best
                hidden_exact = mpc.if_else(take, chars, hidden_exact)
                best = mpc.if_else(take, score, best)
            hidden_exact_active = best > 0
            guard.policy_version += 1
            receipt = guard.receipt(request, digest, 'policy_refreshed', committed=True)
        elif op == 'checkpoint_reload':
            protected, _, _, _ = await private_profile()
            expected = bundle['expected_checks'][request['phase']]
            matched = int(await mpc.output(mpc.all([v == truth for v, truth in zip(protected, expected)])))
            diagnostics.append({'check': 'secret_profile_matches_reference', 'phase': request['phase'], 'passed': bool(matched)})
            saved = await checkpoint()
            saved = json.loads((directory / 'credential_share_checkpoint.json').read_text('utf-8'))
            assert saved['field_prime'] == secint.field.order
            # Re-load only local Shamir shares, with no new input or opening.
            accounts = {account: [secint(secint.field(v)) for v in values] for account, values in saved['credential_shares'].items()}
            versions = saved['account_versions']
            guard = TransactionGuard.from_export(saved['transaction_guard'])
            reloaded, _, _, _ = await private_profile()
            matched = int(await mpc.output(mpc.all([v == truth for v, truth in zip(reloaded, expected)])))
            diagnostics.append({'check': 'reloaded_secret_profile_matches_reference', 'phase': request['phase'], 'passed': bool(matched)})
            receipt = guard.receipt(request, digest, 'local_share_checkpoint_reloaded')
        elif op == 'prepare_abort':
            # A prepared but uncommitted transaction cannot mutate the active store.
            before_versions = dict(versions)
            candidate = await secret_field('new', index)
            valid = False if candidate is None else bool(await mpc.output(policy_accept(candidate)))
            if valid:
                pending_shares = [int(v.value) for v in await mpc.gather(candidate)]
                pending = {'request_digest': digest, 'status': 'prepared', 'new_local_shares': pending_shares}
                (directory / 'aborted_prepare_journal.json').write_text(json.dumps(pending), encoding='utf-8')
            votes = await mpc.transfer(bool(valid and mpc.pid != 2))
            assert not all(votes), 'This failure fixture intentionally withholds one prepare vote'
            if valid:
                pending['status'] = 'aborted'
                (directory / 'aborted_prepare_journal.json').write_text(json.dumps(pending), encoding='utf-8')
            diagnostics.append({'check': 'aborted_prepare_preserves_active_versions', 'passed': versions == before_versions})
            receipt = guard.receipt(request, digest, 'prepared_then_aborted' if valid else 'prepare_rejected')
        else:
            expected_fields = {'register': {'new'}, 'login': {'candidate'}, 'change': {'old', 'new'},
                               'eligibility': {'candidate'}, 'assign_task': {'candidate'}}
            if op not in expected_fields or set(action['fields']) != expected_fields[op]:
                receipt = guard.receipt(request, digest, 'unexpected_field')
            else:
                inputs = {name: await secret_field(name, index) for name in action['fields']}
                if any(value is None for value in inputs.values()):
                    receipt = guard.receipt(request, digest, 'inconsistent_or_nonbinary_shares')
                elif op == 'register':
                    if account in accounts:
                        receipt = guard.receipt(request, digest, 'duplicate_account')
                    elif bool(await mpc.output(policy_accept(inputs['new']))):
                        # Exactly the checked object becomes the credential and
                        # the one account contribution used for the next profile.
                        accounts[account] = inputs['new']
                        versions[account] = 1
                        receipt = guard.receipt(request, digest, 'registered', committed=True)
                    else:
                        receipt = guard.receipt(request, digest, 'policy_rejected')
                elif op == 'login':
                    passed = bool(await mpc.output(equal(inputs['candidate'], accounts[account])))
                    receipt = guard.receipt(request, digest, 'authenticated' if passed else 'authentication_failed')
                elif op == 'change':
                    passed = equal(inputs['old'], accounts[account]) * policy_accept(inputs['new'])
                    if bool(await mpc.output(passed)):
                        accounts[account] = inputs['new']
                        versions[account] += 1
                        guard.complete_task(account)
                        receipt = guard.receipt(request, digest, 'password_changed', committed=True)
                    else:
                        receipt = guard.receipt(request, digest, 'change_rejected')
                else:
                    # Eligibility is conditioned on authentication; no website
                    # endpoint can query an arbitrary account's hidden structure.
                    logged_in = equal(inputs['candidate'], accounts[account])
                    shape = characterize(accounts[account])[1]
                    eligible = logged_in * (shape == cfg['fixed_forbidden_shape_index'])
                    passed = bool(await mpc.output(eligible))
                    if op == 'assign_task':
                        outcome = guard.assign_task(account) if passed else 'eligibility_not_proved'
                        receipt = guard.receipt(request, digest, outcome, committed=outcome == 'task_assigned')
                    else:
                        receipt = guard.receipt(request, digest, 'eligible' if passed else 'eligibility_not_proved')
        events.append(sign_receipt(receipt))
        phase_timings.append({'op': op, 'phase': request['phase'], 'seconds': time.perf_counter() - t0})
    await checkpoint()
    await mpc.barrier()
    outbound = sum(peer.protocol.nbytes_sent for peer in mpc.parties if peer.pid != mpc.pid)
    result = {'party': mpc.pid, 'upload_bytes': upload_bytes, 'transport': 'mutual_TLS',
              'authenticated_client_subject': str(client_certificate.get('subject')), 'mpc_bytes_sent': outbound,
              'mpc_seconds': time.perf_counter() - protocol_start, 'phase_timings': phase_timings,
              'events': events, 'diagnostics': diagnostics, 'public_profiles': public_profiles,
              'epsilon_spent': guard.epsilon_spent, 'active_accounts': len(accounts), 'account_versions': versions,
              'remediation_tasks': guard.tasks, 'remediation_task_cap': guard.task_cap,
              'credential_storage': 'local Shamir shares only', 'no_password_opening_calls': True}
    (directory / 'party_result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    await mpc.shutdown()


if __name__ == '__main__':
    mpc.run(main())
