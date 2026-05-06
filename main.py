from __future__ import annotations

import io
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional

import qrcode
from PIL import Image
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap, QImage, QFont
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

# ---------------------------------------------------------------------------
# Model Identifier → display size lookup (built-in screen diagonal in inches)
# ---------------------------------------------------------------------------
MODEL_DISPLAY_SIZE: dict[str, str] = {
    # MacBook Air
    "MacBookAir3,1": '11"', "MacBookAir3,2": '13"',
    "MacBookAir4,1": '11"', "MacBookAir4,2": '13"',
    "MacBookAir5,1": '11"', "MacBookAir5,2": '13"',
    "MacBookAir6,1": '11"', "MacBookAir6,2": '13"',
    "MacBookAir7,1": '11"', "MacBookAir7,2": '13"',
    "MacBookAir8,1": '13"', "MacBookAir8,2": '13"',
    "MacBookAir9,1": '13"',
    "MacBookAir10,1": '13"',
    "Mac14,2": '13"',   # MBA M2 13"
    "Mac15,12": '13"',  # MBA M3 13"
    "Mac15,13": '15"',  # MBA M3 15"
    "Mac14,15": '15"',  # MBA M2 15"
    # MacBook Pro 13"
    "MacBookPro5,5": '13"', "MacBookPro7,1": '13"',
    "MacBookPro8,1": '13"', "MacBookPro9,2": '13"',
    "MacBookPro10,2": '13"', "MacBookPro11,1": '13"',
    "MacBookPro12,1": '13"', "MacBookPro13,1": '13"',
    "MacBookPro13,2": '13"', "MacBookPro14,1": '13"',
    "MacBookPro14,2": '13"', "MacBookPro15,2": '13"',
    "MacBookPro15,4": '13"', "MacBookPro16,2": '13"',
    "MacBookPro16,3": '13"', "MacBookPro17,1": '13"',
    # MacBook Pro 14"
    "MacBookPro18,3": '14"', "MacBookPro18,4": '16"',
    "Mac14,5": '14"', "Mac14,9": '14"',
    "Mac15,6": '14"', "Mac15,8": '14"', "Mac15,10": '14"',
    # MacBook Pro 15"
    "MacBookPro8,2": '15"', "MacBookPro8,3": '17"',
    "MacBookPro9,1": '15"', "MacBookPro10,1": '15"',
    "MacBookPro11,2": '15"', "MacBookPro11,3": '15"',
    "MacBookPro11,4": '15"', "MacBookPro11,5": '15"',
    "MacBookPro12,1": '13"',
    "MacBookPro13,3": '15"', "MacBookPro14,3": '15"',
    "MacBookPro15,1": '15"', "MacBookPro15,3": '15"',
    "MacBookPro16,1": '16"', "MacBookPro16,4": '16"',
    # MacBook Pro 16"
    "Mac14,6": '16"', "Mac14,10": '16"',
    "Mac15,7": '16"', "Mac15,9": '16"', "Mac15,11": '16"',
    # MacBook (Retina 12")
    "MacBook8,1": '12"', "MacBook9,1": '12"',
    "MacBook10,1": '12"',
}


# ---------------------------------------------------------------------------
# System info dataclass
# ---------------------------------------------------------------------------
@dataclass
class SystemInfo:
    serial: str = ""
    manufacturer: str = "Apple"
    model_name: str = ""
    model_id: str = ""
    cpu_model: str = ""
    ram_value: str = ""
    hdd1_value: str = ""
    hdd2_value: str = ""
    gpu_model: str = ""
    battery_cycles: str = ""
    monitor_size: str = ""
    resolution: str = ""


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------
def _run(cmd: list[str]) -> str:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _grep(text: str, pattern: str) -> str:
    m = re.search(pattern, text, re.MULTILINE)
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# System info gathering
# ---------------------------------------------------------------------------
def gather_system_info() -> SystemInfo:
    info = SystemInfo()

    # --- Hardware overview (serial, model, ram, chip for Apple Silicon) ---
    hw = _run(["system_profiler", "SPHardwareDataType"])
    info.serial = _grep(hw, r"Serial Number \(system\):\s*(.+)")
    info.model_name = _grep(hw, r"Model Name:\s*(.+)")
    info.model_id = _grep(hw, r"Model Identifier:\s*(.+)")
    info.ram_value = _grep(hw, r"Memory:\s*(.+)")

    # CPU — Apple Silicon uses "Chip:", Intel uses sysctl brand_string
    chip = _grep(hw, r"^\s*Chip:\s*(.+)")
    if chip:
        info.cpu_model = chip
    else:
        info.cpu_model = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        if not info.cpu_model:
            info.cpu_model = _grep(hw, r"Processor Name:\s*(.+)")

    # --- Display size from model id lookup ---
    info.monitor_size = MODEL_DISPLAY_SIZE.get(info.model_id, "")

    # --- Storage ---
    storage_raw = _run(["system_profiler", "SPStorageDataType"])
    internal_capacities = _parse_internal_storage(storage_raw)
    info.hdd1_value = internal_capacities[0] if len(internal_capacities) > 0 else ""
    info.hdd2_value = internal_capacities[1] if len(internal_capacities) > 1 else ""

    # --- GPU ---
    display_raw = _run(["system_profiler", "SPDisplaysDataType"])
    info.gpu_model = _grep(display_raw, r"Chipset Model:\s*(.+)")

    # Resolution — first match only (built-in display)
    res_match = re.search(r"Resolution:\s*(\d+\s*x\s*\d+)", display_raw)
    info.resolution = res_match.group(1).strip() if res_match else ""

    # --- Battery ---
    power_raw = _run(["system_profiler", "SPPowerDataType"])
    info.battery_cycles = _grep(power_raw, r"Cycle Count:\s*(\d+)")

    return info


