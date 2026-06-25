import numpy as np
from client.dataType import DataType
import logging
from scipy.stats import gaussian_kde
from config.atoms import zIntToZStr, atomColors
from config.userConfig import getConfig

logger = logging.getLogger("FFAST")

DEPENDENCIES = ["basicErrors"]


def loadData(env):
    class AtomicForcesErrorDist(DataType):
        modelDependent = True
        datasetDependent = True
        key = "atomicForcesErrorDist"
        dependencies = ["forcesError"]
        iterable = False

        def __init__(self, *args):
            super().__init__(*args)

        def data(self, dataset=None, model=None, taskID=None):
            env = self.env

            err = env.getData("forcesError", model=model, dataset=dataset)
            z = dataset.getElements()

            diffAll = err.get("diff")
            out = {}

            for i in np.unique(z):
                idxs = np.argwhere(z == i)
                idxs = idxs.flatten()

                # Handle variable vs uniform datasets
                if isinstance(diffAll, list):
                    # Variable dataset: compute per-molecule MAE for atoms of type i
                    mae_list = []
                    for mol_idx in range(len(diffAll)):
                        z_mol = dataset.getElements(mol_idx)
                        mol_idxs = np.argwhere(z_mol == i).flatten()
                        if len(mol_idxs) > 0:
                            mol_diff = diffAll[mol_idx][mol_idxs]  # (n_atoms_of_type_i, 3)
                            mol_mae = np.mean(np.abs(mol_diff))
                            mae_list.append(mol_mae)

                    if len(mae_list) == 0:
                        # No atoms of this type found
                        continue

                    mae = np.array(mae_list)
                else:
                    # Uniform dataset: diffAll is (N, M, 3) array
                    diff = diffAll[:, idxs, :]
                    diff = diff.reshape(diff.shape[0], -1)
                    mae = np.mean(np.abs(diff), axis=1)

                absMae = np.abs(mae)
                nPts = getConfig("plotDistNum")

                if len(absMae) < 2 or np.std(absMae) < 1e-10:
                    distX = np.linspace(
                        0,
                        max(np.max(absMae), 1e-10),
                        nPts,
                    )
                    distY = np.zeros_like(distX)
                    closest_idx = np.argmin(
                        np.abs(distX - np.mean(absMae))
                    )
                    distY[closest_idx] = 1.0
                else:
                    kde = gaussian_kde(absMae)
                    distX = np.linspace(
                        np.min(absMae) * 0.95,
                        np.max(absMae) * 1.05,
                        nPts,
                    )
                    distY = kde(distX)

                out[zIntToZStr[i]] = {"distY": distY, "distX": distX}

            de = self.newDataEntity(**out)
            env.setData(de, self.key, model=model, dataset=dataset)
            return True

    class AtomicForceErrors(DataType):
        modelDependent = True
        datasetDependent = True
        key = "atomicForcesError"
        dependencies = ["forcesError"]
        iterable = False

        def __init__(self, *args):
            super().__init__(*args)

        def data(self, dataset=None, model=None, taskID=None):
            env = self.env

            err = env.getData("forcesError", model=model, dataset=dataset)
            z = dataset.getElements()

            diffAll = err.get("diff")
            out = {}

            for i in np.unique(z):
                idxs = np.argwhere(z == i)
                idxs = idxs.flatten()

                # Handle variable vs uniform datasets
                if isinstance(diffAll, list):
                    # Variable dataset: aggregate all atoms of type i across all molecules
                    diff_list = []
                    for mol_idx in range(len(diffAll)):
                        z_mol = dataset.getElements(mol_idx)
                        mol_idxs = np.argwhere(z_mol == i).flatten()
                        if len(mol_idxs) > 0:
                            diff_list.append(diffAll[mol_idx][mol_idxs].flatten())

                    if len(diff_list) == 0:
                        # No atoms of this type found
                        continue

                    diff = np.concatenate(diff_list)
                    mae = np.mean(np.abs(diff))
                    rmse = np.sqrt(np.mean(diff ** 2))
                else:
                    # Uniform dataset: diffAll is (N, M, 3) array
                    diff = diffAll[:, idxs, :]
                    diff = diff.reshape(diff.shape[0], -1)
                    mae = np.mean(np.abs(diff))
                    rmse = np.sqrt(np.mean(diff ** 2))

                out[zIntToZStr[i]] = {"mae": mae, "rmse": rmse}

            de = self.newDataEntity(**out)
            env.setData(de, self.key, model=model, dataset=dataset)
            return True

    env.registerDataType(AtomicForcesErrorDist)
    env.registerDataType(AtomicForceErrors)


