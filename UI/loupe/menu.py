from UI.menuShared import MenuHandlerBase


class LoupeMenuHandler(MenuHandlerBase):
    """Per-Loupe menu bar.

    Bond width/colour, atom size, and background colour used to live here as
    submenus; they now sit in the sidebar panes (ADR 0040): Bond width/colour in
    BONDS, Atom size + Background colour in DISPLAY. Nothing is left to add, so
    the menu bar stays empty for now.
    """

    def connectActions(self):
        pass
