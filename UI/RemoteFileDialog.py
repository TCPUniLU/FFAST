"""Finder-style remote file browser for the cluster filesystem.

The server already exposes ``LIST_DIR`` → ``DIR_LISTING`` (it lists the
filesystem *it* can see — the compute node running the ffast-server, ADR 0028).
This dialog drives that RPC from a lazy ``QAbstractItemModel`` behind a
``QColumnView`` (Qt's Finder-style miller-columns widget). No SSH/SFTP library
is involved: browsing the server's own view guarantees any file the user can
pick is a file the server can actually open.

Usage (from a *synchronous* Qt slot, never inside a running coroutine)::

    dlg = RemoteFileDialog(session, loop, parent=window, title="Load Remote Dataset")
    if dlg.exec():
        path = dlg.selectedPath()
"""
import asyncio
import logging
import os

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QColumnView,
    QDialog,
    QDialogButtonBox,
    QFileIconProvider,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

logger = logging.getLogger("FFAST")

_ICONS = QFileIconProvider()


class _Node:
    __slots__ = ("path", "name", "is_dir", "size", "parent", "row",
                 "children", "fetched", "fetching")

    def __init__(self, path, name, is_dir, size, parent, row):
        self.path = path
        self.name = name
        self.is_dir = is_dir
        self.size = size
        self.parent = parent
        self.row = row
        self.children = []
        self.fetched = False
        self.fetching = False


class RemoteDirModel(QAbstractItemModel):
    """Lazy tree model backed by the server's ``LIST_DIR`` RPC.

    ``hasChildren`` is answered optimistically from the ``is_dir`` flag already
    carried by each entry, so opening a column costs no round-trip; the actual
    listing is fetched only when the view descends into a directory
    (``canFetchMore``/``fetchMore``). Each fetch schedules a ``list_dir``
    coroutine on the asyncio loop and, on reply, splices the children in.
    """

    #: emitted (on the loop thread = GUI thread under qasync) when a listing
    #: arrives, so model mutation happens through Qt's signal machinery.
    _listingArrived = Signal(object, object)
    errorRaised = Signal(str)

    def __init__(self, session, loop, parent=None):
        super().__init__(parent)
        self._session = session
        self._loop = loop
        # Synthetic invisible root; its children are the home dir's entries.
        self._root = _Node(None, "", True, 0, None, 0)
        self._listingArrived.connect(self._applyListing)

    # ── bootstrap ────────────────────────────────────────────────────────
    def start(self):
        """Kick off the initial listing (server home directory)."""
        self._fetchNode(self._root)

    # ── QAbstractItemModel plumbing ──────────────────────────────────────
    def _node(self, index):
        return index.internalPointer() if index.isValid() else self._root

    def index(self, row, column, parent=QModelIndex()):
        node = self._node(parent)
        if row < 0 or row >= len(node.children) or column != 0:
            return QModelIndex()
        return self.createIndex(row, column, node.children[row])

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        node = index.internalPointer()
        p = node.parent
        if p is None or p is self._root:
            return QModelIndex()
        return self.createIndex(p.row, 0, p)

    def rowCount(self, parent=QModelIndex()):
        return len(self._node(parent).children)

    def columnCount(self, parent=QModelIndex()):
        return 1

    def hasChildren(self, parent=QModelIndex()):
        return self._node(parent).is_dir

    def canFetchMore(self, parent):
        node = self._node(parent)
        return node.is_dir and not node.fetched

    def fetchMore(self, parent):
        self._fetchNode(self._node(parent))

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        node = index.internalPointer()
        if role == Qt.DisplayRole:
            return node.name
        if role == Qt.DecorationRole:
            return _ICONS.icon(QFileIconProvider.Folder if node.is_dir
                               else QFileIconProvider.File)
        return None

    # ── async fetch ──────────────────────────────────────────────────────
    def _fetchNode(self, node):
        if node.fetching or node.fetched:
            return
        node.fetching = True

        async def _run():
            try:
                listing = await self._session.list_dir(node.path)
            except Exception as exc:  # network / timeout
                node.fetching = False
                self.errorRaised.emit(str(exc))
                return
            self._listingArrived.emit(node, listing)

        try:
            asyncio.run_coroutine_threadsafe(_run(), self._loop)
        except Exception as exc:
            node.fetching = False
            self.errorRaised.emit(str(exc))

    def _applyListing(self, node, listing):
        node.fetching = False
        if listing.get("error"):
            node.fetched = True
            self.errorRaised.emit(listing["error"])
            return
        base = listing.get("path") or node.path or ""
        if node is self._root and base:
            node.path = base  # remember resolved home
        entries = listing.get("entries") or []
        children = [
            _Node(os.path.join(base, e["name"]), e["name"],
                  bool(e["is_dir"]), int(e.get("size", 0)), node, i)
            for i, e in enumerate(entries)
        ]
        if children:
            parent_index = (QModelIndex() if node is self._root
                            else self.createIndex(node.row, 0, node))
            self.beginInsertRows(parent_index, 0, len(children) - 1)
            node.children = children
            node.fetched = True
            self.endInsertRows()
        else:
            node.children = []
            node.fetched = True


class RemoteFileDialog(QDialog):
    """Modal cluster file picker. Call ``exec()`` from a synchronous slot."""

    def __init__(self, session, loop, parent=None,
                 title="Open Remote File", select_dirs=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 460)
        self._selected = None
        self._select_dirs = select_dirs

        self._model = RemoteDirModel(session, loop, self)
        self._model.errorRaised.connect(self._onError)

        self._view = QColumnView()
        self._view.setModel(self._model)
        self._view.clicked.connect(self._onClicked)
        self._view.doubleClicked.connect(self._onDoubleClicked)

        self._selectedLabel = QLabel("Selected: —")
        self._selectedLabel.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._error = QLabel("")
        self._error.setStyleSheet("color: #c0392b;")
        self._error.setVisible(False)

        # Fallback: paste an absolute path directly (preserves prior behaviour).
        pathRow = QHBoxLayout()
        self._pathEdit = QLineEdit()
        self._pathEdit.setPlaceholderText("…or paste an absolute cluster path")
        goBtn = QPushButton("Use path")
        goBtn.clicked.connect(self._onUsePath)
        pathRow.addWidget(self._pathEdit)
        pathRow.addWidget(goBtn)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.Open | QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        self._openBtn = self._buttons.button(QDialogButtonBox.Open)
        self._openBtn.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.addWidget(self._view)
        layout.addWidget(self._selectedLabel)
        layout.addWidget(self._error)
        layout.addLayout(pathRow)
        layout.addWidget(self._buttons)

        self._model.start()

    # ── selection ────────────────────────────────────────────────────────
    def _acceptable(self, node):
        return node is not None and (node.is_dir if self._select_dirs
                                     else not node.is_dir)

    def _onClicked(self, index):
        node = index.internalPointer() if index.isValid() else None
        if self._acceptable(node):
            self._selected = node.path
            self._selectedLabel.setText(f"Selected: {node.path}")
            self._openBtn.setEnabled(True)
        else:
            self._selected = None
            self._openBtn.setEnabled(False)

    def _onDoubleClicked(self, index):
        node = index.internalPointer() if index.isValid() else None
        if self._acceptable(node):
            self._selected = node.path
            self.accept()

    def _onUsePath(self):
        text = self._pathEdit.text().strip()
        if text:
            self._selected = text
            self.accept()

    def _onError(self, msg):
        self._error.setText(msg)
        self._error.setVisible(True)

    def selectedPath(self):
        return self._selected
