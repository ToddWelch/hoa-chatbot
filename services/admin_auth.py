"""Bcrypt password hashing + IP hashing helpers.

The HOA project has one admin password, stored hashed in
config.admin_password_hash. There is no User model; this module is the
sole place bcrypt is called.

Cost factor 12 is hardcoded per IMPLEMENTATION_PLAN risk note: an env-
overridable factor invites operator misconfiguration. 12 is the modern
default and gives ~250ms verify on a typical Railway worker, well below
the 120s gunicorn timeout.

IP hashing is deliberately not in `services/rate_limit.py` because the
hash function is shared between rate limiting and failed-address logging,
and putting both crypto-shaped helpers in one module keeps the auth
boundary explicit.
"""

from __future__ import annotations

import hashlib

import bcrypt

_BCRYPT_COST = 12


def hash_password(plaintext: str) -> str:
    """Hash a password with bcrypt cost 12. Returns the hash as ASCII string.

    The returned string is what gets stored in config.admin_password_hash.
    Both scripts/set_admin_password.py and /admin/config/password call this
    same function so the hash format is byte-identical between bootstrap
    and runtime change.
    """
    salt = bcrypt.gensalt(_BCRYPT_COST)
    digest = bcrypt.hashpw(plaintext.encode("utf-8"), salt)
    return digest.decode("ascii")


def verify_password(plaintext: str, hashed: str) -> bool:
    """Return True if plaintext matches the stored bcrypt hash.

    Returns False on any malformed hash or mismatch (never raises). Callers
    treat the return value as the auth decision.
    """
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        # Malformed stored hash; treat as no-match rather than 500.
        return False


def hash_ip(remote_addr: str | None) -> str:
    """Return SHA-256 hex of the IP. Empty string if no IP.

    Per the threat-model note: raw IPs are never persisted. We only ever
    need to know whether two attempts came from the same client, which
    a stable hash gives us.
    """
    if not remote_addr:
        return ""
    return hashlib.sha256(remote_addr.encode("utf-8")).hexdigest()
