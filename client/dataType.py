import logging
import os
import time
import ase.io
from collections import UserList
from events import EventClass

logger = logging.getLogger("FFAST")


class DataEntity:
    unitType = None
    unit = None
    timestamp = 0
    dataType = None

    def __init__(self, dataType, **kwargs):
        self.dataType = dataType
        self.data = {}
        for k, v in kwargs.items():
            self.data[k] = v

        self.timestamp = time.time()

    def get(self, key=None):
        if key is None:
            keys = self.keys()
            if len(keys) == 1:
                return self.data[keys[0]]
            else:
                return None

        return self.data.get(key, None)

    def keys(self):
        return list(self.data.keys())

    def getSubEntity(self, indices):
        if indices is None:
            return self
        return SubDataEntity(self, indices)

    def getAtomFilteredEntity(self, indices):
        if indices is None:
            return self
        return AtomFilteredEntity(self, indices)


class SubDataEntity(DataEntity):
    def __init__(self, parent, indices):
        self.parent = parent
        self.indices = indices

        self.unitType = parent.unitType
        self.unit = parent.unit
        self.timestamp = parent.timestamp
        self.dataType = parent.dataType
        self.data = parent.data

    def get(self, key=None):
        data = self.parent.get(key=key)

        # Handle variable datasets (list of arrays)
        if isinstance(data, list):
            return [data[i] for i in self.indices]
        # Scalars (0-d arrays, plain Python numbers) are not sub-indexable
        if not hasattr(data, 'ndim') or data.ndim == 0:
            return data
        return data[self.indices]


class AtomFilteredEntity(DataEntity):
    def __init__(self, parent, indices):
        self.parent = parent
        self.indices = indices

        self.unitType = parent.unitType
        self.unit = parent.unit
        self.timestamp = parent.timestamp
        self.dataType = parent.dataType
        self.data = parent.data

    def get(self, key=None):
        d = self.parent.get(key=key)
        if len(d.shape) == 1:
            return d[self.indices]
        else:
            return self.parent.get(key=key)[:, self.indices]


