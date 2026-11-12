from ase.data import chemical_symbols

from tests.ffast.readers.example_mol import MolReader


class MolWriter:
    skipped = 0                  # frames left out; this format leaves none out

    def __init__(self, path):
        self.out = open(path, "w")

    def write(self, chunk):      # called again for each chunk, the dict read() returns
        energies = next((v for k, v in chunk.items() if k.startswith("energy:")), None)
        start = 0
        for k, count in enumerate(chunk["n_atoms"]):
            self.out.write(f"{count}\n{0.0 if energies is None else energies[k]!r}\n")
            for z, xyz in zip(chunk["numbers"][start:start + count], chunk["positions"][start:start + count]):
                self.out.write(f"{chemical_symbols[z]} {xyz[0]!r} {xyz[1]!r} {xyz[2]!r}\n")
            start += count

    def close(self):
        self.out.close()


class MolReaderThatWrites(MolReader):
    name = "mol-writer"
    write_formats = [{"format": "mol", "label": "Mol", "ext": ".mol", "can_write": {
        "energies": 1, "forces": 0, "cell": False, "frame_fields": False, "atom_fields": False}}]

    @staticmethod
    def open_writer(path, contents, format=None):
        return MolWriter(path)
