"""Password hashing and bearer-token minting.

Two separate jobs that are easy to conflate, handled differently on purpose:

* **Passwords** are low-entropy secrets a human chose, so they get Argon2id —
  deliberately slow and memory-hard, to make an offline guessing attack on a
  stolen hash expensive.
* **Session tokens** are 256 bits from the OS CSPRNG. There is nothing to
  guess, so hashing them with Argon2 would only add latency to every request;
  a single SHA-256 is the right tool. It is still hashed rather than stored
  raw so a database dump does not contain replayable live sessions.

Nothing here logs, returns, or formats a password or a raw token — the only
place a raw token exists is the response to a successful login.
"""

from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

# argon2-cffi's defaults track the OWASP recommendation and are revised with
# the library; pinning our own numbers here would freeze them at whatever was
# current the day this was written.
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Argon2id hash in PHC string format (algorithm, cost and salt included)."""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Constant-time-ish verify. False on mismatch *and* on a malformed hash."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False
    except Exception:
        # Any other argon2 failure (a hash from a future parameter set this
        # build cannot read, say) is a failed login, never a 500 that tells
        # the caller something about the stored value.
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when a stored hash predates the current cost parameters.

    Called after a *successful* login — that is the one moment the plaintext
    is available to re-hash with, so raising Argon2's cost later upgrades
    accounts silently as people sign in, instead of stranding old hashes.
    """
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False


def new_session_token() -> str:
    """A fresh opaque bearer token. 256 bits, url-safe."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """The stored form of a session token. Never store the token itself."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
