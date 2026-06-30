"""Data coordinator for the Environment (ADR 0020).

``DataService`` is the one place allowed to know about the cache, the model and
dataset registries, and the datatype registry all at once.  It owns:

* the datatype registry (built-in + externally registered ``DataType`` objects);
* cache-key resolution (``cacheKeyToComponents``, ``getCacheKey``) and the
  subdataset/atom-filter fallback in ``getData`` / ``hasData``;
* the data-generation queue (``taskGenerateData`` … ``handleGenerationQueue``);
* the in-process metric spine (``registerMetricRequest`` … ``generateMetric``).

Dependencies are injected (cache, model registry, dataset registry, task
manager, event bus).  Method bodies are kept verbatim from the former
``Environment`` methods; the same-named delegators below (``cache``, ``tm``,
``eventPush``, ``newTask``, ``getModel``/``getDataset``/``getObject``) let those
bodies stay unchanged.

There is no back-reference to the owning ``Environment``: the prediction-source
path in ``generateMetric`` goes through an injected ``PredictionSource``, and
``DataType`` instances are constructed with *this* service (the only data-layer
surface they use) rather than the full env.
"""

import logging

logger = logging.getLogger("FFAST")


class DataService:
    """Cache-key resolution, data generation, and in-process metrics (ADR 0020)."""

    def __init__(self, cache, models, datasets, tm, events,
                 source=None, headless=True):
        self.cache = cache
        self._models = models
        self._datasets = datasets
        self.tm = tm
        self._events = events
        self.headless = headless

        # Where metric results / prediction arrays come from (ADR 0020).
        from client.prediction_source import InProcessSource
        self._source = source if source is not None else InProcessSource()

        self.dataTypes = {}
        self.generationQueue = set()
        self.queuedTasks = set()

        # Client-side metric generation spine (ADR 0019): plot panels register
        # metric dependencies via registerMetricRequest; missing keys are computed
        # in-process by generateMetric and cached alongside DataType results.
        self._metricRequests = {}   # cache_key -> (metric_id, params, model, dataset)
        self._inputResolver = None
        self._metricExecutor = None

        # Prediction Dataset Fields (ADR 0023): eagerly extracted at prediction
        # load (the prediction's ASE source is otherwise discarded). Keyed by
        # (modelKey, dataset_fp) — modelKey equals the GhostModel fingerprint, so
        # the resolver finds it from the model object at metric time.
        # {(model_fp, dataset_fp): {"info": {key: arr}, "atoms": {key: arr}}}
        self.predictionFields = {}

        self.initialiseDataTypes()

    # ── delegators: keep the moved method bodies verbatim ─────────────────
    def eventPush(self, *args, **kwargs):
        return self._events.eventPush(*args, **kwargs)

    def newTask(self, *args, **kwargs):
        return self.tm.queueTask(*args, **kwargs)

    def getModel(self, key):
        return self._models.get(key, None)

    def getDataset(self, key):
        return self._datasets.get(key, None)

    def getModelOrDataset(self, key):
        model = self.getModel(key)
        return model if model is not None else self.getDataset(key)

    def getObject(self, *args):
        return self.getModelOrDataset(*args)

    # ── datatypes ─────────────────────────────────────────────────────────
    def initialiseDataTypes(self):
        """Register the built-in prediction data types that other modules depend on."""
        from client.dataType import EnergyPredictionData, ForcesPredictionData

        self.registerDataType(EnergyPredictionData)
        self.registerDataType(ForcesPredictionData)

    def hasDataType(self, dataTypeKey):
        """Provide a cheap existence check before code asks for a data type."""
        return dataTypeKey in self.dataTypes

    def getDataType(self, dataTypeKey):
        """Resolve the live data-type instance used for generation and dependency checks."""
        return self.dataTypes.get(dataTypeKey, None)

    def registerDataType(self, dataType):
        """Add a new data type to the known data types.

        DataType instances are constructed with *this* DataService — the data
        coordinator they call (setData/getCacheKey/hasData/cacheKeyToComponents)
        — rather than the owning Environment.  DataTypes never need anything on
        the Environment outside the data layer.

        Args:
            dataType (class): DataType class (not object!).
        """
        self.dataTypes[dataType.key] = dataType(self)

    def getRegisteredDataType(self, dataTypeKey):
        """Keep the older accessor name for callers that still use it."""
        return self.dataTypes.get(dataTypeKey, None)

    # ── data access ───────────────────────────────────────────────────────
    def getData(self, dataTypeKey, model=None, dataset=None):
        """Serve cached data, including derived subdataset and atom-filtered views."""
        dataType = self.getRegisteredDataType(dataTypeKey)

        if dataType is None:
            logger.error(
                f"Tried to get data for dataTypeKey {dataTypeKey}, "
                + "but no such key was registered"
            )
            return None

        cacheKey = dataType.getCacheKey(model=model, dataset=dataset)

        if type(model) == str:
            obj = self.getObject(model)
            if obj is None:
                logger.error(
                    f"In env.getData, tried to get model for key {model} but no model found"
                )
            model = obj

        if type(dataset) == str:
            obj = self.getObject(dataset)
            if obj is None:
                logger.error(
                    f"In env.getData, tried to get dataset for key {dataset} but no dataset found"
                )
            dataset = obj

        ## SUBDATSETS
        if (
                (dataset is not None)
                and (dataset.isSubDataset)
                and not self.hasCacheKey(cacheKey, subChecks=False)
        ):
            ## ATOM FILTERED
            if dataset.isAtomFiltered:
                if dataType.atomFilterable:
                    data = self.getData(
                        dataTypeKey, model=model, dataset=dataset.parent
                    )
                    if data is not None:
                        return data.getAtomFilteredEntity(
                            indices=dataset.indices
                        )

                if dataType.atomConstant:
                    return self.getData(
                        dataTypeKey, model=model, dataset=dataset.parent
                    )

            elif dataType.iterable:
                data = self.getData(
                    dataTypeKey, model=model, dataset=dataset.parent
                )
                if data is not None:
                    return data.getSubEntity(indices=dataset.indices)

        return self.cache.get(cacheKey, None)

    def setData(self, dataEntity, dataTypeKey, model=None, dataset=None):
        """Store generated data in the cache and notify subscribers that it changed."""
        dataType = self.getRegisteredDataType(dataTypeKey)

        if dataType is None:
            logger.error(
                f"Tried to set data for dataTypeKey {dataTypeKey}, "
                + "but no such key was registered"
            )
            return None

        cacheKey = dataType.getCacheKey(model=model, dataset=dataset)

        self.cache[cacheKey] = dataEntity
        logger.info(f"Data for key {cacheKey} set, {self.cache[cacheKey]}")
        self.eventPush("DATA_UPDATED", cacheKey)

    def make_metric_cache_key(self, metric_id, params, model, dataset):
        """Build a Cache Key for a metric result (CONTEXT.md "Cache Key").

        Non-empty Compute Parameters are folded into the *identity* token
        (``metric_id__p<hash>``), mirroring the Transform Metric compiler, so
        every key is exactly ``identity__model__dataset`` and right-anchored
        decoding stays sound. With no params the string is byte-identical to the
        legacy 3-part DataType format, so in-session lookups remain compatible.
        """
        from ffast.cache import CacheKey

        identity = metric_id
        if params:
            import hashlib, json
            params_hash = hashlib.md5(
                json.dumps(params, sort_keys=True).encode()
            ).hexdigest()[:8]
            identity = f"{metric_id}__p{params_hash}"

        model_fp = model.fingerprint if model is not None else None
        dataset_fp = dataset.fingerprint if dataset is not None else None
        return CacheKey(identity, model_fp, dataset_fp).format()

    @property
    def inputResolver(self):
        """Lazily-built resolver mapping metric input refs to dataset arrays."""
        if self._inputResolver is None:
            from client.inputResolver import InputResolver
            self._inputResolver = InputResolver(self)
        return self._inputResolver

    @property
    def metricExecutor(self):
        """Lazily-built in-process metric executor over the built-in registry."""
        if self._metricExecutor is None:
            import ffast.metrics.builtin  # noqa: F401 — register built-in metrics
            from ffast.metrics.executor import InProcessExecutor
            from ffast.metrics.registry import default_registry
            self._metricExecutor = InProcessExecutor(default_registry)
        return self._metricExecutor

    def registerMetricRequest(self, metric_id, params, model, dataset):
        """Record a plot's metric dependency and return its cache key (ADR 0019).

        The key is returned even when the metric isn't cached yet; dataWatcher
        adds missing keys to the generation queue, where handleGenerationQueue
        routes metric requests to generateMetric.
        """
        key = self.make_metric_cache_key(metric_id, params, model, dataset)
        self._metricRequests[key] = (metric_id, params or {}, model, dataset)
        return key

    def taskGenerateMetric(self, metric_id, params, model, dataset, key):
        """Queue in-process computation of one metric (mirrors taskGenerateData)."""
        if key in self.cache or key in self.queuedTasks:
            return
        self.queuedTasks.add(key)
        self.newTask(
            self.generateMetric,
            args=(metric_id, params, model, dataset),
            kwargs={"key": key},
            threaded=True,
            visual=True,
            name=f"Computing {metric_id}",
            taskKey=key,
        )

    def generateMetric(self, metric_id, params, model, dataset, key=None, taskID=None):
        """Compute one metric in-process, cache the MetricResult, fire DATA_UPDATED.

        Returns True on success.  If a required model prediction is not cached
        yet, queues the prediction DataType(s) and returns False; the metric is
        recomputed the next time its dependencies are refreshed (real-model
        on-demand prediction retry is wired in a later milestone — ghost/remote
        predictions are already transferred, so they compute immediately).
        """
        from ffast.metrics.models import MetricResult

        if key is None:
            key = self.make_metric_cache_key(metric_id, params, model, dataset)

        # 4a server-owned metrics: prefer server-side computation (the server
        # holds the full arrays + ghost/remote predictions and runs the same
        # metric code).  Falls through to in-process compute when there is no
        # server or the server can't source it (e.g. a real client-only model).
        if self._source.available:
            if self._source.fetch_metric_result(metric_id, params, model, dataset, key):
                return True

        # Resolve required predictions before computing.
        missing = self.inputResolver.missing_prediction_keys(
            metric_id, model=model, dataset=dataset
        )
        if missing:
            # Proxy/ghost models can't predict on the client (Stage 2): ask the
            # server to generate + transfer the predictions synchronously (we're
            # in a worker thread), then compute the metric in this same task.
            if (model is not None and getattr(model, "isGhost", False)
                    and self._source.available):
                # A SubDataset (interactive subbing) is client-side only — the
                # server doesn't know its fingerprint and can't serve predictions
                # for it. Its predictions ARE the parent's sliced by sub indices,
                # and getData() already does that slicing once the parent's
                # predictions are cached. So fetch the *root* real dataset's
                # predictions (which the server holds); the sub-slice in getData
                # then satisfies missing_prediction_keys below.
                pred_dataset = dataset
                while (getattr(pred_dataset, "isSubDataset", False)
                       and getattr(pred_dataset, "parent", None) is not None):
                    pred_dataset = pred_dataset.parent
                if not self._source.fetch_prediction_arrays(
                    pred_dataset.fingerprint, model.fingerprint
                ):
                    return False
                missing = self.inputResolver.missing_prediction_keys(
                    metric_id, model=model, dataset=dataset
                )
                if missing:
                    logger.warning(
                        "Metric %s: predictions still missing after server "
                        "fetch: %s", metric_id, missing,
                    )
                    return False
                # fall through to compute
            else:
                # Real client-loaded model (no server): predict in-process.
                for dt_key in missing:
                    self.taskGenerateData(
                        dt_key, model=model, dataset=dataset,
                        visual=True, threaded=True,
                    )
                logger.info(
                    "Metric %s deferred: missing predictions %s",
                    metric_id, missing,
                )
                return False

        inputs = self.inputResolver.build_metric_inputs(
            metric_id, model=model, dataset=dataset
        )
        result = self.metricExecutor.run(metric_id, inputs, params or {})
        if isinstance(result, MetricResult):
            self.cache[key] = result
            self.eventPush("DATA_UPDATED", key)
            logger.info("Metric %s computed and cached (%s)", metric_id, key)
            return True

        logger.error("Metric %s failed: %r", metric_id, result)
        return False

    def getCacheKey(self, dataTypeKey, model=None, dataset=None):
        """Build the canonical cache key for one datatype/model/dataset triple."""
        dataType = self.getRegisteredDataType(dataTypeKey)
        if dataType is None:
            return None

        cacheKey = dataType.getCacheKey(model=model, dataset=dataset)

        return cacheKey

    def hasCacheKey(self, key, subChecks=True):
        """Check whether a cache key is available, optionally honoring subdataset fallbacks."""
        if key is None:
            logger.error("Called env.data.hasCacheKey(key) but key was None!")
            return False
        # Direct cache hit is authoritative (covers in-process metric results,
        # which have no DataType backing for the sub-fallback path below).
        if key in self.cache:
            return True
        if key in self._metricRequests:
            # Registered metric, not yet cached → genuinely missing.  Skip the
            # DataType sub-fallback: a metric id has no registered DataType.
            return False
        if subChecks:
            (dataTypeKey, model, dataset) = self.cacheKeyToComponents(key)
            return self.hasData(dataTypeKey, model=model, dataset=dataset)
        else:
            return key in self.cache

    def hasData(self, dataTypeKey, model=None, dataset=None):
        """Answer whether data exists, including inherited subdataset cases."""
        cacheKey = self.getCacheKey(dataTypeKey, model=model, dataset=dataset)
        hasKey = self.hasCacheKey(cacheKey, subChecks=False)

        if hasKey:
            return True

        if (dataset is not None) and (dataset.isSubDataset):
            dataType = self.getDataType(dataTypeKey)

            if dataset.isAtomFiltered:
                if dataType.atomFilterable or dataType.atomConstant:
                    return self.hasData(
                        dataTypeKey, model=model, dataset=dataset.parent
                    )

            elif dataType.iterable:
                return self.hasData(
                    dataTypeKey, model=model, dataset=dataset.parent
                )

        return False

    # ── data generation ───────────────────────────────────────────────────
    def taskGenerateDataByKey(self, key, **kwargs):
        """Schedule data generation when the caller already has a full cache key."""
        (dataTypeKey, model, dataset) = self.cacheKeyToComponents(key)
        self.taskGenerateData(
            dataTypeKey, model=model, dataset=dataset, **kwargs
        )

    def taskGenerateData(
            self,
            dataTypeKey,
            model=None,
            dataset=None,
            threaded=True,
            visual=False,
            isComponent=False,
            componentParent=None,
    ):
        """Deduplicate and queue one data-generation request."""
        # for models that predict energies and forces at the same time (e.g. sGDML)
        # convert force tasks to energy tasks to avoid duplicates
        if (
                (model is not None)
                and (model.singlePredict)
                and (dataTypeKey == "forces")
        ):
            dataTypeKey = "energy"

        dataKey = self.getCacheKey(dataTypeKey, model=model, dataset=dataset)

        if self.hasCacheKey(dataKey):
            return

        if dataKey in self.queuedTasks:
            # even if the job is not running, it's possible it was generated already
            # in that case, don't
            return

        self.queuedTasks.add(dataKey)

        func = (threaded and self.generateData) or self.generateDataAsync
        self.newTask(
            func,
            args=(dataTypeKey,),
            kwargs={
                "model": model,
                "dataset": dataset,
                "isComponent": isComponent,
            },
            threaded=threaded,
            visual=visual,
            name=f"Generating {dataTypeKey}",
            taskKey=f"{dataKey}",
            componentParent=componentParent,
        )

    async def generateDataAsync(self, *args, **kwargs):
        """Provide an awaitable adapter for synchronous generation code."""
        self.generateData(*args, **kwargs)

    def canGenerateData(self, dataTypeKey, model=None, dataset=None):
        """Ask the data type whether all dependencies are already satisfied."""
        dataType = self.getDataType(dataTypeKey)
        (deps, canGenerate) = dataType.checkDependencies(
            model=model, dataset=dataset
        )

        return canGenerate

    def generateData(
            self,
            dataTypeKey,
            model=None,
            dataset=None,
            isComponent=False,
            taskID=None,
    ):
        """Attempt one generation step and defer unresolved work to the dependency queue."""
        dataType = self.getDataType(dataTypeKey)

        if dataType is None:
            logger.error(
                f"Tried to generate data for dataTypeKey {dataTypeKey}, "
                + "but no such key was registered"
            )
            return None

        cacheKey = dataType.getCacheKey(model=model, dataset=dataset)

        sModel, sDataset = "None", "None"
        if model is not None:
            sModel = model.getDisplayName()
        if dataset is not None:
            sDataset = dataset.getDisplayName()
        logger.info(
            f"Generating data for key {cacheKey}, model = {sModel}, dataset = {sDataset}"
        )

        generated = dataType.generateData(
            model=model, dataset=dataset, taskID=taskID
        )

        if (taskID is not None) and (not self.tm.isTaskRunning(taskID)):
            # check if the task was cancelled, in which case it's normal it
            # failed to generate, thus skip the generation queue
            # in principle this should be unnecessary since cancelling means
            # this function is no longer directly awaited, but better safe
            # than sorry
            return

        if (not generated) and (not isComponent):
            if cacheKey is None:
                # A None key would crash handleGenerationQueue (key.split). Never
                # enqueue it; log with context so the root (a DataType.getCacheKey
                # returning None) is traceable from server.log.
                logger.error(
                    "generateData: getCacheKey returned None (dataType=%r model=%s "
                    "dataset=%s) — not enqueuing",
                    dataTypeKey, sModel, sDataset,
                )
            else:
                self.generationQueue.add(cacheKey)
                logger.info(f"Added {cacheKey} to generation queue")
                self.eventPush("GENERATION_QUEUE_CHANGED")

    def keyIsHaunted(self, dataTypeKey, model=None, dataset=None):
        """Detect requests that can be satisfied from ghost-model cache instead of a real model."""
        if (model is not None) and (not model.isGhost):
            return False

        compKeys = self.getLowestComponents(
            dataTypeKey, model=model, dataset=dataset
        )

        for key in compKeys:
            (dataTypeKey, _, _) = self.cacheKeyToComponents(key)
            if (dataTypeKey == "energy") or (dataTypeKey == "forces"):
                return True

        return False

    def addToGenerationQueue(self, key, dataset=None, model=None):
        """Record a high-level request for later dependency-driven generation."""
        dataType = self.getDataType(key)
        cacheKey = dataType.getCacheKey(model=model, dataset=dataset)
        self.generationQueue.add(cacheKey)
        if self.headless:
            print(f"Added {cacheKey} to generation queue", flush=True)

    async def handleGenerationQueue(self, *args):
        """Expand queued requests into the lowest runnable dependency tasks."""
        queue = self.generationQueue

        if len(queue) == 0:
            return

        logger.info(f"Handling generation queue {self.generationQueue}")

        # copying because we discard in loop
        from ffast.cache import CacheKey
        keysToGenerate = {}
        for cacheKey in queue.copy():
            # Defensive: a None / malformed key must never crash the queue drain.
            # On the server this loop runs in headlessEventLoop, so an unhandled
            # error here would tear down the whole server (clients see
            # "no close frame"). Drop and log instead. try_parse returns None for
            # non-str / too-few-segment keys (CONTEXT.md "Cache Key").
            if CacheKey.try_parse(cacheKey) is None:
                logger.error(
                    "handleGenerationQueue: dropping malformed queue key %r", cacheKey
                )
                queue.discard(cacheKey)
                continue
            # Metric requests are computed in-process (generateMetric), not via
            # the DataType dependency path below.
            if cacheKey in self._metricRequests:
                queue.discard(cacheKey)
                if cacheKey not in self.cache:
                    metric_id, params, model, dataset = self._metricRequests[cacheKey]
                    self.taskGenerateMetric(metric_id, params, model, dataset, cacheKey)
                continue

            (dataTypeKey, model, dataset) = self.cacheKeyToComponents(cacheKey)

            if ("cluster" in cacheKey) and hasattr(dataset, 'isVariable') and dataset.isVariable:
                logger.info("The cluster errors feature is not supported for variable datasets")
                queue.discard(cacheKey)
                self.eventPush('CLUSTER_FOR_VARIABLE')
                continue

            if self.hasCacheKey(cacheKey):
                queue.discard(cacheKey)
                continue

            if self.canGenerateData(dataTypeKey, model=model, dataset=dataset):
                keysToGenerate[
                    cacheKey
                ] = None  # value is the parent key, if available
                queue.discard(cacheKey)

            elif self.keyIsHaunted(dataTypeKey, model=model, dataset=dataset):
                keysToGenerate[cacheKey] = None
                queue.discard(cacheKey)

            else:
                compKeys = self.getLowestComponents(
                    dataTypeKey, model=model, dataset=dataset
                )

                for key in compKeys:
                    if key not in keysToGenerate:
                        keysToGenerate[
                            key
                        ] = cacheKey  # indicates the parent key

        for key, parentKey in keysToGenerate.items():
            (dataTypeKey, model, dataset) = self.cacheKeyToComponents(key)

            self.taskGenerateData(
                dataTypeKey,
                model=model,
                dataset=dataset,
                visual=True,
                threaded=True,
                isComponent=parentKey is not None,
                componentParent=parentKey,
            )

    def getLowestComponents(self, dataTypeKey, model=None, dataset=None):
        """Ask the data type for the deepest currently generatable dependency set."""
        dataType = self.getDataType(dataTypeKey)
        compKeys = dataType.getGeneratableComponent(
            model=model, dataset=dataset
        )

        return compKeys

    def deleteCacheByDataset(self, datasetKey):
        """Invalidate cached outputs when a dataset's membership changes."""
        for key in self.cache.invalidate(lambda k: datasetKey in k):
            self.eventPush("DATA_UPDATED", key)

    def getCacheByKey(self, key, subChecks=True):
        """Resolve a cache key directly, with optional subdataset-aware lookup."""
        if subChecks:
            (dataTypeKey, model, dataset) = self.cacheKeyToComponents(key)
            return self.getData(dataTypeKey, model=model, dataset=dataset)
        else:
            return self.cache.get(key, None)

    def cacheKeyToComponents(self, key, dataTypeObject=False):
        """Decode a Cache Key into datatype, model, and dataset references.

        Right-anchored via ``CacheKey.parse`` (CONTEXT.md "Cache Key"): the model
        and dataset are the last two segments, so an identity token containing
        ``__`` (a Transform Metric) no longer mis-decodes into them.
        """
        from ffast.cache import CacheKey

        ck = CacheKey.parse(key)
        dataType = self.getDataType(ck.dtype) if dataTypeObject else ck.dtype
        model = self.getModel(ck.model_fp) if ck.model_fp is not None else None
        dataset = self.getDataset(ck.dataset_fp) if ck.dataset_fp is not None else None
        return (dataType, model, dataset)
