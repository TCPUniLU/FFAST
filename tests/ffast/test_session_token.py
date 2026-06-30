"""Tests for ffast.session.token."""

import pytest

from ffast.session.token import ClientRole, SessionToken


class TestSessionToken:
    def test_generate_produces_64_char_hex_plaintext(self):
        t = SessionToken.generate()
        assert len(t.plaintext) == 64
        assert all(c in "0123456789abcdef" for c in t.plaintext)

    def test_generate_produces_64_char_hex_hash(self):
        t = SessionToken.generate()
        assert len(t.hash) == 64
        assert all(c in "0123456789abcdef" for c in t.hash)

    def test_generate_unique(self):
        assert SessionToken.generate().plaintext != SessionToken.generate().plaintext

    def test_verify_correct_plaintext(self):
        t = SessionToken.generate()
        assert t.verify(t.plaintext)

    def test_verify_wrong_plaintext(self):
        t = SessionToken.generate()
        assert not t.verify("wrong")

    def test_verify_empty(self):
        t = SessionToken.generate()
        assert not t.verify("")

    def test_from_hash_round_trip(self):
        t = SessionToken.generate()
        server_side = SessionToken.from_hash(t.hash)
        assert server_side.plaintext == ""
        assert server_side.verify(t.plaintext)

    def test_from_hash_rejects_wrong(self):
        t = SessionToken.generate()
        other = SessionToken.generate()
        server_side = SessionToken.from_hash(t.hash)
        assert not server_side.verify(other.plaintext)

    def test_frozen(self):
        t = SessionToken.generate()
        with pytest.raises((AttributeError, TypeError)):
            t.plaintext = "x"  # type: ignore[misc]


class TestClientRole:
    def test_values(self):
        assert ClientRole.CONTROLLING == "CONTROLLING"
        assert ClientRole.READ_ONLY == "READ_ONLY"

    def test_str_enum(self):
        assert isinstance(ClientRole.CONTROLLING, str)
