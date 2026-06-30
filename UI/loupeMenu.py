from UI.menuShared import MenuHandlerBase


class LoupeMenuHandler(MenuHandlerBase):
    """Per-Loupe menu: bond width, atom size, and colour controls.

    Reaches into ``self.window`` (the Loupe) — its ``settings`` and ``canvas``.
    These directly mutate client-side Loupe settings today; ADR 0010/0021 will
    later reroute them through the server as View Commands.
    """

    def connectActions(self):
        window = self.window
        mb = window.menuBar()

        Loupe = self._addLoupeMenu(mb)

        # Bond Width submenu
        bondMenu = Loupe.addMenu("Bond Width")
        bondMenu.addAction("Thin (10)", lambda: self.setBondWidth(10))
        bondMenu.addAction("Normal (25)", lambda: self.setBondWidth(25))
        bondMenu.addAction("Thick (50)", lambda: self.setBondWidth(50))
        bondMenu.addAction("Extra Thick (100)", lambda: self.setBondWidth(100))
        # TODO: add custom bond width dialog
        # bondMenu.addSeparator()
        # bondMenu.addAction("Custom...", self.showBondWidthDialog)

        # Atom Size submenu
        atomMenu = Loupe.addMenu("Atom Size")
        atomMenu.addAction("50%", lambda: self.setAtomSize(0.5))
        atomMenu.addAction("75%", lambda: self.setAtomSize(0.75))
        atomMenu.addAction("100%", lambda: self.setAtomSize(1.0))
        atomMenu.addAction("150%", lambda: self.setAtomSize(1.5))
        atomMenu.addAction("200%", lambda: self.setAtomSize(2.0))
        # TODO: add custom atom size dialog
        # atomMenu.addSeparator()
        # atomMenu.addAction("Custom...", self.showAtomSizeDialog)

        # Colors submenu
        colorMenu = Loupe.addMenu("Colors")
        colorMenu.addAction("Bond Color...", self.showBondColorPicker)
        colorMenu.addAction("Background Color...", self.showBackgroundColorPicker)

    def setBondWidth(self, width):
        """Set bond width for the current Loupe."""
        loupe = self.window
        if not loupe:
            return
        loupe.settings.setParameter("bondWidth", width, refresh=True)

    def setAtomSize(self, scale):
        """Set atom size scale for the current Loupe."""
        loupe = self.window
        if loupe and hasattr(loupe, 'settings'):
            loupe.settings.setParameter("atomSizeScale", scale, refresh=True)

    def showBondWidthDialog(self):
        """Show custom bond width input dialog (current loupe)."""
        loupe = self.window
        if not loupe:
            return

        from PySide6.QtWidgets import QInputDialog
        current = loupe.settings.get("bondWidth", 200)
        value, ok = QInputDialog.getInt(
            self.window,
            "Bond Width",
            "Enter bond width (pixels):",
            value=current,
            min=10,
            max=1000,
            step=10
        )
        if ok:
            self.setBondWidth(value)

    def showAtomSizeDialog(self):
        """Show custom atom size input dialog. (current loupe)"""
        loupe = self.window
        if not loupe:
            return

        from PySide6.QtWidgets import QInputDialog
        current = loupe.settings.get("atomSizeScale", 1.0)
        value, ok = QInputDialog.getDouble(
            self.window,
            "Atom Size",
            "Enter atom size scale:",
            value=current,
            min=0.1,
            max=10.0,
            decimals=2
        )
        if ok:
            self.setAtomSize(value)

    def showBondColorPicker(self):
        """Show bond color picker dialog. (current loupe)"""
        loupe = self.window
        if not loupe:
            return

        from PySide6.QtWidgets import QColorDialog
        from PySide6.QtGui import QColor
        from config.userConfig import getConfig

        current_hex = loupe.settings.get("bondColor", getConfig("loupeBondsColor", "#404040"))
        current_color = QColor(current_hex)

        color = QColorDialog.getColor(
            current_color,
            self.window,
            "Select Bond Color"
        )

        if color.isValid():
            hex_color = color.name()
            loupe.settings.setParameter("bondColor", hex_color, refresh=True)

    def showBackgroundColorPicker(self):
        """Show background color picker dialog. (current loupe)"""
        loupe = self.window
        if not loupe:
            return

        from PySide6.QtWidgets import QColorDialog
        from PySide6.QtGui import QColor
        from config.userConfig import getConfig

        current_hex = getConfig("loupeBGColor", "#000000")
        current_color = QColor(current_hex)

        color = QColorDialog.getColor(
            current_color,
            self.window,
            "Select Background Color"
        )

        if color.isValid():
            # Update canvas background directly
            loupe.canvas.canvas.bgcolor = color.getRgbF()[:3]
            loupe.canvas.canvas.update()
