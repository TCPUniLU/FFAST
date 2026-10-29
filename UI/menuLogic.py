"""Qt-free decision helpers for the menu load flows.

These were previously inlined (and duplicated) inside ``MenuHandler``: the
ASE key-selection rule appeared 3× across local/remote dataset and remote
prediction loading, and the stride and address parsing each appeared twice.
Pulling them out here makes them unit-testable without Qt and kills the
duplication (and the historical ``Nona`` typo lineage on the empty-keys path).
"""


def resolve_key_options(energy_keys, force_keys,
                        has_calc_energy, has_calc_forces):
    """Decide whether the ASE key-selection dialog is needed.

    An option exists per available key plus one for a live calculator. When
    there is at most one option for *both* energy and force, the choice is
    unambiguous and the dialog is skipped. The returned defaults are the first
    available key (or ``None`` when only a calculator / nothing is present).

    Returns ``(need_dialog, default_energy_key, default_force_key)``.
    """
    energy_keys = energy_keys or []
    force_keys = force_keys or []

    energy_options = len(energy_keys) + (1 if has_calc_energy else 0)
    force_options = len(force_keys) + (1 if has_calc_forces else 0)

    need_dialog = energy_options > 1 or force_options > 1
    default_energy = energy_keys[0] if energy_keys else None
    default_force = force_keys[0] if force_keys else None
    return need_dialog, default_energy, default_force


def stride_to_slice_num(stride):
    """Map a user-chosen stride to the loader's ``slice_num`` convention.

    ``slice_num=0`` means "load all" (the efficient path); ``N>1`` keeps every
    Nth frame. A stride of 1 therefore maps to 0.
    """
    return 0 if stride == 1 else stride


def parse_host_port(addr_str, default_host="127.0.0.1"):
    """Parse a ``host:port`` (or bare ``port``) string.

    IPv6 hosts require bracket syntax (``[host]:port``) since a bare IPv6
    address is itself colon-separated and would otherwise be ambiguous with
    the ``host:port`` split.

    Raises ``ValueError`` on a non-integer port, an out-of-range port
    (must be 1-65535), or a bare (unbracketed) multi-colon address — same
    failure the caller surfaces as an "Invalid address" dialog.
    """
    addr_str = addr_str.strip()
    if addr_str.startswith("["):
        end = addr_str.find("]")
        if end == -1:
            raise ValueError(f"Invalid address: {addr_str!r}")
        host = addr_str[1:end]
        rest = addr_str[end + 1:].strip()
        if not rest.startswith(":"):
            raise ValueError(f"Invalid address: {addr_str!r}")
        port = int(rest[1:].strip())
    elif addr_str.count(":") > 1:
        raise ValueError(
            f"Ambiguous IPv6 address {addr_str!r}; use [host]:port bracket syntax"
        )
    elif ":" in addr_str:
        host, port_s = addr_str.rsplit(":", 1)
        host = host.strip()
        port = int(port_s.strip())
    else:
        host = default_host
        port = int(addr_str)

    if not (1 <= port <= 65535):
        raise ValueError(f"Port out of range (1-65535): {port}")
    return host, port


def latest_session_record(records, profile_name):
    """Most recent saved session record for ``profile_name`` (or ``None``)."""
    matching = [
        r for r in records if r.get("profile_name") == profile_name
    ]
    return matching[-1] if matching else None
