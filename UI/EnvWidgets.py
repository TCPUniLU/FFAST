import logging
from ffast.core.events import EventChildClass
from UI.Templates.base import ComboBox

logger = logging.getLogger("FFAST")


class ObjectComboBox(ComboBox, EventChildClass):

    updateFunc = None
    selectedKey = None
    currentlyUpdatingList = False

    def __init__(
        self, handler, hasDatasets=True, watcher=None, *args, **kwargs
    ):
        self.handler = handler
        self.env = handler.env
        super().__init__(*args, **kwargs)
        EventChildClass.__init__(self)

        self.hasDatasets = hasDatasets
        self.watcher = watcher

        if self.hasDatasets:
            self.eventSubscribe("DATASET_LOADED", self.updateList)
            self.eventSubscribe("DATASET_DELETED", self.updateList)

        else:
            self.eventSubscribe("MODEL_LOADED", self.updateList)
            self.eventSubscribe("MODEL_DELETED", self.updateList)

        self.eventSubscribe("OBJECT_NAME_CHANGED", self.updateList)

        self.currentKeyList = []

        self.currentIndexChanged.connect(self.onIndexChanged)
        self.updateList()

    def updateList(self, *args):
        self.currentlyUpdatingList = True
        l = []
        if self.hasDatasets:
            l = l + self.env.datasets.all_keys()

        else:
            l = l + self.env.models.all_keys()

        self.currentKeyList = l
        self.updateComboBox()

        # RESELECT PREVIOUS ONE
        if self.selectedKey in self.currentKeyList:
            index = self.currentKeyList.index(self.selectedKey)
            self.setCurrentIndex(index)
            self.currentlyUpdatingList = False

        elif len(self.currentKeyList) > 0:
            self.setCurrentIndex(0)
            self.currentlyUpdatingList = False
            self.forceUpdate()

    def updateComboBox(self, *args):
        self.clear()
        self.addItems(
            [
                self.env.getModelOrDataset(x).getDisplayName()
                for x in self.currentKeyList
            ]
        )

    def setOnIndexChanged(self, func):
        self.updateFunc = func

    def forceUpdate(self):
        self.onIndexChanged(self.currentIndex())

    def updateWatcher(self):
        key = self.getActiveKey()
        if self.hasDatasets:
            self.watcher.setDatasetDependencies(key)
        else:
            self.watcher.setModelDependencies(key)

    def getActiveKey(self):
        index = self.currentIndex()
        if (index < 0) or (index >= len(self.currentKeyList)):
            return None

        return self.currentKeyList[index]

    def onIndexChanged(self, index):
        if self.currentlyUpdatingList:
            return

        key = self.getActiveKey()
        self.selectedKey = key

        if self.watcher is not None:
            self.updateWatcher()

        if self.updateFunc is not None:
            self.updateFunc(key)
