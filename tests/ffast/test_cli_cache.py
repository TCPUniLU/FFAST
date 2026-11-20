"""``ffast-cli cache`` lists, pins, unpins and deletes parsed copies (ADR 0108)."""
import json
import os

import pytest

from ffast.cli.main import main
from ffast.io import cache_space, frame_cache

MB = 1000


@pytest.fixture
def root(tmp_path, monkeypatch):
    folder = tmp_path / "cache"
    folder.mkdir()
    monkeypatch.setattr(frame_cache, "CACHE_DIR", folder)
    return folder


def _entry(root, key, path, size, age):
    d = root / key
    d.mkdir()
    (d / "positions.npy").write_bytes(b"x" * size)
    (d / "meta.json").write_text(json.dumps({"path": path}))
    t = 1_000_000 - age
    os.utime(d / "meta.json", (t, t))


@pytest.fixture
def two(root, tmp_path):
    old, new = tmp_path / "old.xyz", tmp_path / "new.xyz"
    old.write_text("x")
    new.write_text("x")
    _entry(root, "a", os.path.realpath(old), 4 * MB, 300)
    _entry(root, "b", os.path.realpath(new), 4 * MB, 100)
    return os.path.realpath(old), os.path.realpath(new)


def test_list_shows_pinned_sizes_and_the_total(root, two, capsys):
    main(["cache", "pin", two[0]])
    capsys.readouterr()
    main(["cache", "list"])
    out = capsys.readouterr().out
    line = next(line for line in out.splitlines() if line.startswith(two[0]))
    assert line.endswith("pinned") and "GB" in line
    assert not next(line for line in out.splitlines() if line.startswith(two[1])).endswith("pinned")
    assert "Total:" in out and " of " in out.splitlines()[-1]


def test_list_json_has_the_same_facts(root, two, capsys):
    main(["cache", "pin", two[0]])
    capsys.readouterr()
    main(["cache", "list", "--json"])
    rows = {r["path"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows[two[0]]["pinned"] is True and rows[two[1]]["pinned"] is False
    assert rows[two[0]]["bytes"] > 4 * MB and rows[two[0]]["has_copy"] is True


def test_list_marks_a_missing_source_and_a_pin_without_a_copy(root, tmp_path, capsys):
    _entry(root, "a", str(tmp_path / "gone.xyz"), MB, 10)
    main(["cache", "pin", str(tmp_path / "nocopy.xyz")])
    capsys.readouterr()
    main(["cache", "list"])
    out = capsys.readouterr().out
    assert "source missing" in next(line for line in out.splitlines() if "gone.xyz" in line)
    assert "no copy" in next(line for line in out.splitlines() if "nocopy.xyz" in line)


def test_pin_then_evict_keeps_it(root, two):
    main(["cache", "pin", two[0]])
    assert cache_space.make_room(root, 4 * MB, 9 * MB)
    assert (root / "a").exists() and not (root / "b").exists()


def test_unpin_then_evict_removes_it(root, two):
    main(["cache", "pin", two[0]])
    main(["cache", "unpin", two[0]])
    assert cache_space.make_room(root, 4 * MB, 9 * MB)
    assert not (root / "a").exists() and (root / "b").exists()


def test_delete_of_a_pinned_path_asks_and_n_keeps_it(root, two, monkeypatch):
    main(["cache", "pin", two[0]])
    asked = []
    monkeypatch.setattr("builtins.input", lambda prompt="": asked.append(prompt) or "n")
    main(["cache", "delete", two[0]])
    assert len(asked) == 1
    assert (root / "a").exists() and two[0] in cache_space.read_pins(root)


def test_delete_with_yes_removes_the_copies_and_the_pin(root, two, monkeypatch):
    main(["cache", "pin", two[0]])
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("asked"))
    main(["cache", "delete", two[0], "--yes"])
    assert not (root / "a").exists() and (root / "b").exists()
    assert cache_space.read_pins(root) == []


def test_delete_of_an_unpinned_path_does_not_ask(root, two, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("asked"))
    main(["cache", "delete", two[1]])
    assert not (root / "b").exists()


def test_delete_of_an_open_copy_exits_1_and_keeps_it(root, two):
    handle = cache_space.hold_open(root / "a" / ".open", needs=root / "a" / "meta.json")
    try:
        with pytest.raises(SystemExit) as stop:
            main(["cache", "delete", two[0], "--yes"])
        assert stop.value.code == 1
        assert (root / "a").exists()
    finally:
        handle.close()


def test_a_path_whose_file_is_gone_can_be_unpinned_and_deleted(root, tmp_path):
    gone = str(tmp_path.resolve() / "gone.xyz")
    _entry(root, "a", gone, MB, 10)
    main(["cache", "pin", gone])
    main(["cache", "unpin", gone])
    main(["cache", "pin", gone])
    main(["cache", "delete", gone, "--yes"])
    assert not (root / "a").exists() and cache_space.read_pins(root) == []


# -- verify (ADR 0111) -----------------------------------------------------------

def _verify_setup(tmp_path, monkeypatch):
    import gc

    from ffast.io import budget as resources
    from tests.ffast.readers.test_scan_naming import scan, write_xyz
    owner = resources.Budget(ram_bytes=512 * 1024, parsed_bytes=2**30, result_bytes=8 * 2**20)
    owner.limit = 16 * 2**20
    monkeypatch.setattr(resources, "_budget", owner)
    from ffast.io import frame_index
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "parsed")
    monkeypatch.setattr(frame_index, "CACHE_DIR", tmp_path / "index")
    path = write_xyz(tmp_path / "a.xyz")
    store = scan(path)
    identity = store.identity
    del store
    gc.collect()
    return path, tmp_path / "parsed" / "batches", identity


