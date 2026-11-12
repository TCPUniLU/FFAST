import pytest

from ffast.readers import registry
from tests.ffast.readers.toy_reader import ToyReader


@pytest.fixture(autouse=True)
def _registry_as_it_was():
    """Each test registers the readers it needs and leaves the registry as it found it."""
    saved = dict(registry._readers), registry._discovered
    ToyReader.asked = []
    yield
    registry._readers.clear()
    registry._readers.update(saved[0])
    registry._discovered = saved[1]


@pytest.fixture
def toy():
    registry.register(ToyReader)
    return ToyReader


@pytest.fixture
def only_my_readers():
    """An empty registry, so a test sees exactly the readers it registers (the
    bundled ones claim ``*`` and ``*.npz``)."""
    registry._readers.clear()
    registry._discovered = True
