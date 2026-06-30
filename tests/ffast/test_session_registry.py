"""Tests for ffast.session.registry.ConnectionRegistry."""

from ffast.session.registry import ConnectionRegistry
from ffast.session.token import ClientRole


class TestConnectionRegistry:
    def setup_method(self):
        self.reg = ConnectionRegistry()
        self.ws1 = object()
        self.ws2 = object()
        self.ws3 = object()

    def test_empty_has_no_controlling(self):
        assert not self.reg.has_controlling
        assert self.reg.count == 0

    def test_first_valid_token_gets_controlling(self):
        role = self.reg.claim(self.ws1, token_ok=True)
        assert role == ClientRole.CONTROLLING
        assert self.reg.has_controlling

    def test_second_valid_token_gets_read_only(self):
        self.reg.claim(self.ws1, token_ok=True)
        role = self.reg.claim(self.ws2, token_ok=True)
        assert role == ClientRole.READ_ONLY

    def test_no_token_gets_read_only_when_controlling_exists(self):
        self.reg.claim(self.ws1, token_ok=True)
        role = self.reg.claim(self.ws2, token_ok=False)
        assert role == ClientRole.READ_ONLY

    def test_no_token_gets_read_only_even_without_controlling(self):
        role = self.reg.claim(self.ws1, token_ok=False)
        assert role == ClientRole.READ_ONLY
        assert not self.reg.has_controlling

    def test_release_controlling_clears_has_controlling(self):
        self.reg.claim(self.ws1, token_ok=True)
        released = self.reg.release(self.ws1)
        assert released == ClientRole.CONTROLLING
        assert not self.reg.has_controlling

    def test_release_unknown_returns_none(self):
        assert self.reg.release(self.ws1) is None

    def test_role_of_registered_client(self):
        self.reg.claim(self.ws1, token_ok=True)
        assert self.reg.role_of(self.ws1) == ClientRole.CONTROLLING

    def test_role_of_unknown_client_returns_none(self):
        assert self.reg.role_of(self.ws1) is None

    def test_count(self):
        assert self.reg.count == 0
        self.reg.claim(self.ws1, token_ok=True)
        assert self.reg.count == 1
        self.reg.claim(self.ws2, token_ok=False)
        assert self.reg.count == 2
        self.reg.release(self.ws1)
        assert self.reg.count == 1

    def test_new_controlling_can_be_claimed_after_release(self):
        self.reg.claim(self.ws1, token_ok=True)
        self.reg.release(self.ws1)
        # Slot is free — next valid-token client gets CONTROLLING
        role = self.reg.claim(self.ws2, token_ok=True)
        assert role == ClientRole.CONTROLLING
