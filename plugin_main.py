"""
QGIS Doctor — Main plugin class.
Registra menu, toolbar e dock. Accumula il log di sistema.
"""

import os

from PyQt5.QtWidgets import QAction
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import Qt

try:
    from qgis.core import QgsApplication
    _HAS_QGIS = True
except ImportError:
    _HAS_QGIS = False

_PLUGIN_DIR = os.path.dirname(__file__)


def _icon(name: str) -> QIcon:
    return QIcon(os.path.join(_PLUGIN_DIR, "icons", name))


class QGISDoctorPlugin:

    def __init__(self, iface):
        self.iface = iface
        self._dock = None
        self._action_run = None
        self._log_buffer = []   # list of (tag, level, message)

        # SettingsManager disponibile per tutto il plugin
        from .settings_manager import SettingsManager
        self.settings = SettingsManager()

        # Accumula i messaggi del log QGIS fin dall'avvio
        if _HAS_QGIS:
            try:
                QgsApplication.messageLog().messageReceived.connect(
                    self._on_log_message)
            except Exception:
                pass

    def initGui(self):
        self._action_run = QAction(
            _icon("icon.svg"),
            "QGIS Doctor",
            self.iface.mainWindow()
        )
        self._action_run.setToolTip("Open QGIS Doctor diagnostic panel")
        self._action_run.triggered.connect(self._open_dock)
        self.iface.addPluginToMenu("QGIS Doctor", self._action_run)
        self.iface.addToolBarIcon(self._action_run)

    def unload(self):
        self.iface.removePluginMenu("QGIS Doctor", self._action_run)
        self.iface.removeToolBarIcon(self._action_run)
        if self._dock:
            self.iface.removeDockWidget(self._dock)
            self._dock.deleteLater()
            self._dock = None
        if _HAS_QGIS:
            try:
                QgsApplication.messageLog().messageReceived.disconnect(
                    self._on_log_message)
            except Exception:
                pass

    def _open_dock(self):
        if self._dock is None:
            from .ui.dock_widget import QGISDoctorDock
            self._dock = QGISDoctorDock(self.iface, self)
            self.iface.addDockWidget(Qt.RightDockWidgetArea, self._dock)

        self._dock.show()
        self._dock.raise_()

    def _on_log_message(self, message: str, tag: str, level: int):
        """Accumula messaggi del log QGIS (ultimi 200)."""
        self._log_buffer.append((tag, level, message))
        if len(self._log_buffer) > 200:
            self._log_buffer = self._log_buffer[-200:]
