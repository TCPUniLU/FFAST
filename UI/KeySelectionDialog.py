"""Dialog for selecting energy/force keys from ASE datasets."""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QCheckBox,
    QLineEdit,
)
from PySide6.QtCore import Qt


class KeySelectionDialog(QDialog):
    """Dialog for selecting reference and prediction keys from ASE datasets."""

    def __init__(self, energy_keys, force_keys, parent=None, for_predictions=False):
        """Initialize key selection dialog.

        Args:
            energy_keys: List of available energy key names
            force_keys: List of available force key names
            parent: Parent widget
            for_predictions: If True, show simplified dialog for loading predictions only
        """
        super().__init__(parent)
        self.energy_keys = energy_keys
        self.force_keys = force_keys
        self.for_predictions = for_predictions
        self.setWindowTitle("Select Energy/Force Keys")
        self.setModal(True)

        self._setupUI()

    def _setupUI(self):
        """Set up the dialog UI."""
        layout = QVBoxLayout()

        # Reference selection section
        ref_layout = QVBoxLayout()
        ref_layout.addWidget(QLabel("<b>Reference Data (Ground Truth):</b>"))

        # Energy key selection
        energy_options = ["<Use ASE Calculator>"] + (
            self.energy_keys if self.energy_keys else []
        )
        self.energy_ref_combo = QComboBox()
        self.energy_ref_combo.addItems(energy_options)
        if self.energy_keys:
            self.energy_ref_combo.setCurrentIndex(1)  # Skip calculator by default

        ref_layout.addWidget(
            QLabel(f"Energy key ({len(self.energy_keys)} available):")
        )
        ref_layout.addWidget(self.energy_ref_combo)

        # Force key selection
        force_options = ["<Use ASE Calculator>"] + (
            self.force_keys if self.force_keys else []
        )
        self.force_ref_combo = QComboBox()
        self.force_ref_combo.addItems(force_options)
        if self.force_keys:
            self.force_ref_combo.setCurrentIndex(1)  # Skip calculator by default

        ref_layout.addWidget(
            QLabel(f"Force key ({len(self.force_keys)} available):")
        )
        ref_layout.addWidget(self.force_ref_combo)

        layout.addLayout(ref_layout)

        # Prediction selection section (only for dataset loading, not for prepredicted model loading)
        self.pred_rows = []
        if not self.for_predictions:
            pred_layout = QVBoxLayout()
            pred_layout.addWidget(QLabel("<b>Load as Predictions (Optional):</b>"))
            pred_layout.addWidget(
                QLabel(
                    "Select additional key pairs to load as pre-computed model predictions:"
                )
            )

            # Container for prediction rows
            self.pred_rows_layout = QVBoxLayout()
            pred_layout.addLayout(self.pred_rows_layout)

            # Add Prediction button
            add_pred_button = QPushButton("Add Prediction")
            add_pred_button.clicked.connect(self._addPredictionRow)
            pred_layout.addWidget(add_pred_button)

            layout.addLayout(pred_layout)

        # Buttons
        button_layout = QHBoxLayout()
        ok_button = QPushButton("OK")
        cancel_button = QPushButton("Cancel")

        ok_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)

        button_layout.addWidget(ok_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)

        self.setLayout(layout)

    def _addPredictionRow(self):
        """Add a new prediction row with combo boxes for energy/force selection."""
        row_layout = QHBoxLayout()

        # Energy combo box
        energy_options = ["<Use ASE Calculator>"] + (
            self.energy_keys if self.energy_keys else []
        )
        energy_combo = QComboBox()
        energy_combo.addItems(energy_options)

        # Force combo box
        force_options = ["<Use ASE Calculator>"] + (
            self.force_keys if self.force_keys else []
        )
        force_combo = QComboBox()
        force_combo.addItems(force_options)

        # Model name input
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("Model name...")

        # Remove button
        remove_button = QPushButton("Remove")
        remove_button.clicked.connect(
            lambda: self._removePredictionRow(row_layout, energy_combo, force_combo, name_edit, remove_button)
        )

        # Add widgets to row
        row_layout.addWidget(QLabel("Energy:"))
        row_layout.addWidget(energy_combo)
        row_layout.addWidget(QLabel("Force:"))
        row_layout.addWidget(force_combo)
        row_layout.addWidget(QLabel("Name:"))
        row_layout.addWidget(name_edit)
        row_layout.addWidget(remove_button)

        # Add row to container
        self.pred_rows_layout.addLayout(row_layout)

        # Store row components
        self.pred_rows.append((energy_combo, force_combo, name_edit, row_layout, remove_button))

    def _removePredictionRow(self, row_layout, energy_combo, force_combo, name_edit, remove_button):
        """Remove a prediction row."""
        # Remove from stored rows
        self.pred_rows = [
            row for row in self.pred_rows
            if row[0] != energy_combo
        ]

        # Remove widgets from layout
        while row_layout.count():
            item = row_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        # Remove layout
        self.pred_rows_layout.removeItem(row_layout)

    def _pairKeys(self, energy_keys, force_keys):
        """Intelligently pair energy and force keys.

        Looks for matching prefixes (e.g., 'MACE_energy' with 'MACE_forces').

        Args:
            energy_keys: List of energy key names
            force_keys: List of force key names

        Returns:
            list: [(energy_key, force_key, suggested_model_name), ...]
        """
        pairs = []

        # Extract prefixes from keys
        def get_prefix(key):
            for sep in ["_", "-", "."]:
                if sep in key:
                    return key.split(sep)[0]
            return (
                key.replace("energy", "")
                .replace("forces", "")
                .replace("Energy", "")
                .replace("Forces", "")
                .strip()
            )

        # Try to match by prefix
        for e_key in energy_keys:
            e_prefix = get_prefix(e_key)

            # Find matching force key
            matching_f_key = None
            for f_key in force_keys:
                f_prefix = get_prefix(f_key)
                if e_prefix == f_prefix:
                    matching_f_key = f_key
                    break

            if matching_f_key:
                pairs.append((e_key, matching_f_key, e_prefix or "Model"))
            else:
                # No match found, pair with first force key
                if force_keys:
                    pairs.append(
                        (e_key, force_keys[0], get_prefix(e_key) or "Model")
                    )

        # Add any unmatched force keys
        used_force_keys = {pair[1] for pair in pairs}
        for f_key in force_keys:
            if f_key not in used_force_keys:
                if energy_keys:
                    pairs.append(
                        (energy_keys[0], f_key, get_prefix(f_key) or "Model")
                    )

        return pairs

    def getSelection(self):
        """Get the selected keys from the dialog.

        Returns:
            dict: {
                'energy_ref': selected energy key or "" for calculator or None,
                'force_ref': selected force key or "" for calculator or None,
                'predictions': [(energy_key, force_key, model_name), ...]
            }
        """
        # Get reference selections
        selected_energy = self.energy_ref_combo.currentText()
        if selected_energy == "<Use ASE Calculator>":
            selected_energy = ""
        elif selected_energy == "" or not self.energy_keys:
            selected_energy = None

        selected_force = self.force_ref_combo.currentText()
        if selected_force == "<Use ASE Calculator>":
            selected_force = ""
        elif selected_force == "" or not self.force_keys:
            selected_force = None

        # Collect prediction selections from combo box rows
        predictions = []
        for energy_combo, force_combo, name_edit, row_layout, remove_button in self.pred_rows:
            selected_pred_energy = energy_combo.currentText()
            if selected_pred_energy == "<Use ASE Calculator>":
                selected_pred_energy = ""

            selected_pred_force = force_combo.currentText()
            if selected_pred_force == "<Use ASE Calculator>":
                selected_pred_force = ""

            model_name = name_edit.text() or "Model"
            predictions.append((selected_pred_energy, selected_pred_force, model_name))

        return {
            "energy_ref": selected_energy,
            "force_ref": selected_force,
            "predictions": predictions,
        }
