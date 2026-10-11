"""Launch real, separate MPC parties and a synthetic client over mutual TLS.

The fixture controller knows its synthetic passwords. Party programs never
import this controller or receive the fixture strings or aggregate raw values.
One-host execution is a protocol feasibility test, not independent operators.
"""
from __future__ import annotations

from collections import Counter
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'privacy_deps'))
sys.path.insert(0, str(ROOT))
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ed25519
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from policy.registration_joint import structure
from privacy.transaction_contract import canonical, verify_joint_receipt


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_tls_credentials(directory):
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'ZipfGuard local privacy experiment CA')])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5))
          .not_valid_after(now+timedelta(days=2)).add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                      data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                      crl_sign=True, encipher_only=None, decipher_only=None), critical=True)
          .sign(ca_key, hashes.SHA256()))
    ca_bytes = ca.public_bytes(serialization.Encoding.PEM)
    (directory / 'mpyc_ca.crt').write_bytes(ca_bytes)
    credentials = {}
    for name in ['client'] + [f'party_{i}' for i in range(3)]:
        identity = 'ZipfGuard synthetic client' if name == 'client' else f'MPyC party {name[-1]}'
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, identity)]))
                .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=2))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
                .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=True,
                                            data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                            crl_sign=False, encipher_only=None, decipher_only=None), critical=True)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(identity), x509.DNSName('localhost')]), critical=False)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH, ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .sign(ca_key, hashes.SHA256()))
        cert_bytes = cert.public_bytes(serialization.Encoding.PEM)
        key_bytes = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        (directory / f'{name}.crt').write_bytes(cert_bytes)
        (directory / f'{name}.key').write_bytes(key_bytes)
        credentials[name] = (cert_bytes, key_bytes)
    return ca_bytes, credentials


def available_base(count):
    for _ in range(100):
        base = 22000 + secrets.randbelow(20000)
        probes = []
        try:
            for i in range(count):
                probe = socket.socket()
                probes.append(probe)
                probe.bind(('127.0.0.1', base+i))
            return base
        except OSError:
            continue
        finally:
            for probe in probes:
                probe.close()
    raise RuntimeError('No consecutive loopback ports available')


def client_share(chars, prime, width):
    if isinstance(chars, str):
        chars = list(chars.encode('ascii'))
    if len(chars) > width:
        raise ValueError('Fixture too long')
    chars = chars + [0] * (width - len(chars))
    bits = [(c >> i) & 1 for c in chars for i in range(8)]
    shares = [[], [], []]
    for bit in bits:
        slope = secrets.randbelow(prime)
        for party in range(3):
            shares[party].append((bit + (party+1)*slope) % prime)
    return shares


def profile_reference(accounts, shapes, length):
    grouped = {s: Counter() for s in shapes + ['OTHER']}
    for password in accounts.values():
        shape = structure(password)
        grouped[shape if shape in shapes else 'OTHER'][password] += 1
    counts = [sum(grouped[s].values()) for s in shapes + ['OTHER']]
    spectra = []
    for shape in shapes + ['OTHER']:
        frequencies = sorted(grouped[shape].values(), reverse=True)[:length]
        spectra += frequencies + [0] * (length - len(frequencies))
    return counts + spectra


