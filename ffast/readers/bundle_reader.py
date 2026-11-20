"""The reader of ``*.ffast.json``: a portable bundle opens as a dataset (ADR 0110).

It answers the questions a load asks of any reader (what the file holds, how
many frames) from the bundle's description, and gives a load the same kind of
result a memory-saving XYZ load gives: the columns of an ``IndexedStore``.
"""
from __future__ import annotations

import os

from ffast.readers import bundle
from ffast.readers.contract import ReaderError


class BundleReader:
    name = "ffast-bundle"
    extensions = ["*.ffast.json"]
    package = None            # bundled
    hidden = True             # not a Type of its own: "ase (auto)" resolves to it for these files
    cache_version = 1

    def __init__(self, path):
        self.path = path

    def contents(self):
        return dict(bundle.read_description(self.path)["contents"])

    def n_frames(self):
        return int(bundle.read_description(self.path)["n_frames"])

    @classmethod
    def load_whole(cls, path, *, stride=0, select=None, progress=None, workers=None):
        """``(arrays, contents, frames, n_file_frames)`` of the bundle at
        ``path``. A bundle loads whole: a stride, or a selection that is not
        every frame, is refused."""
        from ffast.readers.bundle_store import BundleStore
        from ffast.readers.indexed import IndexedArrays

        name = os.path.basename(os.path.dirname(os.path.abspath(path)))
        if stride and int(stride) > 1:
            raise ReaderError(f"The bundle {name} loads whole: every {int(stride)}th frame cannot be "
                              f"taken from it. Load it with a stride of 1.")
        store = BundleStore(cls, path, workers=workers)
        n = len(store.counts)
        if select is not None and [int(f) for f in select(n)] != list(range(n)):
            raise ReaderError(f"The bundle {name} loads whole: it cannot be loaded as a sample or "
                              f"as a part of its frames.")
        if progress:
            progress(n, n)
        return IndexedArrays(store), store.contents, list(range(n)), n