def _needs_fast_parser():
    from ffast.readers import text
    if not text.fast_parser_usable():
        pytest.skip("Optional fast parser unavailable")


def test_verify_exits_0_and_says_all_verified(tmp_path, monkeypatch, capsys):
    _needs_fast_parser()
    path, folder, identity = _verify_setup(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as stop:
        main(["cache", "verify", path])
    out = capsys.readouterr().out
    assert stop.value.code == 0
    assert out.splitlines()[0].startswith(path) and "cache" in out.splitlines()[0]
    assert "source" in out and "batches" in out and out.rstrip().endswith("all verified")


def test_verify_exits_1_and_counts_the_mismatches(tmp_path, monkeypatch, capsys):
    import numpy as np
    _needs_fast_parser()
    path, folder, identity = _verify_setup(tmp_path, monkeypatch)
    victim = sorted(folder.glob(f"{identity}-[0-9]*-[0-9]*.npz"))[1]
    with np.load(victim) as z:
        data = {k: z[k].copy() for k in z.files}
    key = next(k for k in data if k.startswith("forces"))
    data[key].flat[3] += 0.5
    np.savez(victim, **data)
    with pytest.raises(SystemExit) as stop:
        main(["cache", "verify", path])
    out = capsys.readouterr().out
    assert stop.value.code == 1
    assert victim.name in out and "1 mismatch. Deleted" in out.rstrip().splitlines()[-1]


def test_verify_exits_2_when_nothing_could_be_checked(tmp_path, monkeypatch, capsys):
    root = tmp_path / "cache"
    root.mkdir()
    monkeypatch.setattr(frame_cache, "CACHE_DIR", root)
    path = tmp_path / "never.xyz"
    path.write_text("1\n\nH 0 0 0\n")
    with pytest.raises(SystemExit) as stop:
        main(["cache", "verify", str(path)])
    assert stop.value.code == 2
    assert capsys.readouterr().out.rstrip().splitlines()[-1].startswith("nothing could be checked: no finished cache")
