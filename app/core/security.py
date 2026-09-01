"""Password hashing, API keys, and RS256 token issuance.

Tokens are signed with RS256 rather than HMAC and the public key is published at
/.well-known/jwks.json, so the analytics layers built alongside this registry can
verify a token offline. That matters operationally: their login must not fail
because the registry is restarting.
"""

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_hasher = PasswordHasher()

ISSUER = "sentinel-registry"
AUDIENCE = "sentinel-platform"
ACCESS_TTL_SECONDS = 900
REFRESH_TTL_SECONDS = 60 * 60 * 24 * 7
KEY_PATH = Path("keys/jwt_private.pem")


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, plain)
    except (VerifyMismatchError, VerificationError, ValueError):
        return False


hash_secret = hash_password
verify_secret = verify_password


def generate_api_key() -> tuple[str, str, str]:
    """Returns (raw_key, prefix, hash).

    The raw key is shown once. Only the hash is stored, and the prefix is indexed
    so verification is one indexed read plus one argon2 check rather than a scan
    of every key in the table.
    """
    raw = f"sk_{secrets.token_urlsafe(32)}"
    return raw, raw[:12], hash_secret(raw)


@lru_cache(maxsize=1)
def _private_key() -> rsa.RSAPrivateKey:
    if KEY_PATH.exists():
        return serialization.load_pem_private_key(KEY_PATH.read_bytes(), password=None)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEY_PATH.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    KEY_PATH.chmod(0o600)
    return key


def _kid() -> str:
    numbers = _private_key().public_key().public_numbers()
    material = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
    return hashlib.sha256(material).hexdigest()[:16]


def _b64u(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def public_jwk() -> dict[str, str]:
    numbers = _private_key().public_key().public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": _kid(),
        "n": _b64u(numbers.n),
        "e": _b64u(numbers.e),
    }


def _private_pem() -> bytes:
    return _private_key().private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _public_pem() -> bytes:
    return _private_key().public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def create_access_token(
    subject: str,
    role: str,
    department_id: str | None,
    scopes: list[str],
    expires_in: int = ACCESS_TTL_SECONDS,
    token_type: str = "access",
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "department_id": department_id,
        "scopes": scopes,
        "type": token_type,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    return jwt.encode(claims, _private_pem(), algorithm="RS256", headers={"kid": _kid()})


def create_refresh_token(subject: str) -> str:
    return create_access_token(
        subject=subject, role="", department_id=None, scopes=[],
        expires_in=REFRESH_TTL_SECONDS, token_type="refresh",
    )


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(
        token, _public_pem(), algorithms=["RS256"], issuer=ISSUER, audience=AUDIENCE
    )
