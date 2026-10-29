"""Single owner of per-object session metadata (path, name, type, ...).

This used to be a raw ``env.info['objects']`` dict mutated at ~12 sites across
six files — Environment, both registries, the remote session, persistence, and
the ghost loader all ``setdefault`` / ``pop`` / index into it directly. No
module owned the lifecycle, so consistency was nobody's job: the
delete-not-persisted bug lived exactly here (a deleted object kept its metadata
because some writer forgot to ``pop``).

``ObjectCatalog`` makes the dict private implementation behind a small
interface. Writers go through :meth:`register` / :meth:`prune`, readers through
:meth:`get` / ``in``, and persistence through :meth:`snapshot` / :meth:`load`.
The prune-on-delete logic now lives in one place and is testable without a GUI.

The metadata payload per object is the existing free-form dict
(``{"path", "name", "type", ...}``); the catalog owns *which fingerprints
exist*, not the shape of each entry.
"""


class ObjectCatalog:
    """Owns the fingerprint→metadata map for one Environment."""

    def __init__(self):
        self._objects = {}

    def register(self, fingerprint, info):
        """Record (or overwrite) the metadata for one object."""
        self._objects[fingerprint] = info

    def prune(self, fingerprint):
        """Drop an object's metadata. No-op if absent — delete-safe."""
        self._objects.pop(fingerprint, None)

    def get(self, fingerprint, default=None):
        """Return one object's metadata, or ``default`` if unknown."""
        return self._objects.get(fingerprint, default)

    def __contains__(self, fingerprint):
        return fingerprint in self._objects

    def snapshot(self):
        """A shallow copy of the whole map for serialization.

        Callers iterate / serialize this without being able to mutate the
        catalog's private store.
        """
        return dict(self._objects)

    def load(self, mapping):
        """Merge persisted metadata in (does not clear existing entries)."""
        self._objects.update(mapping or {})
