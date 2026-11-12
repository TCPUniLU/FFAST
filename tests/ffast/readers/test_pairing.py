"""Energy and force names that match are paired (``dft`` with ``dft``); the user
can choose another pairing, and ``''`` means "none"."""
import pytest

from ffast.readers import ReaderError
from ffast.readers.pairing import pair_names


def test_matching_names_pair_and_the_first_match_wins():
    assert pair_names(["dft", "mace"], ["mace", "dft"]) == ("dft", "dft")
    assert pair_names(["a", "mace"], ["b", "mace"]) == ("mace", "mace")


def test_with_no_match_each_side_takes_its_first_name():
    assert pair_names(["a"], ["b"]) == ("a", "b")


def test_a_file_with_one_kind_has_none_of_the_other():
    assert pair_names(["dft"], []) == ("dft", None)
    assert pair_names([], ["dft"]) == (None, "dft")
    assert pair_names([], []) == (None, None)


def test_a_chosen_name_decides_its_side_and_the_other_follows_by_name():
    assert pair_names(["dft", "mace"], ["dft", "mace"], energy="mace") == ("mace", "mace")
    assert pair_names(["dft", "mace"], ["dft", "mace"], force="mace") == ("mace", "mace")
    # no same-named partner: the first of the other side
    assert pair_names(["x", "y"], ["f"], energy="y") == ("y", "f")


def test_the_user_can_pair_names_that_do_not_match():
    assert pair_names(["dft", "mace"], ["dft", "mace"], energy="dft", force="mace") == ("dft", "mace")


def test_an_empty_string_means_none_and_is_not_replaced():
    assert pair_names(["dft"], ["dft"], energy="", force="dft") == (None, "dft")
    assert pair_names(["dft"], ["dft"], energy="dft", force="") == ("dft", None)
    assert pair_names(["dft"], ["dft"], energy="", force="") == (None, None)


def test_an_unknown_chosen_name_is_refused_and_lists_the_names():
    with pytest.raises(ReaderError, match=r"p.toy has no energy named 'x' \(it has: dft, mace\)"):
        pair_names(["dft", "mace"], ["dft"], energy="x", file="p.toy")
    with pytest.raises(ReaderError, match=r"p.toy has no force named 'x' \(it has: none\)"):
        pair_names(["dft"], [], force="x", file="p.toy")
