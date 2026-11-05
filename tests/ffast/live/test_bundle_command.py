"""`ffast bundle` and the LAMMPS include file it writes (ADR 0086)."""

from __future__ import annotations

import pytest

from ffast.live.cli import main
from ffast.live.include_file import include_text
from tests.ffast.live.bundle_helpers import make_bundle, write_frames


def test_the_include_sets_up_the_dump_the_switch_and_the_stop_check():
    text = include_text("/abs/aspirin.bundle", ["C", "H", "O"], every=50)
    lines = text.splitlines()
    assert "variable ffast_on    index 0" in lines
    assert "variable ffast_every index 50" in lines
    assert any(l.startswith('if "${ffast_on} == 0" then') and "jump SELF ffast_end" in l for l in lines)
    assert ("dump        ffast all custom ${ffast_every} ${ffast_dir}/frames.lammpstrj "
            "id type element x y z") in lines
    assert ("dump_modify ffast sort id flush yes element C H O time yes units yes "
            "format float %.17g") in lines
    assert "variable    ffast_stop equal is_file(${ffast_dir}/STOP)" in lines
    assert "fix         ffast_halt all halt ${ffast_every} v_ffast_stop != 0 error soft" in lines
    assert lines[-1] == "label       ffast_end"
    # tells the scorer which bundle, through the run folder
    assert "bundle /abs/aspirin.bundle" in lines
    assert 'end""" file ${ffast_dir}/include.txt screen no' in lines


def test_the_include_refuses_a_bundle_path_with_a_space():
    with pytest.raises(ValueError, match="space"):
        include_text("/a b/x.bundle", ["C"])


def test_ffast_bundle_writes_a_bundle_that_loads_and_its_include(tmp_path, capsys):
    import detectana

    bundle_dir, include = make_bundle(tmp_path, every=20)
    b = detectana.load(bundle_dir)
    assert b.n_atoms == 21 and b.elements[0] == "C"
    text = include.read_text()
    assert f"bundle {bundle_dir}" in text and "index 20" in text
    out = capsys.readouterr().out
    assert f"include {include}" in out


def test_ffast_bundle_refuses_the_same_data_twice(tmp_path, capsys):
    train = write_frames(tmp_path / "t.xyz", 50, seed=1)
    rc = main(["bundle", "--train", str(train), "--valid", str(train),
               "--elements", "C H O", "-o", str(tmp_path / "x")])
    assert rc == 1
    assert "same data" in capsys.readouterr().err
    assert not (tmp_path / "x.bundle").exists()


def test_ffast_bundle_refuses_elements_missing_from_the_list(tmp_path, capsys):
    train = write_frames(tmp_path / "t.xyz", 50, seed=1)
    valid = write_frames(tmp_path / "v.xyz", 50, seed=2)
    rc = main(["bundle", "--train", str(train), "--valid", str(valid),
               "--elements", "C H", "-o", str(tmp_path / "x")])
    assert rc == 1
    assert "lacks O" in capsys.readouterr().err


def test_ffast_bundle_refuses_an_output_path_with_a_space(tmp_path, capsys):
    train = write_frames(tmp_path / "t.xyz", 50, seed=1)
    valid = write_frames(tmp_path / "v.xyz", 50, seed=2)
    rc = main(["bundle", "--train", str(train), "--valid", str(valid),
               "--elements", "C H O", "-o", str(tmp_path / "a b" / "x")])
    assert rc == 1
    assert "space" in capsys.readouterr().err


def test_the_launcher_hands_bundle_to_the_live_commands(monkeypatch):
    import launcher

    seen = {}
    monkeypatch.setattr("ffast.live.cli.main", lambda argv: seen.setdefault("argv", argv) and 0)
    with pytest.raises(SystemExit):
        launcher.main(["bundle", "--help-me"])
    assert seen["argv"] == ["bundle", "--help-me"]
