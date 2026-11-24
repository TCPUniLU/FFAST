"""The scan's frame index grows window by window without recopying itself."""

import numpy as np

from ffast.readers.indexed import _Appendable


def test_extend_gives_the_same_values_as_concatenating():
    rng = np.random.default_rng(0)
    column, expected = _Appendable([0]), np.array([0])
    for _ in range(300):
        a, b = rng.integers(0, 100, size=rng.integers(0, 50)), rng.integers(0, 100, size=3)
        got = column.extend(a, b)
        expected = np.concatenate((expected, a, b))
        assert got.dtype == np.int64
        np.testing.assert_array_equal(got, expected)


def test_an_earlier_view_keeps_its_values_after_later_windows():
    # A resume snapshot holds the view of its window while the scan goes on.
    column = _Appendable(np.arange(5))
    first = column.extend([10, 11])
    kept = first.copy()
    for i in range(200):                 # forces the room to grow several times
        column.extend(np.full(100, -i))
    np.testing.assert_array_equal(first, kept)


def test_room_grows_by_doubling_not_per_window():
    column, rooms = _Appendable([]), set()
    for _ in range(5000):
        column.extend(np.ones(100, dtype=np.int64))
        rooms.add(id(column._room))
    # 500 000 values: about log2(500 000 / 1024) + 1 rooms, not 5000.
    assert len(rooms) <= 12
