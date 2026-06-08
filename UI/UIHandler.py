import os

import pyqtgraph
from PySide6 import QtWidgets
from PySide6.QtCore import QDir
from PySide6.QtWidgets import QMessageBox

from events import EventClass
from UI.Loupe import Loupe
from UI.MainWindow import MainWindow


class UIHandler(EventClass):
    """
    Main object responsible for handling UI elements.
    """

    uiFilesPath = os.path.join("UI", "uiFiles")
    env = None
    tabs = []
    loupes = 0
    loupe_list = []
    loupeModules = []
    activeLoupe = None  # Currently active Loupe for menu actions
    workdir = None  # Working directory for file dialogs
    energyShiftEnabled = False  # Global toggle for energy shift

    def __init__(self, *args, workdir=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.workdir = workdir if workdir else os.getcwd()
        self.eventSubscribe("QUIT_READY", self.setQuitReady)
        self.eventSubscribe('CLUSTER_FOR_VARIABLE', self.showCLusterVariable)
        self.eventSubscribe('LoupeClosing', self.onLoupeClosed)

    def quitEvent(self):
        self.eventPush("QUIT_EVENT")
        # self.app.quit()
        self.setQuitReady()

    def quit(self):
        self.app.quit()

    quitReady = False

    def setQuitReady(self):
        self.quitReady = True

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

        for func in self.loupeModules:
            func(self, loupe)

        loupe.forceUpdate()
        self.loupe_list.append(self.loupes)
        self.loupes += 1
        loupe.show()
        loupe.setFocus()

        self.eventPush("LOUPES_UPDATE")

    def onLoupeClosed(self, id):
        #print(f"loupe {id} is closed")
        self.loupe_list.remove(id)
        if id == self.activeLoupe:
            self.activeLoupe = self.loupe_list[0] if len(self.loupe_list) != 0 else None

    def registerLoupeModule(self, func):
        self.loupeModules.append(func)

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
        self.mainWindow.mainContent.currentChanged.connect(self.onContentTabChanged)

    def onContentTabChanged(self, index):
        #tab_widget = self.mainWindow.mainContent.widget(index)
        tab_name = self.mainWindow.mainContent.tabText(index)

        # print(f"Selected tab: {tab_name}")
        if tab_name == "Subsystem Errors" and self.activeLoupe is None:  # create new loupe only if there is none.
            self.newLoupe()
            self.activeLoupe = self.loupes-1
            #print(f'active loupe: {self.activeLoupe}')

    def initialisePlotConfigs(self):
        pyqtgraph.setConfigOptions(
            antialias=True,
            leftButtonPan=False,
            crashWarning=True,
            foreground=self.config["envs"].get("TextColor1"),
            # background=self.config["envs"].get("BGColor2"),
            background=None,
            useOpenGL=True,
            enableExperimental=True,
            exitCleanup=True,
        )

    def addContentTab(self, widget, name):
        self.mainWindow.mainContent.addTab(widget, name)