class DataType(EventClass):
    modelDependent = False
    datasetDependent = False
    key = None
    data = None
    dependencies = None

    iterable = False  # if True, results are per-config (e.g. forces, energies..., as opposed to distributions)
    atomFilterable = False  # if True, results are per-atom (e.g. forces)
    atomConstant = False  # if True, results are independent of atom filter (e.g. energy, kind of)

    def __init__(self, service):
        super().__init__()
        # The DataService that owns this DataType (ADR 0020). DataTypes only ever
        # reach data-coordination methods (hasData / getCacheKey / setData /
        # cacheKeyToComponents), all of which live on the service — so they hold
        # it directly instead of reaching back through the full Environment.
        self.service = service

    def getCacheKey(self, model=None, dataset=None):
        # Construction routes through the CacheKey value type (CONTEXT.md "Cache
        # Key"); a model/dataset-independent slot stays None -> serializes to the
        # "nil" sentinel, preserving the legacy 3-part string exactly.
        from ffast.cache import CacheKey

        model_fp = None
        if self.modelDependent:
            if model is None:
                logger.error(
                    f"Getting cache key of model dependent DataType"
                    + f"{self}, key {self.key}, but no model was given"
                )
                return None
            model_fp = model if isinstance(model, str) else model.fingerprint

        dataset_fp = None
        if self.datasetDependent:
            if dataset is None:
                logger.exception(
                    f"Getting cache key of dataset dependent DataType"
                    + f"{self}, key {self.key}, but no dataset was given"
                )
                return None
            dataset_fp = dataset if isinstance(dataset, str) else dataset.fingerprint

        return CacheKey(self.key, model_fp, dataset_fp).format()

    def generateData(self, dataset=None, model=None, taskID=None):
        if self.datasetDependent and (dataset is None):
            logger.error(
                f"Getting data of dataset dependent DataType"
                + f"{self}, key {self.key}, but no dataset was given"
            )
            return None

        if self.modelDependent and (model is None):
            logger.error(
                f"Getting data of model dependent DataType"
                + f"{self}, key {self.key}, but no model was given"
            )
            return None

        data = None
        if dataset.isSubDataset and dataset.isAtomFiltered:
            if self.atomFilterable or self.atomConstant:
                return self.generateData(
                    dataset=dataset.parent, model=model, taskID=taskID
                )

        (deps, canGenerate) = self.checkDependencies(
            dataset=dataset, model=model
        )
        if canGenerate:
            data = self.data(dataset, model, taskID=taskID)

            if data is None:
                logger.error(
                    f"DataType {self} generated data but returned None. "
                    + "The .data() method needs to return something when"
                    + " successful"
                )

        return data is not None

    def checkDependencies(self, dataset=None, model=None):
        if self.dependencies is None:
            return [], True

        service = self.service
        deps = []
        for dep in self.dependencies:
            if service.hasData(dep, dataset=dataset, model=model):
                continue

            key = service.getCacheKey(dep, dataset=dataset, model=model)
            deps.append(key)

        # IF ATOM-FILTERED, PARENT DATA CAN BE DEPENDENCY
        if dataset.isSubDataset and dataset.isAtomFiltered:
            if self.atomFilterable or self.atomConstant:
                if not service.hasData(dep, dataset=dataset.parent, model=model):
                    key = service.getCacheKey(
                        dep, dataset=dataset.parent, model=model
                    )
                    deps.append(key)

        return (deps, len(deps) == 0)

    def getGeneratableComponent(self, dataset=None, model=None):
        if self.dependencies is None:
            return [], True

        service = self.service
        generatableComps = []

        initialKey = service.getCacheKey(self.key, model=model, dataset=dataset)
        if initialKey is None:
            logger.warning(f"getCacheKey returned None for dataType={self.key}, model={model}, dataset={dataset}")
            return []

        comps = [initialKey]

        for i in range(100):
            # 100 instead of a while loop just to avoid crashing if infinite
            # loops are created unvoluntarily. Still catching them to fix it
            if i == 99:
                logger.exception(
                    f"Infinite loop in lowest generatable components."
                )

            newComps = []
            for compKey in comps:
                # Skip None values that may have been added
                if compKey is None:
                    logger.warning(f"Skipping None compKey in getGeneratableComponent")
                    continue

                dt, m, d = service.cacheKeyToComponents(
                    compKey, dataTypeObject=True
                )

                (deps, canGenerate) = dt.checkDependencies(dataset=d, model=m)
                if canGenerate:
                    generatableComps.append((compKey))
                else:
                    for dep in deps:
                        # Only append non-None dependencies
                        if dep is not None:
                            newComps.append(dep)

            if len(newComps) == 0:
                break

            comps = newComps

        return set(generatableComps)

    def newDataEntity(self, *args, **kwargs):
        de = DataEntity(self, *args, **kwargs)
        return de


class EnergyPredictionData(DataType):
    modelDependent = True
    datasetDependent = True
    key = "energy"
    iterable = True
    atomConstant = True

    def __init__(self, *args):
        super().__init__(*args)

    def data(self, dataset=None, model=None, taskID=None):
        service = self.service

        if model.singlePredict:
            (e, f) = model.predict(dataset, taskID=taskID)
            fData = self.newDataEntity(forces=f)
            service.setData(fData, "forces", model=model, dataset=dataset)

        else:
            e = model.predictE(dataset, taskID=taskID)

        eData = self.newDataEntity(energy=e)
        service.setData(eData, "energy", model=model, dataset=dataset)

        return True


