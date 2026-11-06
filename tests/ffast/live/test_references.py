"""References FFAST made record themselves; the dialog lists them without DetectAna."""

import json
import os
import shutil

from ffast.live.references import describe, record_reference, recorded, references_dir
from tests.ffast.live.bundle_helpers import make_bundle


def test_making_a_reference_records_it_and_it_is_described(tmp_path, monkeypatch):
    monkeypatch.setenv("FFAST_REFERENCES", str(tmp_path / "refs"))
    bundle, include = make_bundle(tmp_path)
    [row] = recorded()
    assert row["bundle"] == str(bundle.resolve()) and row["include"] == str(include.resolve())
    assert row["n_atoms"] == 21 and row["formula"] == "C9H8O4" and row["types"] == ["C", "H", "O"]
    assert row["made_utc"] and row["problem"] is None and row["name"] == "aspirin"


def test_gone_folders_drop_out_and_newest_comes_first(tmp_path, monkeypatch):
    monkeypatch.setenv("FFAST_REFERENCES", str(tmp_path / "refs"))
    a, b = tmp_path / "a.bundle", tmp_path / "b.bundle"
    for d in (a, b):
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps({"molecule": {"n_atoms": 3, "elements": ["O", "H", "H"]}}))
        d.with_suffix(".lmp").write_text("elements O H\n")
    record_reference(a)
    rec_b = record_reference(b)
    data = json.loads(rec_b.read_text())
    data["recorded_utc"] = "2999-01-01T00:00:00+00:00"
    rec_b.write_text(json.dumps(data))
    assert [r["name"] for r in recorded()] == ["b", "a"]
    shutil.rmtree(a)
    assert [r["name"] for r in recorded()] == ["b"]


def test_problems_are_named(tmp_path):
    assert "manifest" in describe(tmp_path / "nothing.bundle")["problem"]
    d = tmp_path / "x.bundle"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"molecule": {"n_atoms": 1, "elements": ["H"]}}))
    assert "x.lmp" in describe(d)["problem"]


def test_the_record_folder_follows_the_environment(tmp_path):
    assert references_dir({"FFAST_REFERENCES": str(tmp_path)}) == tmp_path
    assert str(references_dir({})).endswith(os.path.join(".ffast", "references"))
