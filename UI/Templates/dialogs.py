from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QDialogButtonBox,
    QFileDialog,
)


def customFileDialog(parent, fileTypes=None, extensions=None, save=False, directory=""):
    options = QFileDialog.Options()

    if fileTypes is None:
        if save:
            fileName, selectedFilter = QFileDialog.getSaveFileName(
                parent, "Save File", directory, options=options
            )
        else:
            fileName, selectedFilter = QFileDialog.getOpenFileName(
                parent, "Open File", directory, options=options
            )
        return fileName, None
    else:
        if extensions is None:
            extensions = ["*"] * len(fileTypes)

        filterList = [
            f"{fileTypes[i]} ({extensions[i]})" for i in range(len(fileTypes))
        ]

        filterString = ";;".join(filterList)
        if save:
            fileName, selectedFilter = QFileDialog.getSaveFileName(
                parent, "Save File", directory, filterString, options=options
            )
        else:
            fileName, selectedFilter = QFileDialog.getOpenFileName(
                parent, "Open File", directory, filterString, options=options
            )

        if fileName == "":
            return None, None

        filterIndex = filterList.index(selectedFilter)
        return fileName, fileTypes[filterIndex]


class BigDatasetWarningDialog(QDialog):
    def __init__(self, file_size_mb, dataset_length, parent=None):
        super().__init__(parent)
        self.user_clicked_pick_samples = False
        self.user_clicked_load_no_cache = False
        self.dataset_length = dataset_length
        self.setWindowTitle("Attention")
        self.resize(700, 250)
        self.setModal(True)

        self.file_size_mb = file_size_mb

        layout = QVBoxLayout()

        msg = (
            "The dataset that you selected is very big. "
            "For efficient processing, you might want to select samples from the dataset "
            "(pick every 10 atomic structures or every 100, etc.). "
            "If you are sure that your system can handle this dataset just press 'Load as a whole (without caching)', "
            "otherwise write your desired slice number in the box below and select "
            "'Pick samples'. "
            "If your system does not have enough resources, yet you still want to have all of the dataset you can click"
            " on 'Load as a whole'. This way, your dataset will be stored partially on RAM and partially on hard "
            "drive (like a caching mechanism), but it will greatly increase the computation time of the program."
            "\n\nAlso, note that the RAM usage estimation shown below only specifies the required space to store the "
            "dataset itself, the values used in plots are also stored in RAM to avoid recalculation. So, the "
            "approximation suggests a minimum on the demanded volume."
        )

        label = QLabel(msg)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignTop)
        layout.addWidget(label)

        self.slice_input = QLineEdit()
        self.slice_input.setPlaceholderText("e.g., 10, 100, ...")
        layout.addWidget(self.slice_input)

        self.ram_hint = QLabel(f"You require approximately {2*self.file_size_mb/1_000_000_000:.2f} GB of RAM "
                               f"(dataset itself+ its prediction dataset in future)!\nDataset length: "
                               f"{self.dataset_length} atoms.")
        self.ram_hint.setStyleSheet("color: #666; font-size: 10pt;")
        layout.addWidget(self.ram_hint)

        button_box = QDialogButtonBox()
        button_box.addButton("Load as a whole", QDialogButtonBox.AcceptRole)
        load_no_cache = button_box.addButton("Load as a whole (without caching)", QDialogButtonBox.AcceptRole)
        pick_btn = button_box.addButton("Pick samples", QDialogButtonBox.ActionRole)

        layout.addWidget(button_box)
        self.setLayout(layout)

        self.slice_input.textChanged.connect(self.update_ram_hint)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        button_box.clicked.connect(self.on_button_clicked)
        pick_btn.clicked.connect(self.accept)
        load_no_cache.clicked.connect(self.accept)

    def on_button_clicked(self, button):
        if button.text() == "Pick samples":
            self.user_clicked_pick_samples = True
        elif button.text() == "Load as a whole (without caching)":
            self.user_clicked_load_no_cache = True

    def update_ram_hint(self):
        text = self.slice_input.text().strip()
        if text:
            try:
                slice_num = int(text)
                if slice_num <= 0:
                    hint = "Slice number must be a positive integer."
                else:
                    effective_size = (self.file_size_mb / slice_num)/1_000_000_000
                    hint = (
                        f"For this slice factor, you require approximately "
                        f"{2*effective_size:.2f} GB of RAM (dataset itself+ its prediction dataset in future)!"
                        f"\nDataset length: {self.dataset_length//slice_num} atoms."
                    )
            except ValueError:
                hint = "Please enter a valid integer slice number."
        else:
            hint = f"RAM usage estimate will appear here.\nDataset length: {self.dataset_length} atoms."

        self.ram_hint.setText(hint)

    def get_slice_number(self):
        text = self.slice_input.text().strip()
        if text:
            return text
        return None


class RemoteStrideDialog(QDialog):
    """Stride-sampling dialog for remote dataset loading.

    Shows total frame count (from server probe) and updates an estimated
    frame count live as the user adjusts N.
    """

    def __init__(self, n_total=None, parent=None):
        super().__init__(parent)
        self.n_total = n_total
        self.setWindowTitle("Stride Sampling")
        self.setModal(True)
        self.resize(420, 160)

        layout = QVBoxLayout()

        if n_total is not None:
            info = QLabel(f"Dataset contains <b>{n_total:,}</b> frames.")
            info.setTextFormat(Qt.RichText)
            layout.addWidget(info)

        row = QHBoxLayout()
        row.addWidget(QLabel("Load every Nth frame (1 = all):"))
        self.spinbox = QSpinBox()
        self.spinbox.setRange(1, 10_000_000)
        self.spinbox.setValue(1)
        row.addWidget(self.spinbox)
        layout.addLayout(row)

        self.estimate_label = QLabel(self._estimate_text(1))
        self.estimate_label.setStyleSheet("color: #666; font-size: 10pt;")
        layout.addWidget(self.estimate_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)
        self.spinbox.valueChanged.connect(self._on_value_changed)

    def _estimate_text(self, n: int) -> str:
        if self.n_total is None:
            if n == 1:
                return "Estimated frames: all"
            return f"Estimated frames: total ÷ {n}"
        estimated = max(1, (self.n_total + n - 1) // n)
        if n == 1:
            return f"Estimated frames: {self.n_total:,} (all)"
        return f"Estimated frames: ~{estimated:,}"

    def _on_value_changed(self, n: int):
        self.estimate_label.setText(self._estimate_text(n))

    def get_stride(self) -> int:
        return self.spinbox.value()