def _parse_internal_storage(text: str) -> list[str]:
    """
    Parse system_profiler SPStorageDataType output and return capacities
    of internal drives only, in order (e.g. ["512 GB", "1 TB"]).
    """
    capacities: list[str] = []
    # Split into per-volume blocks
    blocks = re.split(r"\n(?=\s{4}\S)", text)
    for block in blocks:
        # Must be marked as Internal: Yes
        if not re.search(r"Internal:\s*Yes", block, re.IGNORECASE):
            continue
        cap_match = re.search(r"Capacity:\s*([\d\.,]+ [KMGT]B)", block)
        if cap_match:
            capacities.append(cap_match.group(1).strip())
    # Deduplicate while preserving order (APFS volumes share one physical drive)
    seen: set[str] = set()
    unique: list[str] = []
    for c in capacities:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


# ---------------------------------------------------------------------------
# QR data builder
# ---------------------------------------------------------------------------
def build_qr_string(info: SystemInfo, user: dict[str, str]) -> str:
    keyboard_layout = user.get("keyboard_layout", "")
    laptop_class    = user.get("laptop_class", "")
    compiled_notes  = user.get("compiled_notes", "")
    nrzwrotu        = user.get("nrzwrotu", "")
    magazyn         = user.get("magazyn", "")
    nrid            = user.get("nrid", "")

    touchscreen = ""          # not relevant for Mac
    sw          = " "         # generic switch value

    data = (
        "\t\t"
        + info.serial                + "\t"
        + info.manufacturer          + "\t"
        + info.model_name            + "\t"
        + info.cpu_model             + "\t"
        + info.ram_value             + "\t"
        + info.hdd1_value            + "\t"
        + info.hdd2_value            + "\t"
        + info.gpu_model             + "\t"
        + info.battery_cycles        + "\t"
        + info.monitor_size + touchscreen + "\t"
        + info.resolution            + "\t"
        + sw + "\t"          # LAN_switch
        + sw + "\t"          # WLAN_switch
        + sw + "\t"          # camera_switch
        + sw + "\t"          # sound_switch
        + keyboard_layout            + "\t"
        + sw + "\t"          # polska_switch
        + "None"                     + "\t"   # license
        + laptop_class               + "\t"
        + compiled_notes             + "\t"
        + nrzwrotu + "\t\t"
        + magazyn                    + "\t"
        + nrid + "\t\t\t\t\t"
        + sw + "\t\t\t\t\t\t"  # ant_switch
        + "\t"               # klapa_gorna
        + "\t"               # palmrest
        + "\t"               # klapa_dolna
        + "\t"               # ramka
        + "\t\t"             # touchpad_odnowienie
                             # matryca (last field, no trailing tab)
    )
    return data


