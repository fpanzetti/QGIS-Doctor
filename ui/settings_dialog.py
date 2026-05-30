"""
QGIS Doctor — Settings Dialog (Phase 3+4).

QDialog con due tab: AI Connection e Behaviour.
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QComboBox, QCheckBox, QPushButton,
    QFormLayout, QDialogButtonBox, QSizePolicy,
)
from PyQt5.QtCore import Qt


class SettingsDialog(QDialog):

    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self.settings = settings_manager
        self._test_worker = None

        self.setWindowTitle("QGIS Doctor — Settings")
        self.setMinimumWidth(460)
        self.resize(480, 360)

        self._build_ui()
        self._load_settings()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        tabs = QTabWidget()
        tabs.addTab(self._build_ai_tab(), "AI Connection")
        tabs.addTab(self._build_behaviour_tab(), "Behaviour")
        layout.addWidget(tabs)

        # OK / Cancel
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self._save_and_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _build_ai_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(10)

        # Provider
        self._combo_provider = QComboBox()
        self._combo_provider.addItem("Anthropic / Claude", "anthropic")
        self._combo_provider.addItem("OpenAI / ChatGPT",   "openai")
        self._combo_provider.addItem("Google / Gemini",    "google")
        self._combo_provider.addItem("Custom (OpenAI-compatible)", "custom")
        self._combo_provider.currentIndexChanged.connect(self._on_provider_changed)
        form.addRow("Provider:", self._combo_provider)

        # API Key
        self._edit_api_key = QLineEdit()
        self._edit_api_key.setEchoMode(QLineEdit.Password)
        self._edit_api_key.setPlaceholderText("Paste your API key here")
        form.addRow("API Key:", self._edit_api_key)

        # Model
        self._edit_model = QLineEdit()
        self._edit_model.setPlaceholderText("(default for provider)")
        form.addRow("Model:", self._edit_model)

        # Custom endpoint (only for "custom")
        self._lbl_endpoint = QLabel("Custom endpoint:")
        self._edit_endpoint = QLineEdit()
        self._edit_endpoint.setPlaceholderText("https://your-endpoint/v1/chat/completions")
        form.addRow(self._lbl_endpoint, self._edit_endpoint)

        # Test connection
        self._btn_test = QPushButton("Test Connection")
        self._btn_test.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._btn_test.clicked.connect(self._test_connection)
        self._label_test_result = QLabel("")
        self._label_test_result.setStyleSheet("color: #555; font-size: 11px;")
        test_row = QHBoxLayout()
        test_row.addWidget(self._btn_test)
        test_row.addWidget(self._label_test_result)
        test_row.addStretch()
        test_widget = QWidget()
        test_widget.setLayout(test_row)
        form.addRow("", test_widget)

        return widget

    def _build_behaviour_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(10)

        # Language
        self._combo_lang = QComboBox()
        self._combo_lang.addItem("English", "en")
        self._combo_lang.addItem("Italiano", "it")
        form.addRow("Language:", self._combo_lang)

        # Online lookup
        self._chk_online = QCheckBox("Enable online lookup (GitHub + GIS StackExchange)")
        self._chk_online.setToolTip(
            "When enabled, QGIS Doctor searches online for known bug reports "
            "matching the issues found in your project."
        )
        form.addRow("", self._chk_online)

        # GitHub token
        self._edit_gh_token = QLineEdit()
        self._edit_gh_token.setEchoMode(QLineEdit.Password)
        self._edit_gh_token.setPlaceholderText("(optional — increases rate limit)")
        form.addRow("GitHub token:", self._edit_gh_token)

        # Show welcome
        self._chk_welcome = QCheckBox("Show welcome screen on startup")
        form.addRow("", self._chk_welcome)

        return widget

    # ── Load / Save ───────────────────────────────────────────────────────────

    def _load_settings(self):
        s = self.settings

        # Provider
        provider = s.get_provider()
        for i in range(self._combo_provider.count()):
            if self._combo_provider.itemData(i) == provider:
                self._combo_provider.setCurrentIndex(i)
                break

        self._edit_api_key.setText(s.get_api_key())
        self._edit_model.setText(s.get_model())
        self._edit_endpoint.setText(s.get_custom_endpoint())

        # Language
        lang = s.get_language()
        for i in range(self._combo_lang.count()):
            if self._combo_lang.itemData(i) == lang:
                self._combo_lang.setCurrentIndex(i)
                break

        self._chk_online.setChecked(s.get_online_lookup_enabled())
        self._edit_gh_token.setText(s.get_github_token())
        self._chk_welcome.setChecked(s.get_show_welcome())

        self._on_provider_changed()

    def _save_and_accept(self):
        s = self.settings
        s.set_provider(self._combo_provider.currentData())
        s.set_api_key(self._edit_api_key.text().strip())
        s.set_model(self._edit_model.text().strip())
        s.set_custom_endpoint(self._edit_endpoint.text().strip())
        s.set_language(self._combo_lang.currentData())
        s.set_online_lookup_enabled(self._chk_online.isChecked())
        s.set_github_token(self._edit_gh_token.text().strip())
        s.set_show_welcome(self._chk_welcome.isChecked())
        self.accept()

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_provider_changed(self):
        provider = self._combo_provider.currentData()
        is_custom = (provider == "custom")
        self._lbl_endpoint.setVisible(is_custom)
        self._edit_endpoint.setVisible(is_custom)

        # Update model placeholder
        from ..llm_connector import PROVIDER_DEFAULTS
        default = PROVIDER_DEFAULTS.get(provider, "")
        self._edit_model.setPlaceholderText(
            f"default: {default}" if default else "(leave blank for provider default)"
        )

    def _test_connection(self):
        api_key = self._edit_api_key.text().strip()
        if not api_key:
            self._label_test_result.setText("⚠ Enter an API key first")
            self._label_test_result.setStyleSheet("color: #e67e22; font-size: 11px;")
            return

        self._btn_test.setEnabled(False)
        self._btn_test.setText("Testing…")
        self._label_test_result.setText("Connecting…")
        self._label_test_result.setStyleSheet("color: #555; font-size: 11px;")

        # Build a temporary settings-like object from current dialog state
        provider = self._combo_provider.currentData()
        model = self._edit_model.text().strip()
        endpoint = self._edit_endpoint.text().strip() if provider == "custom" else ""

        from ..llm_connector import TestConnectionWorker
        self._test_worker = TestConnectionWorker(provider, api_key, model, endpoint)
        self._test_worker.finished.connect(self._on_test_done)
        self._test_worker.error.connect(self._on_test_error)
        self._test_worker.start()

    def _on_test_done(self, response: str):
        self._btn_test.setEnabled(True)
        self._btn_test.setText("Test Connection")
        self._label_test_result.setText("✅ Connection OK")
        self._label_test_result.setStyleSheet("color: #1a7a1a; font-size: 11px;")

    def _on_test_error(self, error: str):
        self._btn_test.setEnabled(True)
        self._btn_test.setText("Test Connection")
        short = error[:80] + ("…" if len(error) > 80 else "")
        self._label_test_result.setText(f"❌ {short}")
        self._label_test_result.setStyleSheet("color: #c0392b; font-size: 11px;")
