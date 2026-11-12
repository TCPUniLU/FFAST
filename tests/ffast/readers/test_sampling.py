"""Choosing frames by energy bins (ADR 0096): pure numpy, no FFAST objects."""
import numpy as np
import pytest

from ffast.readers.sampling import SampleResult, sample_by_energy


def _skewed(seed=1, low=10_000, high=30):
    rng = np.random.default_rng(seed)
    return np.concatenate([rng.uniform(0, 1, low), rng.uniform(9, 10, high)])


def _four_bins(n_per_bin=(1000, 1000, 3, 1000), seed=2):
    """Energies on 0..4 so that 4 equal bins hold exactly these counts."""
    rng = np.random.default_rng(seed)
    parts = [rng.uniform(i + 0.01, i + 0.99, c) for i, c in enumerate(n_per_bin)]
    parts.append(np.array([0.0, 4.0]))          # pin the range; they land in bins 0 and 3
    return np.concatenate(parts)


def test_even_keeps_the_rare_high_energy_frames_and_exactly_n():
    e = _skewed()
    res = sample_by_energy(e, 300, method="even")
    assert isinstance(res, SampleResult)
    assert len(res.frames) == 300
    # Proportional sampling would keep about 1 of the 30 high frames per bin; even keeps all 30.
    assert (res.frames >= 10_000).sum() == 30
    # No bin that had frames is left empty.
    assert np.all(res.counts_kept[res.counts_all > 0] >= 1)


def test_even_shares_differ_by_at_most_one_when_no_bin_is_short():
    e = _four_bins((1000, 1000, 1000, 1000))
    res = sample_by_energy(e, 102, method="even", bins=4)
    # 102 = 4 * 25 + 2; the remainder goes to the lowest bins first.
    assert res.counts_kept.tolist() == [26, 26, 25, 25]


def test_proportional_follows_the_full_histogram_within_one_per_bin():
    rng = np.random.default_rng(6)
    e = np.concatenate([rng.normal(0, 1, 6000), rng.normal(2, 0.5, 2000)])
    res = sample_by_energy(e, 300, method="proportional", bins=10)
    assert len(res.frames) == 300
    expected = res.counts_all / res.counts_all.sum() * 300
    assert np.all(np.abs(res.counts_kept - expected) <= 1.0 + 1e-9)
    assert np.all(res.counts_kept[res.counts_all > 0] >= 1)


def test_proportional_on_a_skewed_set_keeps_n_and_one_per_sparse_bin():
    # Many sparse bins each round up to 1, and the big bins pay for it: the bound of
    # one per bin cannot hold, but the total and the minimum of one do.
    e = _skewed()
    res = sample_by_energy(e, 300, method="proportional")
    assert len(res.frames) == 300
    assert np.all(res.counts_kept[res.counts_all > 0] >= 1)
    sparse = int(((res.counts_all > 0) & (res.counts_all < 100)).sum())
    expected = res.counts_all / res.counts_all.sum() * 300
    assert np.all(np.abs(res.counts_kept - expected) <= 1.0 + sparse)


def test_proportional_gives_every_non_empty_bin_at_least_one_even_when_tiny():
    e = _four_bins((5000, 5000, 3, 5000))
    res = sample_by_energy(e, 10, method="proportional", bins=4)
    assert len(res.frames) == 10
    assert res.counts_kept[2] >= 1


def test_the_automatic_bin_count_is_freedman_diaconis():
    e = np.arange(0, 1001, dtype=float)       # IQR 500, range 1000
    res = sample_by_energy(e, 100)
    # width = 2 * 500 / 100 ** (1/3) = 215.4 ; 1000 / 215.4 = 4.64 -> 5 bins
    assert res.bins_used == 5
    assert len(res.edges) == 6
    assert res.counts_all.sum() == 1001


def test_the_bin_count_is_capped_at_half_n():
    rng = np.random.default_rng(3)
    e = np.concatenate([rng.uniform(0, 1, 1000), [1e6, 1e6 + 1]])   # tiny IQR, huge range
    res = sample_by_energy(e, 10)
    assert res.bins_used == 5


def test_a_bin_count_can_be_chosen():
    e = np.arange(0, 1001, dtype=float)
    res = sample_by_energy(e, 100, bins=8)
    assert res.bins_used == 8
    assert len(res.counts_all) == 8


