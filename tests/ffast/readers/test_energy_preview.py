"""What the load dialog asks before a sample by energy is loaded (ADR 0096,
step 4): the file's energies as a histogram, and the preview of a sample."""
import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from ffast.io import frame_cache, frame_index as fi
from ffast.protocol import control
from ffast.protocol.rpc import unpack
from ffast.readers import chunks, registry, text
from ffast.readers.dataset import ReaderDataset
from ffast.readers.energy_sample import energy_histogram, energy_preview
from ffast.readers.sampling import sample_by_energy
from ffast.session.server_session import ServerSession
from tests.ffast.readers.toy_reader import ToyEnergyReader, ToyTextReader, make_frames, write_toy


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(fi, "CACHE_DIR", tmp_path / "index")
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "arrays")
    fi.clear_memory_cache()
    text.clear_memory_cache()
    registry.register(ToyEnergyReader)
    registry.register(ToyTextReader)
    yield
    fi.clear_memory_cache()
    text.clear_memory_cache()


def _file(tmp_path, n=200, variable=False, missing=()):
    frames = make_frames(n, variable=variable)
    rng = np.random.default_rng(3)
    for i, f in enumerate(frames):
        f["energy"] = float(rng.uniform(0, 1)) if i % 20 else float(rng.uniform(9, 10))
    for i in missing:
        frames[i]["energy"] = float("nan")
    path = write_toy(tmp_path / "run.toys", frames)
    return path, np.array([f["energy"] for f in frames])


def test_the_histogram_has_200_bins_from_the_lowest_to_the_highest_energy(tmp_path):
    path, energies = _file(tmp_path, missing=(4,))
    got = energy_histogram(ToyEnergyReader, path)
    assert got["names"] == ["dft"] and got["energy"] == "dft"
    assert got["per_atom"] is False
    assert len(got["edges"]) == 201 and len(got["counts"]) == 200
    finite = energies[np.isfinite(energies)]
    assert got["edges"][0] == pytest.approx(finite.min()) and got["edges"][-1] == pytest.approx(finite.max())
    assert sum(got["counts"]) == 199
    assert got["n_frames"] == 200 and got["left_out"] == 1


def test_the_histogram_is_per_atom_when_atom_counts_differ(tmp_path):
    path, energies = _file(tmp_path, variable=True)
    got = energy_histogram(ToyEnergyReader, path)
    assert got["per_atom"] is True
    counts = np.array([3 + (i % 3) for i in range(200)])
    assert got["edges"][0] == pytest.approx((energies / counts).min())


def test_the_preview_is_what_the_load_would_keep(tmp_path):
    path, energies = _file(tmp_path)
    got = energy_preview(ToyEnergyReader, path, {"n": 20, "seed": 5, "method": "proportional"})
    want = sample_by_energy(energies, 20, seed=5, method="proportional")
    assert got["counts_kept"] == want.counts_kept.tolist()
    assert got["counts_all"] == want.counts_all.tolist()
    assert got["edges"] == pytest.approx(want.edges.tolist())
    assert got["bins_used"] == want.bins_used and got["kept"] == 20
    assert got["energy"] == "dft" and got["per_atom"] is False and got["left_out"] == 0


def test_a_preview_that_cannot_be_made_says_why(tmp_path):
    path, _ = _file(tmp_path)
    assert energy_preview(ToyEnergyReader, path, {"n": 1})["error"] == (
        "Sampling by energy needs to keep at least 2 frames.")
    assert energy_preview(ToyTextReader, path, {"n": 5})["error"] == (
        "run.toys cannot be sampled by energy: the toytext reader does not list energies")


# ── through the server ──────────────────────────────────────────────────────

def _ask(handler, *args, **kwargs):
    session = ServerSession.__new__(ServerSession)
    session.env = SimpleNamespace(datasetTypes={})
    sent = []

    async def emit(data):
        sent.append(data)

    session._emit = emit
    asyncio.run(getattr(session, handler)(*args, **kwargs))
    return [unpack(d) for d in sent]


def test_the_server_answers_the_energy_probe_and_the_preview(tmp_path):
    path, _ = _file(tmp_path)
    [(event, args, kw)] = _ask("_on_probe_dataset_energies", path, "toyenergy")
    assert event == control.DATASET_ENERGIES and list(args) == [path]
    assert kw["names"] == ["dft"] and len(kw["counts"]) == 200 and kw["error"] is None
    [(event, args, kw)] = _ask("_on_preview_energy_sample", path, "toyenergy",
                               sampling={"n": 20}, preview_id=7)
    assert event == control.ENERGY_SAMPLE_PREVIEW and list(args) == [path]
    assert kw["preview_id"] == 7 and sum(kw["counts_kept"]) == 20 and kw["error"] is None


def test_the_server_says_why_a_file_has_no_energies_to_show(tmp_path):
    path, _ = _file(tmp_path)
    [(_, _, kw)] = _ask("_on_probe_dataset_energies", path, "toytext")
    assert kw["error"] == "run.toys cannot be sampled by energy: the toytext reader does not list energies"


def test_the_key_probe_says_whether_the_file_can_be_sampled_by_energy(tmp_path):
    path, _ = _file(tmp_path)
    [(_, _, kw)] = _ask("_on_probe_dataset_keys", path, "toyenergy")
    assert kw["energy_sampling"] is True
    [(_, _, kw)] = _ask("_on_probe_dataset_keys", path, "toytext")
    assert kw["energy_sampling"] is False
    # A file whose first frames list no energy (contents()) cannot be sampled.
    bare = tmp_path / "bare.xyz"
    bare.write_text("1\nProperties=species:S:1:pos:R:3\nH 0 0 0\n1\nProperties=species:S:1:pos:R:3\nH 0 0 1\n")
    [(_, _, kw)] = _ask("_on_probe_dataset_keys", str(bare), "ase (auto)")
    assert kw["energy_sampling"] is False
    with_energy = tmp_path / "e.xyz"
    with_energy.write_text("1\nenergy=1 Properties=species:S:1:pos:R:3\nH 0 0 0\n")
    [(_, _, kw)] = _ask("_on_probe_dataset_keys", str(with_energy), "ase (auto)")
    assert kw["energy_sampling"] is True


def test_a_sampled_datasets_meta_says_how_it_was_sampled(tmp_path):
    path, _ = _file(tmp_path)
    ds = ReaderDataset(path, chunks.read_arrays(ToyEnergyReader, path, select=lambda n: [1, 4, 9]),
                       "toyenergy")
    assert "sampling" not in ds.toMetaDict()
    ds.sampling = {"method": "even", "n": 3, "seed": 42, "n_file_frames": 200}
    assert ds.toMetaDict()["sampling"] == {"method": "even", "n": 3, "seed": 42, "n_file_frames": 200}
