"""Session token and client role."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from enum import Enum


class ClientRole(str, Enum):
    CONTROLLING = "CONTROLLING"
    READ_ONLY = "READ_ONLY"


@dataclass(frozen=True)
class SessionToken:
    """A session token: client holds plaintext, server stores only the hash.

    Server never sees plaintext after startup — it receives only the hash
    via --token-hash and verifies candidates with secrets.compare_digest.
    """

    plaintext: str  # 64-char hex (32 random bytes); "" when reconstructed from hash
    hash: str       # SHA-256 hex digest

    @classmethod
    def generate(cls) -> SessionToken:
        plaintext = secrets.token_hex(32)
        return cls(plaintext=plaintext, hash=cls._sha256(plaintext))

    @classmethod
    def from_hash(cls, token_hash: str) -> SessionToken:
        """Reconstruct from a stored hash (plaintext unavailable — used server-side)."""
        return cls(plaintext="", hash=token_hash)

    def verify(self, candidate: str) -> bool:
        """True if candidate plaintext hashes to this token's hash."""
        return secrets.compare_digest(self._sha256(candidate), self.hash)

    @staticmethod
    def _sha256(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()