def build_fixture(cfg, prime):
    # These are public synthetic tests, never real account passwords.
    bootstrap = ['atlas2026!', 'atlas2026!', 'atlas2026!', 'audit2026!',
                 'Breeze742!', 'Quartz816?', 'rivertrail88', 'Moonlight74!']
    accounts = {f'a{i}': value for i, value in enumerate(bootstrap)}
    party_actions = [[], [], []]
    expected = []
    plaintext_canaries = set(bootstrap)

    def add(label, op, account=None, fields=None, policy=2, version=1, nonce=None,
            expected_outcome=None, phase=None, extra=None, inconsistent=False, nonbinary=False):
        request = {'op': op, 'account': account, 'nonce': nonce or label,
                   'policy_version': policy, 'account_version': version, 'phase': phase or label}
        if extra:
            request.update(extra)
        split_fields = {}
        for name, value in (fields or {}).items():
            split_fields[name] = client_share(value, prime, cfg['maximum_ascii_bytes'])
            if isinstance(value, str):
                plaintext_canaries.add(value)
        if inconsistent:
            first = next(iter(split_fields))
            split_fields[first][2][0] = (split_fields[first][2][0] + 1) % prime
        if nonbinary:
            first = next(iter(split_fields))
            # Willow's low bit is one: shift all shares by one, preserving
            # polynomial consistency but making its secret bit equal two.
            for party in range(3):
                split_fields[first][party][0] = (split_fields[first][party][0] + 1) % prime
        for party in range(3):
            party_actions[party].append({'request': request.copy(), 'fields': {key: value[party] for key, value in split_fields.items()}})
        expected.append({'label': label, 'outcome': expected_outcome})

    for i, password in enumerate(bootstrap):
        add(f'bootstrap_{i}', 'register', f'a{i}', {'new': password}, policy=1, expected_outcome='registered')
    add('profile_before', 'profile', policy=1, phase='before', expected_outcome='profile_released')
    add('same_snapshot_cached', 'profile', policy=1, phase='before', expected_outcome='profile_cached')
    add('refresh_hidden_rule', 'refresh_policy', policy=1, expected_outcome='policy_refreshed')
    add('hidden_exact_rejection', 'register', 'a8', {'new': 'Breeze742!'}, expected_outcome='policy_rejected')
    add('structure_rejection', 'register', 'a8', {'new': 'atlas2026!'}, expected_outcome='policy_rejected')
    add('valid_registration', 'register', 'a8', {'new': 'Delta395?'}, expected_outcome='registered')
    add('alternate_auth_input', 'register', 'a9', {'new': 'Willow834!', 'credential': 'tiny'}, expected_outcome='unexpected_field')
    add('forged_structure_report', 'register', 'a9', {'new': 'Willow834!'}, extra={'reported_structure': 'L1'}, expected_outcome='unexpected_field')
    add('inconsistent_client_shares', 'register', 'a9', {'new': 'Willow834!'}, inconsistent=True, expected_outcome='inconsistent_or_nonbinary_shares')
    add('consistent_nonbinary_shares', 'register', 'a12', {'new': 'Willow834!'}, nonbinary=True, expected_outcome='inconsistent_or_nonbinary_shares')
    add('noncontiguous_padding', 'register', 'a10', {'new': [65, 0, 66, 67, 68, 69, 70, 71, 72]}, expected_outcome='policy_rejected')
    add('wrong_login', 'login', 'a4', {'candidate': 'Breeze742?'}, expected_outcome='authentication_failed')
    add('legacy_login_ignores_new_policy', 'login', 'a4', {'candidate': 'Breeze742!'}, expected_outcome='authenticated')
    add('wrong_old_password', 'change', 'a5', {'old': 'Quartz816!', 'new': 'Sunbeam482#'}, expected_outcome='change_rejected')
    add('abort_without_commit', 'prepare_abort', 'a5', {'new': 'Canopy693#'}, expected_outcome='prepared_then_aborted')
    add('successful_password_change', 'change', 'a5', {'old': 'Quartz816?', 'new': 'Sunbeam482#'}, expected_outcome='password_changed')
    # Exact randomized-envelope replay must produce the original receipt and
    # never repeat the update. Keep all request fields and share bytes equal.
    for party in range(3):
        party_actions[party].append(copy.deepcopy(party_actions[party][-1]))
    expected.append({'label': 'exact_change_replay', 'outcome': 'password_changed'})
    add('conflicting_nonce', 'change', 'a5', {'old': 'Quartz816?', 'new': 'Willow834!'}, version=1,
        nonce='successful_password_change', phase='successful_password_change', expected_outcome='nonce_conflict')
    add('stale_account_version', 'change', 'a5', {'old': 'Quartz816?', 'new': 'Willow834!'}, version=1, expected_outcome='stale_account')
    add('stale_policy', 'register', 'a11', {'new': 'Willow834!'}, policy=1, expected_outcome='stale_policy')
    add('checkpoint_reload', 'checkpoint_reload', phase='after', expected_outcome='local_share_checkpoint_reloaded')
    add('old_password_revoked', 'login', 'a5', {'candidate': 'Quartz816?'}, version=2, expected_outcome='authentication_failed')
    add('new_password_works', 'login', 'a5', {'candidate': 'Sunbeam482#'}, version=2, expected_outcome='authenticated')
    add('login_quota_third', 'login', 'a5', {'candidate': 'Sunbeam482#'}, version=2, expected_outcome='authenticated')
    add('login_quota_fourth', 'login', 'a5', {'candidate': 'Quartz816?'}, version=2, expected_outcome='authentication_failed')
    add('login_quota_fifth', 'login', 'a5', {'candidate': 'Sunbeam482#'}, version=2, expected_outcome='query_limit')
    add('private_remediation_eligibility', 'eligibility', 'a0', {'candidate': 'atlas2026!'}, expected_outcome='eligible')
    add('forged_eligibility_without_auth', 'eligibility', 'a0', {'candidate': 'Willow834!'}, expected_outcome='eligibility_not_proved')
    add('unrelated_account_eligibility', 'eligibility', 'a6', {'candidate': 'rivertrail88'}, expected_outcome='eligibility_not_proved')
    add('unauthenticated_task_denied', 'assign_task', 'a0', {'candidate': 'Willow834!'}, expected_outcome='eligibility_not_proved')
    add('authenticated_task_assignment', 'assign_task', 'a0', {'candidate': 'atlas2026!'}, expected_outcome='task_assigned')
    add('task_assignment_idempotent', 'assign_task', 'a0', {'candidate': 'atlas2026!'}, expected_outcome='task_already_assigned')
    add('task_count_budget_exhausted', 'assign_task', 'a1', {'candidate': 'atlas2026!'}, expected_outcome='task_budget_exhausted')
    add('task_completed_by_private_change', 'change', 'a0', {'old': 'atlas2026!', 'new': 'Meadow953#'}, expected_outcome='password_changed')
    add('final_checkpoint_reload', 'checkpoint_reload', phase='final', expected_outcome='local_share_checkpoint_reloaded')
    add('second_snapshot_budget_exhausted', 'profile', phase='after', expected_outcome='budget_exhausted')
    before = profile_reference(accounts, cfg['structures'], cfg['spectrum_length'])
    accounts['a8'] = 'Delta395?'
    accounts['a5'] = 'Sunbeam482#'
    after = profile_reference(accounts, cfg['structures'], cfg['spectrum_length'])
    accounts['a0'] = 'Meadow953#'
    final = profile_reference(accounts, cfg['structures'], cfg['spectrum_length'])
    checks = {'before': before, 'after': after, 'final': final}
    bundles = [{'field_prime': prime, 'actions': party_actions[p], 'expected_checks': checks} for p in range(3)]
    return bundles, expected, plaintext_canaries