# ---------------------------------------------------------------------------
# QR pixmap generator
# ---------------------------------------------------------------------------
def generate_qr_pixmap(data: str, size: int = 400) -> QPixmap:
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img: Image.Image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    img = img.resize((size, size), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    qimage = QImage.fromData(buf.read(), "PNG")
    return QPixmap.fromImage(qimage)


# ---------------------------------------------------------------------------
# Background thread for system info gathering
# ---------------------------------------------------------------------------
class InfoWorker(QThread):
    finished = Signal(object)  # emits SystemInfo

    def run(self) -> None:
        info = gather_system_info()
        self.finished.emit(info)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Mac System Info")
        self.setMinimumSize(900, 600)
        self._info: Optional[SystemInfo] = None

        # Central widget with horizontal split
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(16)

        root_layout.addWidget(self._build_left_panel(), stretch=1)
        root_layout.addWidget(self._build_divider())
        root_layout.addWidget(self._build_right_panel(), stretch=1)

        # Kick off background info gathering
        self._worker = InfoWorker()
        self._worker.finished.connect(self._on_info_ready)
        self._worker.start()

    # ------------------------------------------------------------------
    # Panel builders
    # ------------------------------------------------------------------
    def _build_left_panel(self) -> QWidget:
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # Title
        title = QLabel("Informacje o systemie")
        font = QFont()
        font.setPointSize(13)
        font.setBold(True)
        title.setFont(font)
        outer.addWidget(title)

        # Scroll area wraps the form
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(6)
        form.setContentsMargins(0, 4, 0, 4)
        scroll.setWidget(form_widget)
        outer.addWidget(scroll)

        # --- Read-only fields ---
        self._f_serial       = self._ro_field(form, "Numer seryjny:")
        self._f_manufacturer = self._ro_field(form, "Producent:")
        self._f_manufacturer.setText("Apple")
        self._f_model        = self._ro_field(form, "Model:")
        self._f_cpu          = self._ro_field(form, "Procesor:")
        self._f_ram          = self._ro_field(form, "RAM:")
        self._f_hdd1         = self._ro_field(form, "Dysk:")
        self._f_hdd2         = self._ro_field(form, "Dysk 2:")
        self._f_gpu          = self._ro_field(form, "GPU:")
        self._f_battery      = self._ro_field(form, "Bateria (cykle):")
        self._f_display      = self._ro_field(form, "Wyświetlacz:")
        self._f_resolution   = self._ro_field(form, "Rozdzielczość:")

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        form.addRow(sep)

        # --- User-editable fields ---
        self._f_layout  = self._rw_field(form, "Layout:")
        self._f_class   = self._rw_field(form, "Klasa:")
        self._f_notes   = self._rw_field(form, "Uwagi:")
        self._f_nrzwrotu = self._rw_field(form, "Nr zwrotu:")
        self._f_magazyn  = self._rw_field(form, "Magazyn:")
        self._f_nrid     = self._rw_field(form, "Nr ID:")

        # Button
        self._btn = QPushButton("Generuj QR kod")
        self._btn.setFixedHeight(36)
        self._btn.clicked.connect(self._on_generate)
        outer.addWidget(self._btn)

        return container

    def _build_right_panel(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        title = QLabel("Kod QR")
        font = QFont()
        font.setPointSize(13)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._qr_label.setMinimumSize(300, 300)
        self._qr_label.setText("Wczytywanie danych…")
        self._qr_label.setStyleSheet("color: gray; font-size: 14px;")
        layout.addWidget(self._qr_label)

        return container

    def _build_divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line

    # ------------------------------------------------------------------
    # Field helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _ro_field(form: QFormLayout, label: str) -> QLineEdit:
        edit = QLineEdit()
        edit.setReadOnly(True)
        edit.setPlaceholderText("wczytywanie…")
        edit.setStyleSheet("background: transparent; border: none; color: palette(text);")
        form.addRow(label, edit)
        return edit

    @staticmethod
    def _rw_field(form: QFormLayout, label: str) -> QLineEdit:
        edit = QLineEdit()
        form.addRow(label, edit)
        return edit

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    def _on_info_ready(self, info: SystemInfo) -> None:
        self._info = info
        self._f_serial.setText(info.serial)
        self._f_model.setText(info.model_name)
        self._f_cpu.setText(info.cpu_model)
        self._f_ram.setText(info.ram_value)
        self._f_hdd1.setText(info.hdd1_value)
        self._f_hdd2.setText(info.hdd2_value)
        self._f_gpu.setText(info.gpu_model)
        self._f_battery.setText(info.battery_cycles)
        self._f_display.setText(info.monitor_size)
        self._f_resolution.setText(info.resolution)
        # Auto-generate QR after info is loaded
        self._on_generate()

    def _on_generate(self) -> None:
        if self._info is None:
            return
        user = {
            "keyboard_layout": self._f_layout.text(),
            "laptop_class":    self._f_class.text(),
            "compiled_notes":  self._f_notes.text(),
            "nrzwrotu":        self._f_nrzwrotu.text(),
            "magazyn":         self._f_magazyn.text(),
            "nrid":            self._f_nrid.text(),
        }
        data = build_qr_string(self._info, user)
        qr_size = min(
            self._qr_label.width() or 400,
            self._qr_label.height() or 400,
        )
        qr_size = max(qr_size, 300)
        pixmap = generate_qr_pixmap(data, size=qr_size)
        self._qr_label.setStyleSheet("")
        self._qr_label.setText("")
        self._qr_label.setPixmap(
            pixmap.scaled(
                self._qr_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        # Re-scale existing QR pixmap when window is resized
        if self._qr_label.pixmap() and not self._qr_label.pixmap().isNull():
            self._on_generate()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
