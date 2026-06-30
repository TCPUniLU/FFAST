from events import EventClass


class MenuHandlerBase(EventClass):
    """Common base for the main-window and Loupe menu adapters.

    Holds the one piece of menu wiring shared by both: the ``&3D View → New``
    action plus its enable/disable hook. ``MainMenuHandler`` reaches into
    ``self.handler.env``; ``LoupeMenuHandler`` reaches into ``self.window``
    (the Loupe). Each subclass builds its own menus in ``connectActions``.
    """

    def __init__(self, window):
        self.handler = window.handler
        self.window = window
        self.connectActions()

    def connectActions(self):  # pragma: no cover - overridden
        raise NotImplementedError

    def _addLoupeMenu(self, mb):
        """Add the shared ``&3D View`` menu with a (disabled) New action.

        Returns the menu so subclasses can append mode-specific submenus.
        The New action starts disabled and is enabled by ``REMOTE_CONNECTED``
        via ``UIHandler._onRemoteConnected`` → ``setNewLoupeEnabled``.
        """
        Loupe = mb.addMenu("&3D &View")
        self._newLoupeAction = Loupe.addAction("New", self.newLoupe, "Ctrl+n")
        self._newLoupeAction.setEnabled(False)
        Loupe.addSeparator()
        return Loupe

    def newLoupe(self):
        self.handler.newLoupe()

    def setNewLoupeEnabled(self, enabled: bool):
        action = getattr(self, "_newLoupeAction", None)
        if action is not None:
            action.setEnabled(enabled)
