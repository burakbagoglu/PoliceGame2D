from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from PySide6.QtCore import QProcess, Qt, QUrl
    from PySide6.QtGui import QDesktopServices, QFont
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QFileDialog,
        QFormLayout,
        QFrame,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QInputDialog,
        QLabel,
        QLineEdit,
        QMainWindow,
        QLayout,
        QMessageBox,
        QPlainTextEdit,
        QScrollArea,
        QTabWidget,
        QSizePolicy,
        QProgressBar,
        QPushButton,
        QSpinBox,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise SystemExit(
        "PySide6 kurulu degil. sd_card_tool/start_windows.bat dosyasini calistirin."
    ) from exc

from sd_card_tool.imager import (
    build_imager_arguments,
    discover_imager,
    is_windows_admin,
    parse_progress,
)
from sd_card_tool.provisioning import ProvisionSettings, build_first_run_script
from sd_card_tool.windows_disks import DiskDevice, list_removable_disks


CLIENT_SOURCE = ROOT_DIR / "thief_client"
OFFICIAL_LITE_32_URL = "https://downloads.raspberrypi.com/raspios_lite_armhf_latest"
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.process: QProcess | None = None
        self.secret_temp: tempfile.TemporaryDirectory | None = None
        self.setWindowTitle("Polis Oyunu · SD Kart Hazirlama")
        self.resize(1040, 900)
        self.setMinimumSize(820, 700)
        self._build_ui()
        self._apply_style()
        self._load_defaults()
        self.refresh_disks()

    def _build_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setCentralWidget(scroll)

        central = QWidget()
        central.setObjectName("page")
        scroll.setWidget(central)
        page = QVBoxLayout(central)
        page.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        page.setContentsMargins(28, 24, 28, 24)
        page.setSpacing(16)

        title = QLabel("Pi Zero 2 W SD Kart Hazirlama")
        title.setObjectName("title")
        page.addWidget(title)
        subtitle = QLabel(
            "Raspberry Pi OS'i yazar; Wi-Fi, ekran, server ve client kurulumunu ilk acilista tamamlar."
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        page.addWidget(subtitle)

        self.admin_banner = QLabel()
        self.admin_banner.setWordWrap(True)
        self.admin_banner.setObjectName("banner")
        page.addWidget(self.admin_banner)

        source_group = QGroupBox("OS ve hedef kart")
        source_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        source_form = QGridLayout(source_group)
        source_form.setHorizontalSpacing(12)
        source_form.setVerticalSpacing(10)
        source_form.setColumnStretch(1, 1)
        self.image_source = QLineEdit(OFFICIAL_LITE_32_URL)
        self.image_source.setPlaceholderText("Resmi URL veya yerel .img/.zip/.xz dosyasi")
        image_browse = QPushButton("Dosya sec")
        image_browse.clicked.connect(self.browse_image)
        source_form.addWidget(QLabel("OS imaji"), 0, 0)
        source_form.addWidget(self.image_source, 0, 1)
        source_form.addWidget(image_browse, 0, 2)

        self.disk_combo = QComboBox()
        self.disk_combo.setMinimumContentsLength(20)
        self.disk_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        refresh = QPushButton("Yenile")
        refresh.clicked.connect(self.refresh_disks)
        source_form.addWidget(QLabel("SD kart"), 1, 0)
        source_form.addWidget(self.disk_combo, 1, 1)
        source_form.addWidget(refresh, 1, 2)

        self.imager_path = QLineEdit()
        imager_browse = QPushButton("Sec")
        imager_browse.clicked.connect(self.browse_imager)
        source_form.addWidget(QLabel("Raspberry Pi Imager"), 2, 0)
        source_form.addWidget(self.imager_path, 2, 1)
        source_form.addWidget(imager_browse, 2, 2)
        install_imager = QPushButton("Imager'i indir")
        install_imager.setObjectName("linkButton")
        install_imager.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://www.raspberrypi.com/software/"))
        )
        source_form.addWidget(install_imager, 3, 1, alignment=Qt.AlignmentFlag.AlignLeft)

        device_group = QGroupBox("Oyun client ayarlari")
        device_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        device_form = QFormLayout(device_group)
        device_form.setHorizontalSpacing(18)
        device_form.setVerticalSpacing(10)
        self.screen_id = QSpinBox()
        self.screen_id.setRange(1, 8)
        self.screen_id.valueChanged.connect(self._sync_hostname)
        self.server = QLineEdit("192.168.1.10")
        self.server.setPlaceholderText("192.168.1.10 veya http://server:8078")
        self.serial_port = QComboBox()
        self.serial_port.setEditable(True)
        self.serial_port.addItems(["/dev/ttyUSB0", "/dev/ttyACM0"])
        self.serial_baud = QComboBox()
        self.serial_baud.setEditable(True)
        self.serial_baud.addItems(["9600", "115200", "57600"])
        self.resolution = QComboBox()
        self.resolution.addItem("1280 × 720 · Pi Zero onerilen", (1280, 720))
        self.resolution.addItem("1920 × 1080 · daha agir", (1920, 1080))
        self.target_fps = QSpinBox()
        self.target_fps.setRange(15, 60)
        self.target_fps.setValue(30)
        self.target_fps.valueChanged.connect(self._sync_min_fps)
        self.min_fps = QSpinBox()
        self.min_fps.setRange(15, 30)
        self.min_fps.setValue(24)
        self.playarea_enabled = QCheckBox("Pleksi / oynanabilir alan kirpmasini ac")
        self.playarea_enabled.setChecked(True)
        self.hostname = QLineEdit()
        device_form.addRow("Ekran numarasi", self.screen_id)
        device_form.addRow("Pi 4 server", self.server)
        device_form.addRow("Arduino seri port", self.serial_port)
        device_form.addRow("Arduino baud", self.serial_baud)
        device_form.addRow("Render cozunurlugu", self.resolution)
        device_form.addRow("Hedef FPS", self.target_fps)
        device_form.addRow("Adaptif kalite esigi", self.min_fps)
        device_form.addRow("", self.playarea_enabled)
        device_form.addRow("Hostname", self.hostname)

        access_group = QGroupBox("Wi-Fi ve cihaz erisimi")
        access_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        access_form = QFormLayout(access_group)
        access_form.setHorizontalSpacing(18)
        access_form.setVerticalSpacing(10)
        self.wifi_ssid = QLineEdit()
        self.wifi_password = QLineEdit()
        self.wifi_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.wifi_country = QComboBox()
        self.wifi_country.setEditable(True)
        self.wifi_country.addItems(["TR", "DE", "GB", "US"])
        self.username = QLineEdit("pi")
        self.user_password = QLineEdit()
        self.user_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.enable_ssh = QCheckBox("SSH servisini kur ve ac")
        self.enable_ssh.setChecked(True)
        access_form.addRow("Wi-Fi adi (SSID)", self.wifi_ssid)
        access_form.addRow("Wi-Fi sifresi", self.wifi_password)
        access_form.addRow("Wi-Fi ulke kodu", self.wifi_country)
        access_form.addRow("Linux kullanicisi", self.username)
        access_form.addRow("Linux sifresi", self.user_password)
        access_form.addRow("", self.enable_ssh)
        setup_tabs = QTabWidget()
        setup_tabs.setObjectName("setupTabs")
        setup_tabs.setDocumentMode(True)
        setup_tabs.setMinimumHeight(505)
        setup_tabs.addTab(source_group, "1   OS ve SD kart")
        setup_tabs.addTab(device_group, "2   Oyun client")
        setup_tabs.addTab(access_group, "3   Wi-Fi ve erisim")
        page.addWidget(setup_tabs)

        note = QLabel(
            "Karttaki tum veriler silinir. Yalnizca cikarilabilir diskler listelenir; "
            "Windows sistem diski araca ve Imager'a gore engellenir. Sifreler kaydedilmez."
        )
        note.setObjectName("warning")
        note.setWordWrap(True)
        page.addWidget(note)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("Hazir")
        page.addWidget(self.progress)

        actions = QHBoxLayout()
        self.prepare_button = QPushButton("SD karti hazirla")
        self.prepare_button.setObjectName("primary")
        self.prepare_button.clicked.connect(self.prepare_card)
        self.cancel_button = QPushButton("Iptal")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_process)
        actions.addWidget(self.prepare_button, 1)
        actions.addWidget(self.cancel_button)
        page.addLayout(actions)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setMinimumHeight(105)
        self.log.setPlaceholderText("Islem kaydi burada gorunecek.")
        page.addWidget(self.log)

    def _apply_style(self):
        self.setStyleSheet("""
            QMainWindow { background: #f4f6fa; }
            QScrollArea, QScrollArea > QWidget > QWidget {
                background: #f4f6fa;
                border: 0;
            }
            QWidget#page {
                background: #f4f6fa;
                color: #182230;
                font-size: 13px;
            }
            QLabel {
                background: transparent;
                color: #344054;
            }
            QLabel#title {
                color: #101828;
                font-size: 25px;
                font-weight: 700;
            }
            QLabel#subtitle {
                color: #667085;
                margin-bottom: 4px;
            }
            QLabel#banner {
                padding: 11px 13px;
                border-radius: 7px;
                background: #fff4e5;
                color: #8a4b08;
            }
            QLabel#warning {
                padding: 11px 13px;
                border: 1px solid #f0c36d;
                border-radius: 7px;
                background: #fffaf0;
                color: #6b4600;
            }
            QTabWidget#setupTabs::pane {
                top: -1px;
                background: #ffffff;
                border: 1px solid #d8dee8;
                border-radius: 0 9px 9px 9px;
            }
            QTabBar::tab {
                min-width: 145px;
                padding: 11px 17px;
                color: #667085;
                background: #e9edf3;
                border: 1px solid #d8dee8;
                border-bottom: 0;
            }
            QTabBar::tab:first {
                border-top-left-radius: 8px;
            }
            QTabBar::tab:last {
                border-top-right-radius: 8px;
            }
            QTabBar::tab:selected {
                color: #175cd3;
                background: #ffffff;
                font-weight: 700;
            }
            QTabBar::tab:hover:!selected {
                color: #344054;
                background: #f2f4f7;
            }
            QTabWidget#setupTabs QGroupBox {
                margin: 16px;
                border-color: #e4e7ec;
            }
            QGroupBox {
                color: #344054;
                background: #ffffff;
                border: 1px solid #d8dee8;
                border-radius: 10px;
                margin-top: 16px;
                padding: 18px 16px 16px 16px;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 14px;
                padding: 0 7px;
                color: #344054;
                background: #f4f6fa;
            }
            QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {
                min-height: 20px;
                padding: 7px 9px;
                color: #101828;
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                selection-color: #ffffff;
                selection-background-color: #2563eb;
            }
            QLineEdit:hover, QComboBox:hover, QSpinBox:hover {
                border-color: #98a2b3;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {
                border: 1px solid #2563eb;
            }
            QComboBox, QSpinBox {
                padding-right: 30px;
            }
            QComboBox::drop-down, QSpinBox::up-button, QSpinBox::down-button {
                width: 26px;
                background: #f8fafc;
                border-left: 1px solid #d8dee8;
            }
            QComboBox QAbstractItemView {
                color: #101828;
                background: #ffffff;
                selection-color: #ffffff;
                selection-background-color: #2563eb;
                border: 1px solid #cbd5e1;
            }
            QCheckBox {
                color: #344054;
                background: transparent;
                spacing: 8px;
                font-weight: 400;
            }
            QPushButton {
                min-height: 20px;
                padding: 8px 14px;
                color: #344054;
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
            }
            QPushButton:hover {
                color: #101828;
                background: #f8fafc;
                border-color: #98a2b3;
            }
            QPushButton:pressed { background: #eef2f6; }
            QPushButton:disabled {
                color: #98a2b3;
                background: #eef1f5;
                border-color: #dfe3e8;
            }
            QPushButton#primary {
                min-height: 24px;
                padding: 10px 18px;
                color: #ffffff;
                background: #1769e0;
                border: 0;
                font-weight: 700;
            }
            QPushButton#primary:hover { background: #1259c2; }
            QPushButton#primary:pressed { background: #0f4da8; }
            QPushButton#linkButton {
                color: #1769e0;
                background: transparent;
                border: 0;
                padding-left: 0;
            }
            QProgressBar {
                min-height: 22px;
                color: #344054;
                background: #e7ebf0;
                border: 0;
                border-radius: 6px;
                text-align: center;
            }
            QProgressBar::chunk {
                background: #1769e0;
                border-radius: 6px;
            }
            QScrollBar:vertical {
                width: 12px;
                margin: 2px;
                background: #eef1f5;
                border: 0;
            }
            QScrollBar::handle:vertical {
                min-height: 36px;
                background: #b7c0cc;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover { background: #98a2b3; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
    def _load_defaults(self):
        found = discover_imager()
        self.imager_path.setText(str(found) if found else "")
        self.screen_id.setValue(1)
        self._sync_hostname()
        if is_windows_admin():
            self.admin_banner.setText("Yonetici yetkisi aktif. Kart yazmaya hazir.")
            self.admin_banner.setStyleSheet("background:#eaf7ef;color:#176b3a;padding:10px 12px;border-radius:6px")
        else:
            self.admin_banner.setText(
                "Yonetici yetkisi yok. Yazma sirasinda uygulamayi start_windows.bat ile yeniden acin."
            )

    def _sync_min_fps(self, value: int):
        self.min_fps.setMaximum(value)
        if self.min_fps.value() > value:
            self.min_fps.setValue(value)

    def _sync_hostname(self):
        current = self.hostname.text().strip()
        if not current or re.fullmatch(r"polis-ekran-[1-8]", current):
            self.hostname.setText(f"polis-ekran-{self.screen_id.value()}")

    def browse_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Raspberry Pi OS imaji",
            "",
            "OS images (*.img *.zip *.xz *.gz *.zst);;Tum dosyalar (*)",
        )
        if path:
            self.image_source.setText(path)

    def browse_imager(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Raspberry Pi Imager", "", "Uygulama (*.exe);;Tum dosyalar (*)"
        )
        if path:
            self.imager_path.setText(path)

    def refresh_disks(self):
        self.disk_combo.clear()
        try:
            disks = list_removable_disks()
        except Exception as exc:
            self.disk_combo.addItem(f"Diskler okunamadi: {exc}", None)
            return
        if not disks:
            self.disk_combo.addItem("Guvenli cikarilabilir SD/USB disk bulunamadi", None)
            return
        for disk in disks:
            self.disk_combo.addItem(disk.display_name, disk)

    def _settings(self) -> ProvisionSettings:
        return ProvisionSettings(
            screen_id=self.screen_id.value(),
            server_address=self.server.text(),
            serial_port=self.serial_port.currentText().strip(),
            wifi_ssid=self.wifi_ssid.text(),
            wifi_password=self.wifi_password.text(),
            wifi_country=self.wifi_country.currentText().strip(),
            hostname=self.hostname.text().strip().lower(),
            username=self.username.text().strip(),
            user_password=self.user_password.text(),
            enable_ssh=self.enable_ssh.isChecked(),
            serial_baud=int(self.serial_baud.currentText()),
            render_width=int(self.resolution.currentData()[0]),
            render_height=int(self.resolution.currentData()[1]),
            target_fps=self.target_fps.value(),
            min_fps=self.min_fps.value(),
            playarea_enabled=self.playarea_enabled.isChecked(),
        )

    def _validate_job(self) -> tuple[ProvisionSettings, DiskDevice, Path]:
        settings = self._settings()
        settings.validate()
        disk = self.disk_combo.currentData()
        if not isinstance(disk, DiskDevice) or not disk.safe_for_imaging:
            raise ValueError("Guvenli bir SD kart secin.")
        imager = Path(self.imager_path.text().strip()).expanduser().resolve()
        if not imager.is_file():
            raise ValueError("Raspberry Pi Imager bulunamadi. Kurun veya EXE yolunu secin.")
        if os.name == "nt" and not is_windows_admin():
            raise PermissionError("Kart yazmak icin program yonetici olarak acilmali.")
        return settings, disk, imager

    def prepare_card(self):
        try:
            settings, disk, imager = self._validate_job()
        except (ValueError, PermissionError) as exc:
            QMessageBox.warning(self, "Hazirlanamadi", str(exc))
            return
        answer = QMessageBox.warning(
            self,
            "Kart tamamen silinecek",
            f"{disk.display_name}\n\nBu diskteki tum bolumler ve dosyalar kalici olarak silinecek.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Ok:
            return
        expected = f"SIL {disk.number}"
        typed, accepted = QInputDialog.getText(
            self,
            "Son guvenlik onayi",
            f"Devam etmek icin {expected} yazin:",
        )
        if not accepted or typed.strip() != expected:
            QMessageBox.information(self, "Iptal", "Disk yazma islemi baslatilmadi.")
            return

        try:
            script = build_first_run_script(settings, CLIENT_SOURCE)
            self.secret_temp = tempfile.TemporaryDirectory(prefix="polisoyunu-sd-")
            script_path = Path(self.secret_temp.name) / "firstrun.sh"
            script_path.write_text(script, encoding="utf-8", newline="\n")
            arguments = build_imager_arguments(
                self.image_source.text(), disk, script_path
            )
        except Exception as exc:
            self._cleanup_secret_temp()
            QMessageBox.critical(self, "Paketleme hatasi", str(exc))
            return

        self.log.clear()
        self.log.appendPlainText(f"Hedef: {disk.display_name}")
        self.log.appendPlainText(f"Ekran: {settings.screen_id} · Server: {settings.server_base_url}")
        self.log.appendPlainText("Client paketi SD imajina gomuldu. Imager baslatiliyor...")
        self.progress.setValue(0)
        self.progress.setFormat("Hazirlaniyor")
        self.prepare_button.setEnabled(False)
        self.cancel_button.setEnabled(True)

        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_process_output)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)
        self.process.start(str(imager), arguments)

    def _read_process_output(self):
        if not self.process:
            return
        output = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        output = ANSI_RE.sub("", output).replace("\r", "\n")
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            self.log.appendPlainText(line)
            progress = parse_progress(line)
            if progress:
                phase, percent = progress
                mapped = int(percent * 0.7) if phase == "Yaziliyor" else 70 + int(percent * 0.3)
                self.progress.setValue(mapped)
                self.progress.setFormat(f"{phase} · %{percent}")

    def _process_finished(self, exit_code: int, _status):
        success = exit_code == 0
        self.prepare_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        if success:
            self.progress.setValue(100)
            self.progress.setFormat("SD kart hazir")
            QMessageBox.information(
                self,
                "Tamamlandi",
                "SD kart yazildi ve dogrulandi. Karti Pi Zero 2 W'ye takip ilk kurulumu bekleyin. "
                "Ilk acilis internet hizina gore 5-15 dakika surebilir ve cihaz yeniden baslar.",
            )
        else:
            self.progress.setFormat("Islem basarisiz")
            QMessageBox.critical(
                self, "Imager hatasi", f"Raspberry Pi Imager {exit_code} koduyla kapandi. Logu kontrol edin."
            )
        self.process = None
        self._cleanup_secret_temp()
        self.refresh_disks()

    def _process_error(self, error):
        self.log.appendPlainText(f"Imager baslatma hatasi: {error}")

    def cancel_process(self):
        if not self.process:
            return
        if QMessageBox.question(
            self,
            "Yazmayi durdur",
            "Islem durdurulursa kart kullanilamaz durumda kalabilir. Durdurulsun mu?",
        ) == QMessageBox.StandardButton.Yes:
            self.process.kill()

    def _cleanup_secret_temp(self):
        if self.secret_temp:
            self.secret_temp.cleanup()
            self.secret_temp = None

    def closeEvent(self, event):
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.warning(self, "Islem suruyor", "Kart yazilirken program kapatilamaz.")
            event.ignore()
            return
        self._cleanup_secret_temp()
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Polis Oyunu SD Kart Hazirlama")
    app.setFont(QFont("Segoe UI", 10))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())