def verify_receipts(results, expected, signing_public_keys):
    checks = []
    key_map = dict(enumerate(signing_public_keys))
    assert all(len(result['events']) == len(expected) for result in results)
    for i, expectation in enumerate(expected):
        receipt = results[0]['events'][i]['receipt']
        assert receipt['outcome'] == expectation['outcome'], (expectation['label'], receipt['outcome'])
        joint = [result['events'][i] for result in results]
        context = {key: receipt[key] for key in ('request_digest', 'nonce', 'account', 'op')}
        assert verify_joint_receipt(joint, key_map, context) == receipt
        checks.append({'case': expectation['label'], 'passed': True, 'outcome': receipt['outcome'], 'three_party_signatures_verified': True})
    # Replayed receipt carries original epoch and no new state mutation.
    original = next(i for i, item in enumerate(expected) if item['label'] == 'successful_password_change')
    replay = next(i for i, item in enumerate(expected) if item['label'] == 'exact_change_replay')
    assert results[0]['events'][original]['receipt'] == results[0]['events'][replay]['receipt']
    joint = [result['events'][original] for result in results]
    receipt = joint[0]['receipt']
    context = {key: receipt[key] for key in ('request_digest', 'nonce', 'account', 'op')}
    tampered = copy.deepcopy(joint)
    for event in tampered:
        event['receipt']['outcome'] = 'forged_acceptance'
    wrong_context = dict(context, nonce='another_transaction')
    negatives = [
        ('tampered_receipt_rejected', tampered, context),
        ('missing_party_receipt_fail_closed', joint[:2], context),
        ('duplicated_signer_fail_closed', [joint[0], joint[0], joint[2]], context),
        ('wrong_receipt_context_fail_closed', joint, wrong_context),
    ]
    for label, messages, bound_context in negatives:
        try:
            verify_joint_receipt(messages, key_map, bound_context)
        except Exception:
            checks.append({'case': label, 'passed': True, 'outcome': 'receipt_rejected', 'three_party_signatures_verified': False})
        else:
            raise AssertionError(f'{label} unexpectedly accepted')
    return checks


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='configs/privacy_protocol_minimal.json')
    args = parser.parse_args()
    config_path = ROOT / args.config
    cfg = json.loads(config_path.read_text('utf-8'))
    output = ROOT / cfg['output']
    private = output / 'private'
    private.mkdir(parents=True, exist_ok=True)
    write_json(output / 'status.json', {'state': 'preparing', 'time': time.strftime('%Y-%m-%d %H:%M:%S')})
    # Determine the same field without giving any party plaintext fixture input.
    from mpyc.runtime import mpc
    mpc.options.sec_param = cfg['statistical_security_bits']
    secint = mpc.SecInt(cfg['secure_integer_bits'])
    prime = secint.field.order
    bundles, expected, canaries = build_fixture(cfg, prime)
    tls = private / 'tls_controller'
    ca_bytes, credentials = make_tls_credentials(tls)
    cfg['mpc_base_port'] = available_base(3)
    cfg['upload_base_port'] = available_base(3)
    runtime_config = private / 'runtime_config.json'
    write_json(runtime_config, cfg)
    signing_public_keys = []
    party_dirs = []
    for party in range(3):
        directory = private / f'party_{party}'
        (directory / '.config').mkdir(parents=True, exist_ok=True)
        cert_bytes, key_bytes = credentials[f'party_{party}']
        (directory / '.config' / f'party_{party}.crt').write_bytes(cert_bytes)
        (directory / '.config' / f'party_{party}.key').write_bytes(key_bytes)
        (directory / '.config/mpyc_ca.crt').write_bytes(ca_bytes)
        signing = ed25519.Ed25519PrivateKey.generate()
        (directory / 'receipt_signing.key').write_bytes(signing.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        signing_public_keys.append(signing.public_key())
        party_dirs.append(directory)
    processes, log_handles = [], []
    started = time.perf_counter()
    try:
        for party, directory in enumerate(party_dirs):
            ready = directory / 'upload_ready.json'
            if ready.exists():
                ready.unlink()
            logfile = (directory / 'process.log').open('wb')
            log_handles.append(logfile)
            command = [sys.executable, str(ROOT / 'privacy/mpc_protocol_party.py'), '-M', '3', '-I', str(party),
                       '-T', '1', '-B', str(cfg['mpc_base_port']), '-K', str(cfg['statistical_security_bits']), '--ssl',
                       '--experiment-config', str(runtime_config), '--party-directory', str(directory)]
            processes.append(subprocess.Popen(command, cwd=directory, stdout=logfile, stderr=subprocess.STDOUT,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0))
        deadline = time.monotonic() + cfg['timeout_seconds']
        while not all((directory / 'upload_ready.json').exists() for directory in party_dirs):
            if any(process.poll() is not None for process in processes) or time.monotonic() > deadline:
                raise RuntimeError('Party upload listeners failed; inspect private process logs')
            time.sleep(0.1)
        print('Three separate parties ready; client uploads fresh randomized shares over mutual TLS.', flush=True)
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(tls / 'mpyc_ca.crt'))
        context.load_cert_chain(tls / 'client.crt', tls / 'client.key')
        for party, bundle in enumerate(bundles):
            body = canonical(bundle)
            with socket.create_connection(('127.0.0.1', cfg['upload_base_port']+party), timeout=20) as raw:
                with context.wrap_socket(raw, server_hostname=f'MPyC party {party}') as connection:
                    connection.sendall(len(body).to_bytes(8, 'big') + body)
                    assert connection.recv(2) == b'OK'
        write_json(output / 'status.json', {'state': 'running', 'time': time.strftime('%Y-%m-%d %H:%M:%S')})
        for process in processes:
            process.wait(timeout=max(1, deadline-time.monotonic()))
            if process.returncode:
                raise RuntimeError(f'Party exited with code {process.returncode}; inspect private process logs')
        results = [json.loads((directory / 'party_result.json').read_text('utf-8')) for directory in party_dirs]
        cases = verify_receipts(results, expected, signing_public_keys)
        diagnostics = []
        for result in results:
            assert result['active_accounts'] == 9 and result['account_versions']['a5'] == 2 and result['account_versions']['a0'] == 2
            assert result['remediation_tasks'] == {'a0': 'completed'}
            assert result['epsilon_spent'] == 1
            assert all(item['passed'] for item in result['diagnostics'])
            diagnostics.extend({'party': result['party'], **item} for item in result['diagnostics'])
        assert len(results[0]['public_profiles']) == 2 and results[0]['public_profiles'][0] == results[0]['public_profiles'][1]
        # Audit actual party input, logs, outputs, persisted credentials and
        # aborted journal for fixture strings. This diagnoses accidental output;
        # the Shamir/MPC argument, not this scan, establishes input privacy.
        scanned_files = []
        plaintext_hits = []
        for directory in party_dirs:
            for path in directory.glob('*.json'):
                if path.name == 'upload_ready.json':
                    continue
                body = path.read_bytes()
                scanned_files.append(str(path.relative_to(output)))
                for canary in canaries:
                    if len(canary) >= 8 and canary.encode('ascii') in body:
                        plaintext_hits.append(path.name)
            log = (directory / 'process.log').read_bytes()
            for canary in canaries:
                if len(canary) >= 8 and canary.encode('ascii') in log:
                    plaintext_hits.append('process.log')
        assert not plaintext_hits
        for directory in party_dirs:
            saved = json.loads((directory / 'credential_share_checkpoint.json').read_text('utf-8'))
            assert set(saved) == {'field_prime', 'account_versions', 'credential_shares', 'transaction_guard'}
            assert all(len(values) == cfg['maximum_ascii_bytes'] for values in saved['credential_shares'].values())
        timings = [{'party': result['party'], 'mpc_seconds': result['mpc_seconds'], 'mpc_bytes_sent': result['mpc_bytes_sent'],
                    'client_upload_bytes': result['upload_bytes']} for result in results]
        summary = {'status': 'passed', 'protocol': 'MPyC real three-party Shamir-based MPC over mutual TLS',
                   'security_model': cfg['security_model'], 'cases': cases, 'case_count': len(cases),
                   'secret_profile_diagnostics': diagnostics, 'active_accounts_final': 9,
                   'same_password_object_used_for_policy_credential_profile': True,
                   'privacy_budget_spent': 1.0, 'profile_releases': 1, 'profile_cached_reads': 1,
                   'remediation_tasks_assigned': 1, 'remediation_tasks_completed': 1, 'remediation_task_cap': cfg['remediation_task_cap'],
                   'private_fixture_string_scan_hits': len(plaintext_hits), 'scanned_party_files': len(scanned_files),
                   'performance': timings, 'wall_seconds': time.perf_counter() - started,
                   'scope': 'small synthetic feasibility run; one host, no active-server security, no PAKE implementation, no crash-consensus proof'}
        write_json(output / 'summary.json', summary)
        write_json(output / 'case_results.json', cases)
        write_json(output / 'public_profile.json', results[0]['public_profiles'][0])
        write_json(output / 'phase_timings.json', results[0]['phase_timings'])
        import mpyc, cryptography
        write_json(output / 'manifest.json', {'config': cfg, 'config_sha256': sha(config_path), 'mpyc_version': mpyc.__version__,
            'cryptography_version': cryptography.__version__, 'field_prime': prime, 'field_prime_bits': prime.bit_length(),
            'synthetic_bootstrap_accounts': 8, 'synthetic_transactions': len(expected), 'client_randomness': 'secrets.randbelow, independent per bit and operation',
            'privacy_noise': 'q=ceil(exp(-epsilon/4)*2^64)/2^64; difference of two exact rational geometric draws at each party',
            'public_request_binding': 'domain-separated HMAC-SHA256 of local envelope under each party private 256-bit key; never unkeyed share hashes',
            'all_party_keys_on_one_host_for_test_only': True, 'independent_deployment_validated': False,
            'TLS_required': True, 'receipt_public_keys': [key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex() for key in signing_public_keys],
            'implementation_sha256': {str(p.relative_to(ROOT)): sha(p) for p in [Path(__file__), ROOT / 'privacy/mpc_protocol_party.py', ROOT / 'privacy/transaction_contract.py']}})
        write_json(output / 'status.json', {'state': 'complete', 'case_count': len(cases), 'time': time.strftime('%Y-%m-%d %H:%M:%S')})
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    except Exception as exc:
        write_json(output / 'status.json', {'state': 'failed', 'error': str(exc), 'time': time.strftime('%Y-%m-%d %H:%M:%S')})
        raise
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
        for logfile in log_handles:
            logfile.close()


if __name__ == '__main__':
    main()