def loadUI(UIHandler, env):
    from UI.ContentTab import ContentTab, DatasetModelSelector, ListCheckButton
    from UI.Plots import BasicPlotWidget, Table
    from UI.Templates import FlexibleListSelector
    from config.atoms import atomColors, zIntToZStr

    tab_name = "Atomic Errors"
    ct = ContentTab(
        UIHandler, tabName=tab_name, hasDataSelector=False
    )  # adding a new one manually
    UIHandler.addContentTab(ct, tab_name)

    class AtomLabel(ListCheckButton):
        def __init__(self, atomIndex, *args, **kwargs):
            color = atomColors[atomIndex]
            name = zIntToZStr[atomIndex]
            self.atomIndex = atomIndex
            self.atomName = name
            super().__init__(*args, color=color, label=name, **kwargs)

    class AtomicDatasetModelSelector(DatasetModelSelector):

        lastSelectedDatasets = set()

        def __init__(self, UIHandler, parent=None, tabName=None):
            super().__init__(UIHandler, parent=parent, tabName=tabName)
            self.atomsList = FlexibleListSelector(
                parent=self, label="Selected elements", elementSize=50
            )
            self.atomsList.setOnUpdate(self.update)
            self.layout.addWidget(self.atomsList)

        def getSelectedAtomIndices(self):
            return [x.atomIndex for x in self.atomsList.getSelectedWidgets()]

        def getSelectedAtomInfo(self):
            l = {}
            idxs = self.getSelectedAtomIndices()
            for i in idxs:
                l[zIntToZStr[i]] = {"index": i, "color": atomColors[i]}

            return l

        def update(self):
            modelKeys, datasetKeys = self.getSelectedKeys()

            datasetKeySet = set(datasetKeys)
            if datasetKeySet != self.lastSelectedDatasets:
                self.lastSelectedDatasets = datasetKeySet
                self.updateAtomsList()

            nModels = len(modelKeys)
            nDatasets = len(datasetKeys)
            nTypes = len(self.getSelectedAtomIndices())

            # single atom unlocks both axes; multiple atoms lock to one pair
            self.atomsList.singleSelection = nModels > 1 or nDatasets > 1
            self.modelsList.singleSelection = nTypes > 1
            self.datasetsList.singleSelection = nTypes > 1

            DatasetModelSelector.update(self)

        def updateAtomsList(self):
            keys = self.lastSelectedDatasets

            self.atomsList.removeWidgets(clear=True)

            elements = set()
            for key in keys:
                dataset = self.handler.env.getDataset(key)
                if dataset is not None:
                    elements |= set(dataset.getElements())

            if len(elements) > 0:
                label = AtomLabel(0, parent=self.atomsList)  # All
                self.atomsList.addWidget(label)
                label.setChecked(True)  # by default All is selected!

            for i in sorted(elements):
                label = AtomLabel(i, parent=self.atomsList)
                self.atomsList.addWidget(label)

    dataselector = AtomicDatasetModelSelector(UIHandler, parent=ct, tabName = tab_name)
    ct.setDataSelector(dataselector)

    class ForcesErrorDistPlot(BasicPlotWidget):
        def __init__(self, handler, **kwargs):
            super().__init__(
                handler,
                title="Forces MAE distribution",
                isSubbable=False,
                name="Force Error Distribution Atomic",
                **kwargs,
            )
            self.setDataDependencies(
                "atomicForcesErrorDist", "forcesErrorDist"
            )
            self.setXLabel("Forces MAE", getConfig("forceUnit"))
            self.setYLabel("Density")
            self.infoButton.setToolTip(
                "Distribution of force MAE (Mean Absolute Error) for the selected atom type across the dataset.\n"
                "MAE is computed per atom over its (x, y, z) force components; the histogram pools values from every occurrence of that atom type in every structure. "
                "Choose \"All\" in the atom selector for the non-atom-resolved distribution."
            )

        def addPlots(self):

            atomTypes = dataselector.getSelectedAtomInfo()
            atomMode = len(atomTypes) > 1
            hasAll = "All" in atomTypes

            for data in self.getWatchedData():
                de = data["dataEntry"]
                dataType = data["dataTypeKey"]

                if dataType == "forcesErrorDist":
                    if not hasAll:
                        continue

                    x, y = de.get("distX"), de.get("distY")
                    if atomMode:
                        self.plot(x, y, color=atomColors[0], autoLabel=data)
                    else:
                        self.plot(x, y, autoColor=data, autoLabel=data)

                else:
                    for atom, info in atomTypes.items():
                        if atom == "All":
                            continue

                        atomDE = de.get(atom)
                        if atomDE is None:
                            continue

                        x, y = atomDE.get("distX"), atomDE.get("distY")

                        if atomMode:
                            self.plot(
                                x, y, color=info["color"], autoLabel=data
                            )
                        else:
                            self.plot(x, y, autoColor=data, autoLabel=data)

    plt = ForcesErrorDistPlot(UIHandler, tabName=tab_name, parent=ct)
    ct.addWidget(plt, 0, 0)
    ct.addDataSelectionCallback(plt.setModelDatasetDependencies)

    class AtomicErrorTable(Table):
        def __init__(self, **kwargs):
            super().__init__(
                UIHandler, parent=ct, title="Atomic Errors", **kwargs
            )
            ct.addDataSelectionCallback(self.setModelDatasetDependencies)
            self.setDataDependencies("atomicForcesError", "forcesErrorMetrics")
            self.eventSubscribe("PlotLoadPushed", self.plotLoadPushed)
            self.dependantPlot = "Force Error Distribution Atomic"

        def plotLoadPushed(self, name):
            if name == self.dependantPlot:
                self.dataWatcher.loadContent()

        def getPairs(self):
            datasets = self.getDatasetDependencies()
            models = self.getModelDependencies()
            pairs = []
            for d in datasets:
                for m in models:
                    has_data = (
                        self.handler.env.getData(
                            "forces", dataset=d, model=m
                        ) is not None
                        or self.handler.env.getData(
                            "energy", dataset=d, model=m
                        ) is not None
                    )
                    if has_data:
                        pairs.append((d, m))
            return pairs

        def getSize(self):
            atomTypes = dataselector.getSelectedAtomInfo()
            nCols = 2
            if len(atomTypes) == 0:
                nRows = 0
            elif len(atomTypes) > 1:
                nRows = len(atomTypes)
            else:
                nRows = len(self.getPairs())
            return (nRows, nCols)

        def getLeftHeader(self, i):
            atomTypes = dataselector.getSelectedAtomInfo()
            if len(atomTypes) > 1:
                return f"{list(atomTypes.keys())[i]}"

            pairs = self.getPairs()
            if i >= len(pairs):
                return "/"
            dataset_key, model_key = pairs[i]
            dataset = self.handler.env.getDataset(dataset_key)
            model = self.handler.env.getModel(model_key)
            ds_name = dataset.getDisplayName() if dataset else dataset_key
            m_name = model.getDisplayName() if model else model_key
            if len(self.getDatasetDependencies()) > 1:
                return f"{ds_name} / {m_name}"
            else:
                return m_name

        def getTopHeader(self, i):
            if i == 0:
                return "MAE"
            elif i == 1:
                return "RMSE"
            else:
                return "/"

        def getValue(self, i, j):
            atomTypes = list(dataselector.getSelectedAtomInfo().keys())
            atomMode = len(atomTypes) > 1
            value = None

            if atomMode:
                datasets = self.getDatasetDependencies()
                models = self.getModelDependencies()
                if not datasets or not models:
                    return
                dataset = datasets[0]
                model = models[0]
                atomType = atomTypes[i]
            else:
                pairs = self.getPairs()
                if i >= len(pairs) or not atomTypes:
                    return
                dataset, model = pairs[i]
                atomType = atomTypes[0]

            if atomType == "All":
                de = self.handler.env.getData(
                    "forcesErrorMetrics", dataset=dataset, model=model
                )
                if de is None:
                    return
                if j == 0:
                    value = de.get("mae")
                else:
                    value = de.get("rmse")

            else:
                de = self.handler.env.getData(
                    "atomicForcesError", dataset=dataset, model=model
                )
                if de is None:
                    return

                atomEntry = de.get(atomType)
                if atomEntry is None:
                    return
                if j == 0:
                    value = atomEntry["mae"]
                else:
                    value = atomEntry["rmse"]

            if value is not None:
                return f"{value:.2f}"

    table = AtomicErrorTable()
    ct.addWidget(table, 1, 0)
