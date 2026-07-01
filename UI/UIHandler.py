import logging
import os

import pyqtgraph
from PySide6 import QtWidgets
from PySide6.QtCore import QDir
from PySide6.QtWidgets import QMessageBox

from events import EventClass
from UI.loupe.window import Loupe
from UI.MainWindow import MainWindow

logger = logging.getLogger("FFAST")


class UIHandler(EventClass):
    """
    Main object responsible for handling UI elements.
    """

    uiFilesPath = os.path.join("UI", "uiFiles")
    env = None
    tabs = []
    loupes = 0
    client_features = []
    dataset_features = []
    activeLoupe = None  # Currently active Loupe for menu actions
    workdir = None  # Working directory for file dialogs
    energyShiftEnabled = False  # Global toggle for energy shift

    def __init__(self, *args, workdir=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.workdir = workdir if workdir else os.getcwd()
        self.eventSubscribe("QUIT_READY", self.setQuitReady)
        self.eventSubscribe("CLUSTER_FOR_VARIABLE", self.showCLusterVariable)
        self.eventSubscribe("REMOTE_CONNECTED", self._onRemoteConnected)

    def quitEvent(self):
        self.eventPush("QUIT_EVENT")
        # self.app.quit()
        self.setQuitReady()

    def quit(self):
        self.app.quit()

    quitReady = False

    def setQuitReady(self):
        self.quitReady = True

    def _onRemoteConnected(self):
        """Local (or cluster) server connected — enable the New Loupe action."""
        if hasattr(self, "window") and hasattr(self.window, "menuHandler"):
            self.window.menuHandler.setNewLoupeEnabled(True)

    def showCLusterVariable(self):
        msg = QMessageBox(self.window)
        msg.setWindowTitle("Notification")
        msg.setText("The cluster errors feature is not supported for variable datasets.")
        msg.setIcon(QMessageBox.Information)
        msg.setStandardButtons(QMessageBox.Ok)

        result = msg.exec()

    def nLoupes(self):
        return self.loupes

    """def getActiveLoupe(self):
        \"""Get the currently active Loupe instance, or the most recently created one.\"""
        if self.activeLoupe is not None:
            return self.activeLoupe
        elif len(self.loupes) > 0:
            return self.loupes[-1]
        return None"""

    def newLoupe(self):
        loupe = Loupe(self, self.loupes)

        for feature in self.client_features:
            if feature.widget_factory is not None:
                feature.widget_factory(self, loupe)

        loupe.forceUpdate()
        self.loupes += 1

        loupe.show()
        loupe.setFocus()

        self.eventPush("LOUPES_UPDATE")

    def registerClientFeatures(self, features):
        self.client_features.extend(features)

    def registerDatasetFeatures(self, features):
        self.dataset_features.extend(features)

    def setEnvironment(self, env):
        self.env = env

    def launch(self, app):
        from config.uiConfig import config, configStyleSheet

        self.config = config

        # qasync creates its own QApplication instance, and as such you don't
        # need to create a new one, just access the created instance.
        # Also, we don't need app.exec() at the end, that's also handled
        # app = QtWidgets.QApplication.instance()  # (sys.argv)
        app.setApplicationDisplayName("FFAST")
        app.setQuitOnLastWindowClosed(False)

        # Load icons
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        QDir.addSearchPath("icon", os.path.join(_root, "theme"))

        # pyqtgraph configs
        self.initialisePlotConfigs()

        # TODO
        if True:
            if "Fusion" in QtWidgets.QStyleFactory.keys():
                app.setStyle("Fusion")

            # Load styles
            with open(os.path.join(_root, "style.qss"), "r") as styleFile:
                styleSheet = styleFile.read()

            # set variables
            styleSheet = configStyleSheet(styleSheet)
            if app is not None:
                app.setStyleSheet(styleSheet)

        window = MainWindow(self)
        window.show()

        self.window = window
        self.app = app
        self.mainWindow = window

    def initialisePlotConfigs(self):
        # Try OpenGL by default, but verify it's safe first: Apple's
        # OpenGL-over-Metal shim segfaults in glDrawArrays when ScatterPlotItem
        # blits its point atlas (drawPixmapFragments). A segfault can't be caught
        # in-process, so glProbe runs a throwaway child that paints a GL scatter;
        # if it survives we use GL, otherwise we fall back to software. Cached.
        from UI.glProbe import opengl_is_safe

        use_gl = opengl_is_safe()
        logger.info("pyqtgraph rendering: %s", "OpenGL" if use_gl else "software")
        pyqtgraph.setConfigOptions(
            antialias=True,
            leftButtonPan=False,
            crashWarning=True,
            foreground=self.config["envs"].get("TextColor1"),
            # background=self.config["envs"].get("BGColor2"),
            background=None,
            useOpenGL=use_gl,
            enableExperimental=use_gl,
            exitCleanup=True,
        )

    def addContentTab(self, widget, name):
        self.mainWindow.mainContent.addTab(widget, name)
