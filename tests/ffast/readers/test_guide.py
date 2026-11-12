"""The guide to writing a reader (docs/writing-a-reader.md) quotes the example
readers in this folder word for word, and they follow the rules: the guide
cannot drift from the code."""
from pathlib import Path

import pytest

from ffast.readers import check_reader, chunks, registry
from ffast.readers.dataset import ReaderDataset
from tests.ffast.readers.example_mol import MolReader
from tests.ffast.readers.example_mol_text import MolTextReader
from tests.ffast.readers.example_mol_writer import MolReaderThatWrites

HERE = Path(__file__).parent
GUIDE = HERE.parents[2] / "docs" / "writing-a-reader.md"

SAMPLE = """3
-76.4
O 0.0 0.0 0.117
H 0.0 0.757 -0.469
H 0.0 -0.757 -0.469
3
-76.5
O 0.0 0.0 0.12
H 0.0 0.76 -0.47
H 0.0 -0.76 -0.47
3
-76.6
O 0.0 0.0 0.13
H 0.0 0.77 -0.48
H 0.0 -0.77 -0.48
"""


@pytest.fixture
def sample(tmp_path):
    def make(name):
        path = tmp_path / name
        path.write_text(SAMPLE)
        return str(path)
    return make


@pytest.mark.parametrize("example", ["example_mol.py", "example_mol_text.py", "example_mol_writer.py"])
def test_the_guide_quotes_the_example_exactly(example):
    code = (HERE / example).read_text().strip()
    assert code in GUIDE.read_text(), f"{example} is not in docs/writing-a-reader.md as it stands"


def test_the_guides_sample_file_is_the_one_the_examples_read():
    assert SAMPLE.strip() in GUIDE.read_text()


@pytest.mark.parametrize("reader, name", [(MolReader, "a.mol"), (MolTextReader, "a.molt"),
                                          (MolReaderThatWrites, "a.mol")])
def test_each_example_follows_the_rules(sample, reader, name):
    check_reader(reader, sample(name))


@pytest.mark.parametrize("reader, name", [(MolReader, "a.mol"), (MolTextReader, "a.molt")])
def test_each_example_loads_a_dataset(sample, reader, name):
    path = sample(name)
    registry.register(reader)
    dataset = ReaderDataset(path, chunks.read_arrays(reader, path), reader.name)
    assert dataset.N == 3 and dataset.chem == "H2O"
    assert dataset.E.tolist() == [-76.4, -76.5, -76.6]
    assert dataset.getUnits() == {"energy": "eV"}


def test_the_text_reader_gives_the_same_arrays_as_the_plain_one(sample):
    a = chunks.read_arrays(MolReader, sample("a.mol")).arrays
    b = chunks.read_arrays(MolTextReader, sample("a.molt")).arrays
    assert a.keys() == b.keys()
    for key in a:
        assert (a[key] == b[key]).all(), key


def test_the_guide_is_linked_from_the_docs_index_and_the_usage_page():
    docs = GUIDE.parent
    assert "writing-a-reader.md" in (docs / "README.md").read_text()
    assert "writing-a-reader.md" in (docs / "usage.md").read_text()
