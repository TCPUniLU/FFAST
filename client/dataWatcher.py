from events import EventChildClass
import logging

logger = logging.getLogger("FFAST")


class DataWatcher(EventChildClass):
    """
    They're always watching.
    """

    def __init__(self, env):
        super().__init__()
        self.env = env
        self.env.addEventChild(self)

        self.eventSubscribe("DATA_UPDATED", self.onDataUpdated)
        self.eventSubscribe("DATASET_LOADED", self.refreshDependencyList)
        self.eventSubscribe("MODEL_LOADED", self.refreshDependencyList)
        self.eventSubscribe("DATASET_UPDATED", self.onDatasetUpdated)
        self.eventSubscribe(
            "DATASET_STATE_CHANGED", self.onDatasetStateChanged
        )
        self.eventSubscribe("DATASET_DELETED", self.refreshDependencyList)
        self.eventSubscribe("MODEL_DELETED", self.refreshDependencyList)

        self.dataTypeDependencies = []
        self.metricDependencies = {}  # {metric_id: params_dict}
        self._metricRequiresModel = False  # True if any metric uses prediction.* inputs
        self.datasetDependencies = []
        self.modelDependencies = []
        self.dependencyList = (
            []
        )  # list of datakeys (data__model__dataset) its needs
        self.refreshList = (
            []
        )  # additional list of datakeys that refresh it (because of subdatasets)
        self.currentlyMissingKeys = []
        self.refreshWidgets = []
        self.callbacks = []

    allDatasets = False
    allModels = False
    parentName = None
    autocomputePriority = False
    # updateKey = 0

    # def getUpdateKey(self):
    #     return self.updateKey

    def setMetricDependencies(self, metric_deps: dict):
        """Declare metric dependencies: {metric_id: params_dict}.

        Replaces setDataDependencies for plots that consume MetricResults directly.
        Plot widgets receive one entry per (model, dataset) pair in getWatchedData(),
        with dataEntry = {metric_id: MetricResult, ...} (ADR 0019, D5/A1).

        Model-independent metrics (inputs only reference.* / selection.*) produce one
        entry per dataset with model=None, so they work without any model loaded.
        """
        from ffast.metrics.registry import default_registry
        from ffast.metrics.input_resolver import metric_needs_prediction
        self.metricDependencies = {}
        requires_model = False
        for metric_id, params in metric_deps.items():
            if not default_registry.has(metric_id):
                logger.error("setMetricDependencies: unknown metric '%s'", metric_id)
                continue
            self.metricDependencies[metric_id] = params
            # Transitive: a metric whose direct inputs are only other metrics
            # (e.g. energy_mae → energy_difference) still needs a prediction at
            # the leaves, so it is model-dependent.
            if metric_needs_prediction(metric_id, default_registry):
                requires_model = True
        self._metricRequiresModel = requires_model
        self.refreshDependencyList()

    def setDataDependencies(self, *args):
        self.dataTypeDependencies = []
        env = self.env

        if len(args) == 0:
            self.refreshDependencyList()
            return

        if isinstance(args[0], list):
            args = args[0]

        for key in args:
            if not env.data.hasDataType(key):
                logger.error(
                    f"Tried to set DataWatcher dependency with key `{key}`,"
                    + ", but this key has not been registered."
                )

            self.dataTypeDependencies.append(key)

        self.refreshDependencyList()

    def getDataDependencies(self):
        return self.dataTypeDependencies

    def setModelDependencies(self, *args, quiet=False):
        self.modelDependencies = []

        if len(args) == 0:
            self.refreshDependencyList()
            return

        if len(args) == 1:
            if args[0] == "all":
                self.allDatasets = True
                return
            else:
                self.allDatasets = False

            if isinstance(args[0], list):
                args = args[0]

        for key in args:
            self.modelDependencies.append(key)

        if not quiet:
            self.refreshDependencyList()

    def getModelDependencies(self):
        return self.modelDependencies

    def isDependentOn(self, key):
        return (key in self.getModelDependencies()) or (
            key in self.getDatasetDependencies()
        )

    def setModelDatasetDependencies(self, modelKeys, datasetKeys):
        self.setModelDependencies(*modelKeys, quiet=True)
        self.setDatasetDependencies(*datasetKeys)

    def setDatasetDependencies(self, *args, quiet=False):
        self.datasetDependencies = []

        if len(args) == 0:
            self.refreshDependencyList()
            return

        if len(args) == 1:
            if args[0] == "all":
                self.allDatasets = True
                return
            else:
                self.allDatasets = False

            if isinstance(args[0], list):
                args = args[0]

        for key in args:
            dataset = self.env.datasets.get(key)
            if (dataset is None) or (not dataset.active):
                continue
            self.datasetDependencies.append(key)

        if not quiet:
            self.refreshDependencyList()

    def getDatasetDependencies(self):
        return self.datasetDependencies

    def refreshDatasets(self):
        self.setDatasetDependencies(*self.datasetDependencies.copy())

    def refreshDependencyList(self, *args):
        # args because event

        self.dependencyList = []
        self.refreshList = []
        env = self.env

        has_dt = len(self.dataTypeDependencies) > 0 and self.dataTypeDependencies[0] is not None
        has_metric = bool(self.metricDependencies)

        if not has_dt and not has_metric:
            self.refresh()
            return

        datasetDependencies = (
            self.allDatasets
            and env.datasets.all_keys()
            or self.datasetDependencies
        )
        modelDependencies = (
            self.allModels and env.models.all_keys() or self.modelDependencies
        )

        if has_dt:
            for dataTypeKey in self.dataTypeDependencies:
                dataType = env.data.getRegisteredDataType(dataTypeKey)
                if dataType is None:
                    continue

                mds = modelDependencies
                if not dataType.modelDependent:
                    mds = [None]

                for modelKey in mds:
                    model = env.models.get(modelKey)
                    if (model is None) and dataType.modelDependent:
                        continue

                    dds = datasetDependencies
                    if not dataType.datasetDependent:
                        dds = [None]

                    for datasetKey in dds:
                        dataset = env.datasets.get(datasetKey)

                        if (dataset is None) and dataType.datasetDependent:
                            continue
                        if (
                            dataset.isSubDataset
                            and dataset.isAtomFiltered
                            and (dataType.atomFilterable or dataType.atomConstant)
                        ):
                            key = dataType.getCacheKey(
                                model=model, dataset=dataset.parent
                            )
                            self.refreshList.append(key)

                        key = dataType.getCacheKey(model=model, dataset=dataset)
                        self.dependencyList.append(key)

        if has_metric:
            for metric_id, params in self.metricDependencies.items():
                if not self._metricRequiresModel:
                    for datasetKey in datasetDependencies:
                        dataset = env.datasets.get(datasetKey)
                        if dataset is None or not dataset.active:
                            continue
                        key = env.data.registerMetricRequest(metric_id, params, None, dataset)
                        self.dependencyList.append(key)
                else:
                    for modelKey in modelDependencies:
                        model = env.models.get(modelKey)
                        if model is None:
                            continue
                        for datasetKey in datasetDependencies:
                            dataset = env.datasets.get(datasetKey)
                            if dataset is None or not dataset.active:
                                continue
                            key = env.data.registerMetricRequest(metric_id, params, model, dataset)
                            self.dependencyList.append(key)

        self.refresh()

    def getMissingDependencies(self):
        missingKeys = []
        env = self.env

        for key in self.dependencyList:
            if not env.data.hasCacheKey(key):
                missingKeys.append(key)

        return missingKeys

    def addRefreshWidget(self, widget):
        self.refreshWidgets.append(widget)

    def refresh(self):
        missingKeys = self.getMissingDependencies()
        self.currentlyMissingKeys = missingKeys

        self.sendRefresh()

    def onDataUpdated(self, cacheKey):
        if (cacheKey not in self.dependencyList) and (
            cacheKey not in self.refreshList
        ):
            return

        self.refresh()

    def onDatasetUpdated(self, key):
        if key not in self.datasetDependencies:
            return

        self.refresh()

    def onDatasetStateChanged(self, key):
        if key not in self.datasetDependencies:
            return

        self.refreshDatasets()

    def sendRefresh(self):
        for widget in self.refreshWidgets:
            self.eventPush("WIDGET_REFRESH", widget)

        for func in self.callbacks:
            func()

    def getWatchedData(self, dataOnly=False):
        if self.metricDependencies:
            return self._getWatchedDataMetric()
        return self._getWatchedDataLegacy(dataOnly=dataOnly)

    def _getWatchedDataLegacy(self, dataOnly=False):
        env = self.env
        allData = []

        for key in self.dependencyList:
            if env.data.hasCacheKey(key):
                if dataOnly:
                    allData.append(env.data.getCacheByKey(key))
                else:
                    (dataTypeKey, model, dataset) = env.data.cacheKeyToComponents(key)
                    dataEntry = env.data.getCacheByKey(key)
                    entry = {
                        "dataTypeKey": dataTypeKey,
                        "model": model,
                        "dataset": dataset,
                        "dataKey": key,
                        "dataEntry": dataEntry,
                    }
                    allData.append(entry)

        return allData

    def _getWatchedDataMetric(self):
        """A1 grouping: one entry per (model, dataset), dataEntry = {metric_id: MetricResult}.

        For model-independent metrics (_metricRequiresModel=False), model is None and
        entries are keyed per dataset only.
        """
        env = self.env

        datasetDeps = (self.allDatasets and env.datasets.all_keys() or self.datasetDependencies)
        modelDeps = (self.allModels and env.models.all_keys() or self.modelDependencies)

        groups = {}  # (model_fp_or_nil, dataset_fp) → entry dict

        if not self._metricRequiresModel:
            for datasetKey in datasetDeps:
                dataset = env.datasets.get(datasetKey)
                if dataset is None or not dataset.active:
                    continue
                gk = ("nil", dataset.fingerprint)
                if gk not in groups:
                    groups[gk] = {"model": None, "dataset": dataset, "dataEntry": {}}
                for metric_id, params in self.metricDependencies.items():
                    cache_key = env.data.make_metric_cache_key(metric_id, params, None, dataset)
                    if env.data.hasCacheKey(cache_key, subChecks=False):
                        groups[gk]["dataEntry"][metric_id] = env.data.getCacheByKey(cache_key, subChecks=False)
        else:
            for modelKey in modelDeps:
                model = env.models.get(modelKey)
                if model is None:
                    continue
                for datasetKey in datasetDeps:
                    dataset = env.datasets.get(datasetKey)
                    if dataset is None or not dataset.active:
                        continue
                    gk = (model.fingerprint, dataset.fingerprint)
                    if gk not in groups:
                        groups[gk] = {"model": model, "dataset": dataset, "dataEntry": {}}
                    for metric_id, params in self.metricDependencies.items():
                        cache_key = env.data.make_metric_cache_key(metric_id, params, model, dataset)
                        if env.data.hasCacheKey(cache_key, subChecks=False):
                            groups[gk]["dataEntry"][metric_id] = env.data.getCacheByKey(cache_key, subChecks=False)

        n_metrics = len(self.metricDependencies)
        return [g for g in groups.values() if len(g["dataEntry"]) == n_metrics]

    def linkSelectionTo(self, dataWatcher):
        # links this dataWatcher to another
        # everytime the other dataWatcher gets updated, updates this one to the same values
        pass

    def loadContent(self):
        env = self.env
        deps = self.getMissingDependencies()
        for dep in deps:
            env.data.generationQueue.add(dep)

    def addCallback(self, func):
        self.callbacks.append(func)
