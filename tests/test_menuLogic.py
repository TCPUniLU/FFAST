import pytest

from UI.menuLogic import (
    resolve_key_options,
    stride_to_slice_num,
    parse_host_port,
    latest_session_record,
)


class TestResolveKeyOptions:
    def test_single_key_each_skips_dialog(self):
        need, e, f = resolve_key_options(["E"], ["F"], False, False)
        assert need is False
        assert e == "E"
        assert f == "F"

    def test_multiple_energy_keys_needs_dialog(self):
        need, e, f = resolve_key_options(["E", "REF_E"], ["F"], False, False)
        assert need is True
        assert e == "E"  # default = first
        assert f == "F"

    def test_multiple_force_keys_needs_dialog(self):
        need, _, _ = resolve_key_options(["E"], ["F", "REF_F"], False, False)
        assert need is True

    def test_key_plus_calculator_is_two_options(self):
        # one key + a live calculator = 2 options → dialog
        need, e, _ = resolve_key_options(["E"], ["F"], True, False)
        assert need is True
        assert e == "E"

    def test_calculator_only_skips_dialog(self):
        # no keys, calculator present → single option, no dialog, None default
        need, e, f = resolve_key_options([], [], True, True)
        assert need is False
        assert e is None
        assert f is None

    def test_empty_keys_no_calculator(self):
        # the historical Nona-bug path: must yield None, not crash
        need, e, f = resolve_key_options([], [], False, False)
        assert need is False
        assert e is None
        assert f is None

    def test_none_inputs_treated_as_empty(self):
        need, e, f = resolve_key_options(None, None, False, False)
        assert need is False
        assert e is None
        assert f is None


class TestStrideToSliceNum:
    def test_stride_one_means_load_all(self):
        assert stride_to_slice_num(1) == 0

    @pytest.mark.parametrize("stride", [2, 5, 100])
    def test_stride_passthrough(self, stride):
        assert stride_to_slice_num(stride) == stride


class TestParseHostPort:
    def test_host_and_port(self):
        assert parse_host_port("127.0.0.1:8765") == ("127.0.0.1", 8765)

    def test_bare_port_uses_default_host(self):
        assert parse_host_port("8765") == ("127.0.0.1", 8765)

    def test_strips_whitespace(self):
        assert parse_host_port("  192.168.0.2 : 9000 ") == ("192.168.0.2", 9000)

    def test_custom_default_host(self):
        assert parse_host_port("9000", default_host="0.0.0.0") == ("0.0.0.0", 9000)

    def test_non_integer_port_raises(self):
        with pytest.raises(ValueError):
            parse_host_port("localhost:abc")

    def test_negative_port_is_rejected(self):
        # Out-of-range ports (must be 1-65535) are rejected rather than passed
        # through as a nonsensical negative port.
        with pytest.raises(ValueError):
            parse_host_port("127.0.0.1:-8765")
        with pytest.raises(ValueError):
            parse_host_port("-8765")

    def test_empty_port_string_raises(self):
        # host:"" -> int("") raises ValueError (the caller's "Invalid address").
        with pytest.raises(ValueError):
            parse_host_port("127.0.0.1:")

    def test_bare_ipv6_address_raises(self):
        # A bare (unbracketed) IPv6 address is ambiguous with host:port
        # splitting — rejected rather than silently mangled.
        with pytest.raises(ValueError):
            parse_host_port("2001:db8::1")
        with pytest.raises(ValueError):
            parse_host_port("::1")

    def test_bracketed_ipv6_address_is_parsed(self):
        assert parse_host_port("[::1]:8765") == ("::1", 8765)
        assert parse_host_port("[2001:db8::1]:9000") == ("2001:db8::1", 9000)


class TestLatestSessionRecord:
    def test_returns_latest_matching(self):
        records = [
            {"profile_name": "a", "job_id": "1"},
            {"profile_name": "b", "job_id": "2"},
            {"profile_name": "a", "job_id": "3"},
        ]
        assert latest_session_record(records, "a") == {"profile_name": "a", "job_id": "3"}

    def test_no_match_returns_none(self):
        assert latest_session_record([{"profile_name": "a"}], "z") is None

    def test_empty_returns_none(self):
        assert latest_session_record([], "a") is None
