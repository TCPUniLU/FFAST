"""Sides of the ingestion benchmark: environments, no timing (ADR 0105 §3)."""

import subprocess
from pathlib import Path

import pytest

from benchmarks.ingest import sides
from benchmarks.ingest.sides import Side, SideError, check_side, parse_side, parse_sides, side_env


def test_parse_side_expands_the_home_folder():
    side = parse_side("old=~/wt/old")
    assert side.name == "old" and side.root == Path("~/wt/old").expanduser().resolve()


@pytest.mark.parametrize("text", ["old", "=x", "old="])
def test_parse_side_rejects_a_missing_part(text):
    with pytest.raises(SideError):
        parse_side(text)


def test_a_duplicate_side_name_is_rejected(tmp_path):
    with pytest.raises(SideError, match="twice"):
        parse_sides([f"a={tmp_path}", f"a={tmp_path}"])


def _fake_git(monkeypatch, diff_code, commit="abc123"):
    def git(root, *args):
        out = {"rev-parse": commit + "\n", "status": ""}.get(args[0], "")
        code = diff_code if args[0] == "diff" else 0
        return subprocess.CompletedProcess(args, code, out, "")

    monkeypatch.setattr(sides, "_git", git)


def test_a_side_without_a_compiled_parser_uses_the_harness_one(tmp_path, monkeypatch):
    side_root, harness = tmp_path / "side", tmp_path / "harness"
    side_root.mkdir()
    _fake_git(monkeypatch, diff_code=0)
    env, info = side_env(Side("old", side_root), harness, {"PYTHONPATH": "/x"})
    assert info["native_from"] == "harness" and info["commit"] == "abc123"
    assert env["PYTHONPATH"].split(":") == [
        str(side_root), str(harness / sides.NATIVE_REL), "/x"]


def test_a_changed_parser_source_asks_for_a_build(tmp_path, monkeypatch):
    side_root = tmp_path / "side"
    side_root.mkdir()
    _fake_git(monkeypatch, diff_code=1)
    with pytest.raises(SideError, match="maturin develop"):
        side_env(Side("old", side_root), tmp_path / "harness", {})


def test_a_side_with_a_compiled_parser_uses_its_own(tmp_path, monkeypatch):
    folder = tmp_path / sides.NATIVE_REL / "ffast_xyz"
    folder.mkdir(parents=True)
    (folder / "_ffast_xyz.abi3.so").write_bytes(b"")
    _fake_git(monkeypatch, diff_code=1)
    env, info = side_env(Side("new", tmp_path), tmp_path / "harness", {})
    assert info["native_from"] == "side"
    assert str(tmp_path / sides.NATIVE_REL) in env["PYTHONPATH"]


def test_check_side_refuses_a_folder_without_ffast(tmp_path):
    side = Side("empty", tmp_path.resolve())
    env = {"PYTHONPATH": str(tmp_path)}
    with pytest.raises(SideError):
        check_side(side, env)