class ForcesPredictionData(DataType):
    modelDependent = True
    datasetDependent = True
    key = "forces"
    iterable = True
    atomFilterable = True

    def __init__(self, *args):
        super().__init__(*args)

    def data(self, dataset=None, model=None, taskID=None):
        service = self.service

        if model.singlePredict:
            (e, f) = model.predict(dataset, taskID=taskID)
            eData = self.newDataEntity(energy=e)
            service.setData(eData, "energy", model=model, dataset=dataset)

        else:
            f = model.predictF(dataset, taskID=taskID)

        fData = self.newDataEntity(forces=f)
        service.setData(fData, "forces", model=model, dataset=dataset)

        return True


class AtomsList(UserList):
    """
    AtomsList class acts as an intermediate cache for large datasets. Just like how CPUs use cache to access larger data
    in smaller space with more speed, this data structure aims to hold large datasets without having to load the
    complete dataset in memory all at once (RAM).
    It holds a cache size of 100_000 atoms (by default) and loads data into that cache. When a user wants to access an
    atom with index i in a specific dataset, if i exists in cache, then the object will be returned, otherwise, a chunk
    of 100_000 (by default) atoms will be read from the source dataset (containing the requested index at first place)
    and the requested atom will then be returned. The new chunk will replace the previous one obviously.

    This way we can work with large datasets without occupying several Gigabytes of RAM.

    It can require more computational time in some occasions, like when we encounter a cash miss (atom i is not inside
    cache), but it surpasses the heavy memory read at the beginning of each dataset load, and uses the memory much more
    efficiently.

    Args:
        path (str): the path to the designated dataset.
        atoms_chunk (int): cache size (in terms of number of atoms).
    """
    def __init__(self, path, atoms_chunk=300_000):
        super().__init__()
        self.offset = atoms_chunk
        self.start_index = 0
        self.path = path
        self.data = []
        # First we need to calculate the length of the dataset without reading it completely.

        self.N = self.calc_dataset_length()

        # Then, we need to load the first chunk of data.

        self.load_new_chunk(0)

    def calc_dataset_length(self):
        """
        Calculating the number of atoms within a dataset, without loading the entire dataset. This algorithm first
        reads the dataset with skip steps of N (depending on the size of the source file). The length of the
        resulting array would be: A = (dataset_length//N)
        We read one more time starting from index A*N until the end of the dataset (no skipping this time). The length
        of the resulting array would be: B = (dataset_length%N)

        We know that: X = N*(X//N) + X%N

        Therefore, all we have to do to get the correct length of the whole dataset is to sum A and B.

        :return: length of the dataset.
        """
        size_gb = os.path.getsize(self.path) // 1_000_000_000
        slice_size = 0
        if size_gb < 1:
            logger.info(f'Small dataset identified, no need for caching mechanism.')
            slice_size = 5 # debug purposes
        if 1 <= size_gb <= 5:  # change it so that the users choose the slice num
            slice_size = 10  # read and then skip 10 atoms.
            logger.info(f"Moderate size file (1 to 5 GB), setting the slice size to {slice_size}")
        elif 5 < size_gb <= 10:
            slice_size = 100  # skip 100
            logger.info(f"Big file (5 to 10 GB), setting the slice size to {slice_size}")
        elif size_gb > 10:
            slice_size = 1000
            logger.info(f"Gigantic file (10 to inf GB), setting the slice size to {slice_size}")

        atoms = ase.io.read(self.path, index=slice(0, None, slice_size))
        number_of_slice_chunks = len(atoms)

        if number_of_slice_chunks == 0:
            logger.error(f"Dataset ({self.path}) has no entries!!!")
            return 0

        remaining_atoms = ase.io.read(self.path, index=slice(slice_size*(number_of_slice_chunks-1), None))
        dataset_size = slice_size*(number_of_slice_chunks-1) + len(remaining_atoms)
        del atoms, remaining_atoms
        return dataset_size

    @staticmethod
    def calc_dataset_length_static(path):
        """
        This method gets the path to a dataset and returns its length without reading the entire dataset.
        For detailed explanation see the documentation for AtomsList.calc_dataset_length(self, path)...
        :param path: Path to the dataset.
        :return: length of the dataset.
        """
        size_gb = os.path.getsize(path) // 1_000_000_000
        slice_size = 0
        if size_gb < 1:
            logger.info(f'Small dataset identified, no need for caching mechanism.')
            slice_size = 5  # debug purposes
        if 1 <= size_gb <= 5:  # change it so that the users choose the slice num
            slice_size = 10  # read and then skip 10 atoms.
            logger.info(f"Moderate size file (1 to 5 GB), setting the slice size to {slice_size}")
        elif 5 < size_gb <= 10:
            slice_size = 100  # skip 100
            logger.info(f"Big file (5 to 10 GB), setting the slice size to {slice_size}")
        elif size_gb > 10:
            slice_size = 1000
            logger.info(f"Gigantic file (10 to inf GB), setting the slice size to {slice_size}")

        atoms = ase.io.read(path, index=slice(0, None, slice_size))
        number_of_slice_chunks = len(atoms)

        if number_of_slice_chunks == 0:
            logger.error(f"Dataset ({path}) has no entries!!!")
            return 0

        remaining_atoms = ase.io.read(path, index=slice(slice_size * (number_of_slice_chunks - 1), None))
        dataset_size = slice_size * (number_of_slice_chunks - 1) + len(remaining_atoms)
        del atoms, remaining_atoms
        return dataset_size

    def load_new_chunk(self, start):
        """
        Loads a new chunk from source dataset.
        :param start: starting index in the source dataset.
        :return: dataset[start: start+chunk]
        """
        self.start_index = start
        del self.data
        self.data = ase.io.read(self.path, index=slice(self.start_index, self.start_index+self.offset))

    def __len__(self):
        return self.N

    def __getitem__(self, item):
        """
        This is the function which is called when you call the [] operator on an array.
        This method first checks whether self.start_index <= item < self.start_index + self.offset, if the condition
        holds, the required atom is inside the cache, so the method just needs to return cache[item-start_index].
        Otherwise, the methods loads a new chunk (dataset[item: item+chunk]) and replaces it with the old chunk then
        returns the required item.

        If item is a slice, the method checks whether the cache has all of the indices included in the slice. If it has,
        it returns them easily. Otherwise, it loads a chunk (starting from the beginning of the slice), extracts
        the slice indices and moves on until all of the slice indices are fully loaded.

        :param item (int | slice): the index of the required atom in the source dataset.
        :return: dataset[item]
        """
        if isinstance(item, slice):
            logger.warning("slice version of __getitem__ may need more optimization")
            start, stop, step = item.indices(self.N)

            if (self.start_index <= start) and (stop < self.start_index + self.offset):
                return self.data[start - self.start_index: stop - self.start_index: step]

            result = []
            new_offset = (step - (self.offset % step)) + self.offset if self.offset % step != 0 else self.offset
            while start < stop:
                self.load_new_chunk(start)
                if start + new_offset > stop:  # Indicating it's the last chunk
                    result.extend(self.data[:stop-start:step])
                else:
                    result.extend(self.data[::step])
                start += new_offset
            return result
        else:
            item = item if item >= 0 else item + self.N

            if item >= self.N or item < 0:
                raise IndexError(f"Index {item} out of range {self.N}")

            if not (self.start_index <= item < self.start_index + self.offset):
                self.load_new_chunk(item)

            return self.data[item-self.start_index]

    def __iter__(self):
        """
        The method which is called whenever the user uses the "for each" operation.
        for atom in AtomsList('path/to/dataset'):
            # some process to do
        :return: Yields an atom object
        """
        idx = 0
        while idx < self.N:
            yield self.__getitem__(idx)
            idx += 1

    def __contains__(self, item):
        """
        Called when user uses "in" operator.

        if x in atoms:
            # some process to do

        Not implemented for this application, because it had no usage. It also requires a whole dataset search which
        makes our caching system inefficient.
        
        :param item: The queried atom.
        :return: True or False.
        """
        raise Exception("__contains__() is not yet implemented for class AtomsList...")
