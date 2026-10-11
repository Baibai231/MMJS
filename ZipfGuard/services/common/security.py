"""Audience-bound Ed25519 tokens and independently verified node receipts."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from privacy.transaction_contract import canonical, verify_joint_receipt


class SecurityError(ValueError):
    pass


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def unb64(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def public_hex(private):
    return private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def load_private(path):
    return serialization.load_pem_private_key(Path(path).read_bytes(), password=None)


def create_private(path):
    path = Path(path)
    if path.exists():
        return load_private(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return key


def token(private, audience, claims, lifetime=300):
    now = int(time.time())
    value = {**claims, 'aud': audience, 'iat': now, 'exp': now+lifetime, 'jti': secrets.token_hex(16)}
    payload = b64(canonical(value))
    return payload + '.' + b64(private.sign(payload.encode('ascii')))


def verify_token(value, public_key, audience):
    try:
        payload, signature = value.split('.')
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key)).verify(unb64(signature), payload.encode('ascii'))
        claims = json.loads(unb64(payload))
        now = time.time()
        if claims['aud'] != audience or claims['exp'] <= now or claims['iat'] > now+15:
            raise SecurityError('expired or wrong audience')
        return claims
    except Exception as exc:
        raise SecurityError('invalid credential') from exc


def verify_client_signature(public_key, signature, value):
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key)).verify(bytes.fromhex(signature), canonical(value))
    except Exception as exc:
        raise SecurityError('invalid client signature') from exc


def receipt_quorum(events, keys, expected):
    try:
        public = {i: Ed25519PublicKey.from_public_bytes(bytes.fromhex(key)) for i, key in enumerate(keys)}
        return verify_joint_receipt(events, public, expected)
    except Exception as exc:
        raise SecurityError('invalid node quorum receipt') from exc


def fingerprint_public(value):
    # Only public context, never a password, recovery secret or share envelope.
    return hashlib.sha256(canonical(value)).hexdigest()
