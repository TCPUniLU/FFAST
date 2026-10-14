"""Try a formula on a whole dataset without saving it (ADR 0057 rule 12)."""
import numpy as np
import pytest

from ffast.metrics import dims
from ffast.metrics.registry import MetricRegistry
from ffast.metrics.try_formula import TryError, try_formula


@pytest.fixture
def registry():
    r = MetricRegistry()

    @r.metric(id="b.e", inputs={"x": "reference.energies"}, shape=(dims.N_frames,), unit="energy")
    def e(x):
        return x

    @r.metric(id="b.q", inputs={"x": "reference.atoms.q"}, shape=(dims.N_atoms,), unit="x")
    def q(x):
        return x

    @r.metric(id="b.f", inputs={"x": "reference.forces"}, shape=(dims.N_atoms, dims.xyz), unit="force")
    def f(x):
        return x

    @r.metric(id="b.s", inputs={"x": "reference.energies"}, shape=(dims.scalar,), unit="x")
    def s(x):
        return 1.0

    return r


N_ATOMS = np.array([2, 3, 1, 2])           # 4 frames, 8 atoms
ENERGY = np.array([1.0, 2.0, 3.0, 4.0])
CHARGE = np.arange(1.0, 9.0)               # per atom


def _resolve(ref):
    return {"b.e": ENERGY, "b.q": CHARGE}.get(ref)


def test_statistics_over_all_frames(registry):
    r = try_formula("e * 10", {"e": "b.e"}, registry, lambda ref: ENERGY, N_ATOMS)
    assert (r.min, r.mean, r.max, r.count, r.bad_count) == (10.0, 25.0, 40.0, 4, 0)
    assert r.shape == "N_frames" and r.bad_frames == []
    assert sum(r.histogram["counts"]) == 4


def test_chunking_gives_the_same_answer_as_one_pass(registry):
    one = try_formula("e / n_atoms", {"e": "b.e"}, registry, lambda ref: ENERGY, N_ATOMS, chunk_frames=100)
    many = try_formula("e / n_atoms", {"e": "b.e"}, registry, lambda ref: ENERGY, N_ATOMS, chunk_frames=1)
    assert one.to_dict() == many.to_dict()


def test_progress_is_reported_after_each_chunk(registry):
    seen = []
    try_formula("e", {"e": "b.e"}, registry, lambda ref: ENERGY, N_ATOMS, chunk_frames=3,
                on_progress=lambda done, total: seen.append((done, total)))
    assert seen == [(3, 4), (4, 4)]


def test_cancel_stops_cleanly_between_chunks(registry):
    calls = {"n": 0}

    def cancelled():
        calls["n"] += 1
        return calls["n"] > 1           # run the first chunk, then stop

    r = try_formula("e", {"e": "b.e"}, registry, lambda ref: ENERGY, N_ATOMS,
                    chunk_frames=1, cancelled=cancelled)
    assert r.cancelled is True


def test_bad_values_are_counted_and_their_frames_listed(registry):
    # e - 3 is zero at frame 2, so 1 / (e - 3) is infinite there
    r = try_formula("1 / (e - 3)", {"e": "b.e"}, registry, lambda ref: ENERGY, N_ATOMS, chunk_frames=2)
    assert r.bad_count == 1 and r.bad_frames == [2]
    assert r.count == 4 and r.max == 1.0 and r.min == pytest.approx(-1.0)


def test_per_atom_bad_values_report_the_frame_they_are_in(registry):
    # atoms 0-1 are frame 0, atoms 2-4 frame 1, atom 5 frame 2, atoms 6-7 frame 3
    r = try_formula("1 / (q - 5)", {"q": "b.q"}, registry, _resolve, N_ATOMS, chunk_frames=2)
    assert r.shape == "N_atoms" and r.count == 8
    assert r.bad_count == 1 and r.bad_frames == [1]        # q == 5 is atom 4, frame 1


def test_per_atom_chunks_cut_atoms_along_frame_boundaries(registry):
    r = try_formula("q", {"q": "b.q"}, registry, _resolve, N_ATOMS, chunk_frames=1)
    assert (r.min, r.max, r.mean, r.count) == (1.0, 8.0, 4.5, 8)


def test_vector_results_count_every_component(registry):
    forces = np.ones((8, 3))
    r = try_formula("norm(F)", {"F": "b.f"}, registry, lambda ref: forces, N_ATOMS, chunk_frames=2)
    assert r.shape == "N_atoms" and r.count == 8
    assert r.max == pytest.approx(np.sqrt(3))


def test_only_the_first_bad_frames_are_listed(registry):
    many = np.zeros(100)
    r = try_formula("1 / e", {"e": "b.e"}, registry, lambda ref: many, np.ones(100), chunk_frames=10)
    assert r.bad_count == 100 and len(r.bad_frames) == 20 and r.bad_frames == list(range(20))
    assert r.min is None and r.histogram is None


def test_a_scalar_result_is_one_pass(registry):
    r = try_formula("s * 2", {"s": "b.s"}, registry, lambda ref: np.float64(1.0), N_ATOMS)
    assert r.shape == "scalar" and (r.min, r.max, r.count) == (2.0, 2.0, 1)


def test_an_unavailable_input_is_named(registry):
    with pytest.raises(TryError, match="b.e is not available"):
        try_formula("e", {"e": "b.e"}, registry, lambda ref: None, N_ATOMS)


def test_a_formula_error_is_a_try_error(registry):
    with pytest.raises(TryError, match="valid expression"):
        try_formula("e +", {"e": "b.e"}, registry, lambda ref: ENERGY, N_ATOMS)