def test_a_short_bin_gives_all_it_has_and_the_total_is_still_n():
    e = _four_bins()
    res = sample_by_energy(e, 100, method="even", bins=4)
    assert res.counts_all.tolist() == [1001, 1000, 3, 1001]
    assert res.counts_kept[2] == 3
    assert res.counts_kept.sum() == 100
    # The short bin could not take its 25: the 22 left over go to the other three bins.
    assert res.counts_kept[[0, 1, 3]].min() >= 32


def test_the_same_seed_gives_the_same_frames_and_another_seed_other_frames():
    e = _skewed()
    a = sample_by_energy(e, 300, seed=7)
    b = sample_by_energy(e, 300, seed=7)
    c = sample_by_energy(e, 300, seed=8)
    assert a.frames.tolist() == b.frames.tolist()
    assert a.frames.tolist() != c.frames.tolist()
    assert a.counts_kept.tolist() == c.counts_kept.tolist()


def test_the_default_seed_is_42_and_the_global_random_state_is_left_alone():
    e = _skewed()
    np.random.seed(5)
    before = np.random.get_state()[1].copy()
    a = sample_by_energy(e, 200)
    assert np.array_equal(np.random.get_state()[1], before)
    assert a.frames.tolist() == sample_by_energy(e, 200, seed=42).frames.tolist()


def test_energy_is_per_atom_when_atom_counts_differ():
    rng = np.random.default_rng(4)
    n_atoms = rng.integers(10, 50, 2000)
    per_atom = rng.uniform(-5, -4, 2000)
    res = sample_by_energy(per_atom * n_atoms, 100, n_atoms=n_atoms)
    assert res.per_atom is True
    assert res.edges[0] == pytest.approx(per_atom.min())
    assert res.edges[-1] == pytest.approx(per_atom.max())


def test_energy_stays_total_when_every_frame_has_the_same_atoms():
    e = np.linspace(-100, -90, 500)
    res = sample_by_energy(e, 50, n_atoms=np.full(500, 12))
    assert res.per_atom is False
    assert res.edges[0] == pytest.approx(-100)
    res = sample_by_energy(e, 50)
    assert res.per_atom is False


def test_frames_without_a_finite_energy_are_left_out_and_counted():
    e = np.linspace(0, 1, 100)
    e[[3, 50]] = np.nan
    e[70] = np.inf
    res = sample_by_energy(e, 20)
    assert res.left_out == 3
    assert not {3, 50, 70} & set(res.frames.tolist())
    assert len(res.frames) == 20


def test_n_at_or_above_the_frames_with_an_energy_keeps_them_all():
    e = np.linspace(0, 1, 50)
    e[0] = np.nan
    res = sample_by_energy(e, 1000)
    assert res.kept_all is True
    assert res.frames.tolist() == list(range(1, 50))
    assert res.left_out == 1
    assert res.counts_kept.tolist() == res.counts_all.tolist()
    res = sample_by_energy(e, 49)
    assert res.kept_all is True


def test_n_below_two_is_refused_in_plain_words():
    with pytest.raises(ValueError, match="at least 2"):
        sample_by_energy(np.arange(10.0), 1)
    with pytest.raises(ValueError, match="at least 2"):
        sample_by_energy(np.arange(10.0), 0)


def test_a_file_with_no_finite_energy_is_refused():
    with pytest.raises(ValueError, match="no energ"):
        sample_by_energy(np.full(5, np.nan), 3)


def test_all_equal_energies_make_one_bin_and_pick_n_at_random():
    e = np.full(1000, -3.5)
    res = sample_by_energy(e, 40)
    assert res.bins_used == 1
    assert len(res.frames) == 40
    other = sample_by_energy(e, 40, seed=1)
    assert res.frames.tolist() != other.frames.tolist()


def test_the_frames_are_sorted_without_duplicates_and_are_file_frame_numbers():
    e = _skewed()
    for method in ("even", "proportional"):
        res = sample_by_energy(e, 500, method=method)
        f = res.frames
        assert f.dtype == np.int64
        assert np.all(np.diff(f) > 0)
        assert f.min() >= 0 and f.max() < len(e)


def test_the_preview_counts_add_up():
    e = _skewed()
    res = sample_by_energy(e, 300)
    assert res.counts_all.sum() == len(e)
    assert res.counts_kept.sum() == 300
    assert len(res.edges) == res.bins_used + 1
    # The kept counts are those of the kept frames.
    again = np.histogram(e[res.frames], bins=res.edges)[0]
    assert again.tolist() == res.counts_kept.tolist()


def test_an_unknown_method_is_refused():
    with pytest.raises(ValueError, match="method"):
        sample_by_energy(np.arange(10.0), 5, method="loudest")
