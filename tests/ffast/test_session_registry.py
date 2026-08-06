"""Tests for ffast.session.registry.decide_role (ADR 0044 Phase 2, ADR 0051).

This covered a ``ConnectionRegistry`` that kept a ``websocket -> role`` dict.
ADR 0044 Phase 2 removed the single-CONTROLLING gate — every admitted connection
controls its own views — which left the dict with nothing to arbitrate:
``role_of`` / ``has_controlling`` / ``count`` had no callers outside this file,
and liveness belongs to ``ConnectionHub``. ADR 0051 reduced it to the role
decision, so the tests that remain are the ones that were ever about behaviour
rather than bookkeeping.
"""

from ffast.session.registry import decide_role
from ffast.session.token import ClientRole


def test_valid_token_grants_controlling():
    assert decide_role(token_ok=True) == ClientRole.CONTROLLING


def test_invalid_token_is_read_only():
    assert decide_role(token_ok=False) == ClientRole.READ_ONLY


def test_every_valid_token_gets_controlling():
    """CONTROLLING is not a single global slot the first client wins.

    Before ADR 0044 Phase 2 the second client was locked out to READ_ONLY; the
    decision is now per-connection and stateless, so N calls give N controllers.
    """
    assert [decide_role(token_ok=True) for _ in range(5)] == [
        ClientRole.CONTROLLING
    ] * 5


def test_explicit_read_only_opt_in_beats_a_valid_token():
    """PRD story 73: a client may hold a token and still ask to be a viewer."""
    assert decide_role(token_ok=True, read_only_requested=True) == ClientRole.READ_ONLY


def test_read_only_opt_in_with_an_invalid_token_is_still_read_only():
    assert decide_role(token_ok=False, read_only_requested=True) == ClientRole.READ_ONLY


def test_the_decision_is_stateless():
    """No registry to consult, so ordering cannot change an outcome."""
    first = decide_role(token_ok=True)
    decide_role(token_ok=False)
    decide_role(token_ok=True, read_only_requested=True)
    assert decide_role(token_ok=True) == first
