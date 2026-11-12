"""PROBE_DATASET_KEYS asks the chosen reader's contents(); the reply keeps the
shape the ASE probe gives (ADR 0095, step 2)."""
import asyncio
from types import SimpleNamespace

from ffast.protocol import control
from ffast.protocol.rpc import unpack
from ffast.session.server_session import ServerSession
from tests.ffast.readers.toy_reader import ToyReader, make_frames, write_toy


def _probe(path, dataset_type, datasetTypes=None):
    session = ServerSession.__new__(ServerSession)
    session.env = SimpleNamespace(datasetTypes=datasetTypes or {})
    sent = []

    async def emit(data):
        sent.append(data)

    session._emit = emit
    asyncio.run(session._on_probe_dataset_keys(path, dataset_type))
    [(event, args, kwargs)] = [unpack(d) for d in sent]
    assert event == control.DATASET_KEYS_RESPONSE and args == [path]
    return kwargs


def test_a_readers_names_come_back_as_energy_and_force_keys(toy, tmp_path):
    path = write_toy(tmp_path / "a.toy", make_frames(3, extra=["mace"]))
    assert _probe(path, "toy") == {
        "energy_keys": ["dft", "mace"], "force_keys": ["dft", "mace"],
        "has_calculator_energy": False, "has_calculator_forces": False, "error": None}


def test_a_reader_that_cannot_read_the_file_replies_with_its_message(toy, tmp_path):
    from ffast.readers import ReaderError, registry

    class Strict(ToyReader):
        name = "strict"

        def contents(self):
            raise ReaderError("Frame 12 has no forces")

    registry.register(Strict)
    path = write_toy(tmp_path / "a.toy", make_frames(2))
    assert _probe(path, "strict")["error"] == "Frame 12 has no forces"
    reply = _probe(str(tmp_path / "missing.toy"), "toy")
    assert reply["error"].startswith("toy reader could not read missing.toy")
    assert reply["energy_keys"] == []


def test_a_type_registered_as_a_dataset_loader_is_not_probed_as_a_reader(toy, tmp_path):
    """Until step 3 the old types keep their own path: a dataset type named
    like a reader wins."""
    path = write_toy(tmp_path / "a.toy", make_frames(2))
    reply = _probe(path, "toy", datasetTypes={"toy": object})
    assert reply["energy_keys"] == []              # went down the ASE probe, which cannot read it
    assert reply["error"]
