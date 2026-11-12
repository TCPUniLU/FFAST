"""Readers register under a unique name; the file's ending, then ``can_read``,
pick among them; outside readers come in through the ``ffast.readers`` entry
point group."""
from types import SimpleNamespace

import pytest

from ffast.readers import registry
from tests.ffast.readers.toy_reader import ToyReader


def _reader(name, extensions, can_read=None):
    attrs = {"name": name, "extensions": extensions}
    if can_read is not None:
        attrs["can_read"] = staticmethod(can_read)
    return type(f"R_{name}", (ToyReader,), attrs)


def test_a_name_is_registered_once(toy):
    with pytest.raises(ValueError, match="'toy' is already registered"):
        registry.register(_reader("toy", ["*.other"]))


def test_a_reader_needs_a_name_and_extensions():
    with pytest.raises(ValueError, match="name"):
        registry.register(_reader("", ["*.x"]))
    with pytest.raises(ValueError, match="extensions"):
        registry.register(_reader("x", []))


def test_the_ending_picks_readers_in_registration_order(only_my_readers):
    first, second, other = _reader("a", ["*.foo"]), _reader("b", ["*.FOO", "*.bar"]), _reader("c", ["*.baz"])
    for r in (first, second, other):
        registry.register(r)
    assert registry.readers_for("/x/run.foo") == [first, second]
    assert registry.readers_for("/x/run.bar") == [second]
    assert registry.readers_for("/x/run.qux") == []


def test_can_read_drops_a_reader_that_says_no_and_a_failing_can_read_counts_as_no(only_my_readers):
    seen = []
    no = _reader("no", ["*.foo"], can_read=lambda p: seen.append(p) or False)
    boom = _reader("boom", ["*.foo"], can_read=lambda p: 1 / 0)
    yes = _reader("yes", ["*.foo"], can_read=lambda p: True)
    plain = _reader("plain", ["*.foo"])         # no can_read: the ending decides
    for r in (no, boom, yes, plain):
        registry.register(r)
    assert registry.readers_for("/x/run.foo") == [yes, plain]
    assert seen == ["/x/run.foo"]


def test_the_type_list_rows_carry_each_readers_name_and_endings(only_my_readers, toy):
    assert registry.type_rows() == [{"name": "toy", "extension": "*.toy", "extensions": ["*.toy"], "reader": True}]


def test_entry_points_are_loaded_and_a_broken_one_is_skipped(monkeypatch):
    class Good(ToyReader):
        name = "good"

    def broken():
        raise ImportError("no module named foo")

    eps = [SimpleNamespace(name="good", load=lambda: Good),
           SimpleNamespace(name="bad", load=broken)]
    monkeypatch.setattr(registry, "entry_points", lambda group: eps if group == "ffast.readers" else [])
    registry._discovered = False
    registry.discover()
    assert registry.get("good") is Good
    assert registry.get("bad") is None


def test_discovery_runs_once(monkeypatch):
    calls = []
    monkeypatch.setattr(registry, "entry_points", lambda group: calls.append(group) or [])
    registry._discovered = False
    registry.discover()
    registry.discover()
    assert calls == ["ffast.readers"]


def test_the_bundled_readers_are_registered_sGDML_before_ASE():
    assert [r.name for r in registry.all_readers()][:2] == ["sGDML", "ase (auto)"]


def test_a_reader_that_claims_every_ending_comes_after_those_that_name_the_ending(only_my_readers):
    catch_all = _reader("any", ["*"])
    named = _reader("named", ["*.foo"])
    registry.register(catch_all)            # first in registration order
    registry.register(named)
    assert registry.readers_for("/x/run.foo") == [named, catch_all]
    assert registry.readers_for("/x/run.bar") == [catch_all]
