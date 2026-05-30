"""
QGIS Doctor — Dock widget (Phase 3+4 rewrite).

Struttura:
  [▶ Run] [📋 Copy for AI] [🐛 Report Bug] [⚙ Settings] [?]
  --- progress bar (visibile solo durante run) ---
  --- area risultati HTML ---
  --- footer AI ---
  --- area risposta AI (collassabile) ---
"""

import webbrowser

from PyQt5.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTextEdit, QTextBrowser, QLabel, QSizePolicy, QApplication,
    QProgressBar, QMenu, QAction, QFrame, QScrollArea,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt5.QtGui import QFont, QDesktopServices


_WELCOME_TEXT = """\
<html>
<body style="font-family:system-ui,sans-serif; font-size:13px; margin:16px; color:#222;">

<h2 style="color:#2c3e50;">Welcome to QGIS Doctor</h2>
<p>Something in your project not working the way you expect?<br>
This tool is here to help you figure out why.</p>

<p>QGIS problems usually fall into a few categories:</p>
<ul>
  <li>Something you configured in a way that conflicts with something else
      — without QGIS telling you clearly that there is a problem.</li>
  <li>A data issue: a missing file, a broken path, an empty layer,
      a coordinate system that does not match the project.</li>
  <li>A known bug in QGIS or a plugin — something the developers
      already know about, or something no one has reported yet.</li>
</ul>

<p>QGIS Doctor reads the live state of your running program —
not just the project file — and tells you what it finds, in plain language.</p>

<hr style="border:1px solid #ddd; margin:16px 0;">

<p><b>💡 Get much more with an AI connection</b></p>
<p>QGIS Doctor can connect to Claude, ChatGPT, Gemini, or any
compatible AI assistant via API key.</p>
<p>No API key? Use the <b>Copy for AI</b> button to paste your report
into any chatbot manually.</p>

<hr style="border:1px solid #ddd; margin:16px 0;">

<p style="text-align:center; font-size:14px; color:#555;">
Press <b>▶ Run Diagnostics</b> to start.</p>

</body>
</html>
"""


class _DiagnosticsWorker(QThread):
    """Esegue la diagnostica in background.

    NOTA: il segnale custom è 'results_ready', NON 'finished'.
    QThread ha già un segnale built-in 'finished()' — sovrascriverlo
    causa un crash (QThread::~QThread while thread still running)
    perché il GC Python distrugge l'oggetto prima che il thread C++
    abbia completato il cleanup interno.
    """
    results_ready = pyqtSignal(list, dict)   # era 'finished' — rinominato
    error = pyqtSignal(str)
    progress = pyqtSignal(str)   # nome modulo corrente

    def __init__(self, engine):
        super().__init__()
        self.engine = engine

    def run(self):
        try:
            issues = self.engine.run_all(
                progress_callback=lambda name: self.progress.emit(name)
            )
            env = self.engine.get_environment_summary()
            self.results_ready.emit(issues, env)
        except Exception as e:
            self.error.emit(str(e))


class QGISDoctorDock(QDockWidget):

    def __init__(self, iface, plugin):
        super().__init__("QGIS Doctor", iface.mainWindow())
        self.iface = iface
        self.plugin = plugin
        self._last_issues = []
        self._last_env = {}
        self._worker = None
        self._ai_worker = None
        self._selected_issue = None  # issue selezionata dall'utente nel report

        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.setMinimumWidth(340)

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # ── Toolbar ──────────────────────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)

        self._btn_run = QPushButton("▶ Run")
        self._btn_run.setToolTip("Run diagnostic analysis")
        self._btn_run.clicked.connect(self._run_diagnostics)
        self._btn_run.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self._btn_copy = QPushButton("📋 Copy for AI")
        self._btn_copy.setToolTip("Copy diagnostic report to clipboard (paste into any AI chatbot)")
        self._btn_copy.clicked.connect(self._copy_for_ai)
        self._btn_copy.setEnabled(False)

        self._btn_bug = QPushButton("🐛 Report Bug")
        self._btn_bug.setToolTip("Report the selected issue as a QGIS bug on GitHub")
        self._btn_bug.clicked.connect(self._report_selected_bug)
        self._btn_bug.setEnabled(False)

        self._btn_settings = QPushButton("⚙")
        self._btn_settings.setToolTip("Open settings")
        self._btn_settings.setFixedWidth(28)
        self._btn_settings.clicked.connect(self._open_settings)

        self._btn_help = QPushButton("?")
        self._btn_help.setToolTip("Show welcome screen")
        self._btn_help.setFixedWidth(28)
        self._btn_help.clicked.connect(self._show_welcome)

        toolbar.addWidget(self._btn_run)
        toolbar.addWidget(self._btn_copy)
        toolbar.addWidget(self._btn_bug)
        toolbar.addWidget(self._btn_settings)
        toolbar.addWidget(self._btn_help)

        # ── Progress bar ──────────────────────────────────────────────────────
        self._progress_widget = QWidget()
        progress_layout = QHBoxLayout(self._progress_widget)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(6)
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)  # indeterminate
        self._progress_bar.setFixedHeight(8)
        self._progress_bar.setTextVisible(False)
        self._progress_label = QLabel("Initialising…")
        self._progress_label.setStyleSheet("color:#555; font-size:11px;")
        progress_layout.addWidget(self._progress_bar)
        progress_layout.addWidget(self._progress_label)
        self._progress_widget.setVisible(False)

        # ── Status label ──────────────────────────────────────────────────────
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #666; font-size: 11px;")

        # ── Results area ─────────────────────────────────────────────────────
        # QTextBrowser invece di QTextEdit: ha anchorClicked e gestione link
        self._text = QTextBrowser()
        self._text.setFont(QFont("system-ui", 12))
        self._text.setOpenLinks(False)          # gestiamo noi i click
        self._text.anchorClicked.connect(self._on_anchor_clicked)

        # ── AI footer ────────────────────────────────────────────────────────
        self._ai_footer = QWidget()
        ai_layout = QHBoxLayout(self._ai_footer)
        ai_layout.setContentsMargins(0, 4, 0, 0)
        ai_layout.setSpacing(6)

        self._btn_send_ai = QPushButton("🤖 Send to AI ▾")
        self._btn_send_ai.setToolTip("Send report to AI assistant")
        self._btn_send_ai.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._btn_send_ai.clicked.connect(self._show_send_ai_menu)

        self._btn_ask_ai = QPushButton("Ask AI about selected")
        self._btn_ask_ai.setToolTip("Ask AI about the selected issue (click an issue first)")
        self._btn_ask_ai.setEnabled(False)
        self._btn_ask_ai.clicked.connect(self._ask_ai_selected)

        self._btn_configure_ai = QPushButton("Configure AI →")
        self._btn_configure_ai.setToolTip("Set up your AI API key")
        self._btn_configure_ai.clicked.connect(self._open_settings)

        ai_layout.addWidget(self._btn_send_ai)
        ai_layout.addWidget(self._btn_ask_ai)
        ai_layout.addWidget(self._btn_configure_ai)
        ai_layout.addStretch()

        # ── AI response area ──────────────────────────────────────────────────
        self._ai_response_widget = QWidget()
        ai_resp_layout = QVBoxLayout(self._ai_response_widget)
        ai_resp_layout.setContentsMargins(0, 4, 0, 0)
        ai_resp_layout.setSpacing(2)

        ai_resp_header = QHBoxLayout()
        lbl_ai = QLabel("🤖 AI Response")
        lbl_ai.setStyleSheet("font-weight:bold; font-size:12px; color:#2c3e50;")
        self._btn_clear_ai = QPushButton("✕")
        self._btn_clear_ai.setFixedWidth(22)
        self._btn_clear_ai.setFixedHeight(20)
        self._btn_clear_ai.setToolTip("Clear AI response")
        self._btn_clear_ai.clicked.connect(self._clear_ai_response)
        ai_resp_header.addWidget(lbl_ai)
        ai_resp_header.addStretch()
        ai_resp_header.addWidget(self._btn_clear_ai)

        self._ai_text = QTextEdit()
        self._ai_text.setReadOnly(True)
        self._ai_text.setFont(QFont("system-ui", 11))
        self._ai_text.setMaximumHeight(200)
        self._ai_text.setStyleSheet(
            "background:#f8f9fa; border:1px solid #ddd; border-radius:3px;"
        )

        ai_resp_layout.addLayout(ai_resp_header)
        ai_resp_layout.addWidget(self._ai_text)
        self._ai_response_widget.setVisible(False)

        # ── Assemble ──────────────────────────────────────────────────────────
        layout.addLayout(toolbar)
        layout.addWidget(self._progress_widget)
        layout.addWidget(self._status_label)
        layout.addWidget(self._text)
        layout.addWidget(self._ai_footer)
        layout.addWidget(self._ai_response_widget)

        self.setWidget(container)
        self._refresh_ai_footer()
        self._show_welcome()

    # ── AI footer visibility ──────────────────────────────────────────────────

    def _refresh_ai_footer(self):
        """Mostra/nasconde i pulsanti AI in base alla configurazione."""
        has_key = self.plugin.settings.has_api_key()
        self._btn_send_ai.setVisible(has_key)
        self._btn_ask_ai.setVisible(has_key)
        self._btn_configure_ai.setVisible(not has_key)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _show_welcome(self):
        self._text.setHtml(_WELCOME_TEXT)
        self._status_label.setText("")

    def _run_diagnostics(self):
        if self._worker and self._worker.isRunning():
            return

        self._btn_run.setEnabled(False)
        self._btn_run.setText("⏳ Running…")
        self._status_label.setText("")
        self._progress_widget.setVisible(True)
        self._progress_label.setText("Starting…")
        self._selected_issue = None
        self._btn_ask_ai.setEnabled(False)
        self._btn_bug.setEnabled(False)

        self._text.setHtml(
            "<html><body style='font-family:system-ui,sans-serif; font-size:13px; "
            "margin:24px; text-align:center; color:#555;'>"
            "<p style='font-size:30px;'>⏳</p><p>Running diagnostics…</p></body></html>"
        )

        from ..diagnostics_engine import DiagnosticsEngine
        engine = DiagnosticsEngine(self.iface, self.plugin._log_buffer)

        self._worker = _DiagnosticsWorker(engine)
        self._worker.results_ready.connect(self._on_diagnostics_done)
        self._worker.error.connect(self._on_diagnostics_error)
        self._worker.progress.connect(self._on_progress)
        # Il segnale built-in QThread.finished() è emesso SOLO dopo che il
        # thread C++ ha completato il cleanup. Colleghiamo lì il deleteLater
        # per evitare il crash "QThread destroyed while still running".
        # _on_worker_thread_finished azzera anche self._worker in modo sicuro.
        self._worker.finished.connect(self._on_worker_thread_finished)
        self._worker.start()

    def _on_progress(self, module_name: str):
        self._progress_label.setText(f"Analysing {module_name}…")

    def _on_worker_thread_finished(self):
        """Slot connesso al segnale built-in QThread.finished().
        Viene chiamato SOLO dopo che il thread C++ ha terminato completamente —
        quindi è il momento sicuro per distruggere l'oggetto.
        """
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _on_diagnostics_done(self, issues: list, env: dict):
        self._last_issues = issues
        self._last_env = env
        # NON toccare self._worker qui: il thread C++ potrebbe non aver ancora
        # completato il cleanup. La pulizia avviene in _on_worker_thread_finished,
        # connesso al segnale built-in QThread.finished() che scatta dopo run().

        self._btn_run.setEnabled(True)
        self._btn_run.setText("▶ Run")
        self._progress_widget.setVisible(False)
        self._btn_copy.setEnabled(bool(issues))

        n_err  = sum(1 for i in issues if i.severity == "ERROR")
        n_warn = sum(1 for i in issues if i.severity == "WARNING")
        n_info = sum(1 for i in issues if i.severity == "INFO")

        if not issues:
            self._status_label.setText("✅ No issues found")
        else:
            parts = []
            if n_err:
                parts.append(f"{n_err} error{'s' if n_err > 1 else ''}")
            if n_warn:
                parts.append(f"{n_warn} warning{'s' if n_warn > 1 else ''}")
            if n_info:
                parts.append(f"{n_info} info")
            self._status_label.setText("Found: " + ", ".join(parts))

        from ..report_builder import build_report_html
        self._text.setHtml(build_report_html(issues, env))

        # Start knowledge lookup if enabled
        if self.plugin.settings.get_online_lookup_enabled():
            known_bug_issues = [i for i in issues if i.issue_type == "known_bug"]
            if known_bug_issues:
                self._start_knowledge_lookup(known_bug_issues)

    def _on_diagnostics_error(self, msg: str):
        # self._worker sarà azzerato da _on_worker_thread_finished
        self._btn_run.setEnabled(True)
        self._btn_run.setText("▶ Run")
        self._progress_widget.setVisible(False)
        self._status_label.setText("⚠ Diagnostics failed")
        self._text.setHtml(
            f"<html><body style='font-family:system-ui,sans-serif; font-size:13px; "
            f"margin:16px; color:#c0392b;'>"
            f"<h3>Diagnostics failed</h3><pre>{msg}</pre></body></html>"
        )

    def _copy_for_ai(self):
        if not self._last_issues and not self._last_env:
            return
        from ..report_builder import build_clipboard_markdown
        md = build_clipboard_markdown(self._last_issues, self._last_env)
        QApplication.clipboard().setText(md)
        self._status_label.setText("✅ Copied to clipboard — paste into your AI assistant")

    def _open_settings(self):
        from .settings_dialog import SettingsDialog
        dlg = SettingsDialog(self.plugin.settings, parent=self)
        if dlg.exec_():
            self._refresh_ai_footer()

    # ── Anchor click handler ──────────────────────────────────────────────────

    def _on_anchor_clicked(self, url):
        href = url.toString()

        if href.startswith("qgisdoctor://select/"):
            issue_id = href.replace("qgisdoctor://select/", "")
            self._select_issue_by_id(issue_id)

        elif href.startswith("qgisdoctor://fix/"):
            parts = href.replace("qgisdoctor://fix/", "").split("/", 1)
            issue_id = parts[0]
            layer_name = parts[1] if len(parts) > 1 else ""
            self._auto_fix_issue(issue_id, layer_name)

        elif href.startswith("qgisdoctor://report_bug/"):
            issue_id = href.replace("qgisdoctor://report_bug/", "")
            self._report_bug_by_id(issue_id)

        elif href.startswith("http://") or href.startswith("https://"):
            QDesktopServices.openUrl(QUrl(href))

    def _select_issue_by_id(self, issue_id: str):
        issue = next((i for i in self._last_issues if i.id == issue_id), None)
        if issue:
            self._selected_issue = issue
            has_key = self.plugin.settings.has_api_key()
            self._btn_ask_ai.setEnabled(has_key)
            self._btn_bug.setEnabled(issue.issue_type == "known_bug")
            self._status_label.setText(
                f"Selected: [{issue.id}] {issue.title[:50]}"
            )

    def _auto_fix_issue(self, issue_id: str, layer_name: str):
        """Esegue il fix automatico per issue auto_fixable."""
        try:
            from qgis.core import QgsProject
            project = QgsProject.instance()
            if issue_id == "LAY-11" and layer_name:
                for layer in project.mapLayers().values():
                    if layer.name() == layer_name and layer.isEditable():
                        layer.commitChanges()
                        self._status_label.setText(f"✅ Saved edits for '{layer_name}'")
                        return
            self._status_label.setText(f"⚠ Auto-fix not available for {issue_id}")
        except Exception as e:
            self._status_label.setText(f"⚠ Fix failed: {e}")

    def _report_selected_bug(self):
        if self._selected_issue:
            self._report_bug_by_id(self._selected_issue.id)

    def _report_bug_by_id(self, issue_id: str):
        issue = next((i for i in self._last_issues if i.id == issue_id), None)
        if not issue:
            return
        try:
            from ..bug_reporter import build_github_issue_url
            url = build_github_issue_url(issue, self._last_env)
            QDesktopServices.openUrl(QUrl(url))
        except Exception as e:
            self._status_label.setText(f"⚠ Could not open browser: {e}")

    # ── AI actions ────────────────────────────────────────────────────────────

    def _show_send_ai_menu(self):
        menu = QMenu(self)
        act_full = QAction("Send full report", self)
        act_full.triggered.connect(lambda: self._send_to_ai(errors_only=False))
        act_errors = QAction("Send errors only", self)
        act_errors.triggered.connect(lambda: self._send_to_ai(errors_only=True))
        menu.addAction(act_full)
        menu.addAction(act_errors)
        menu.exec_(self._btn_send_ai.mapToGlobal(
            self._btn_send_ai.rect().bottomLeft()
        ))

    def _send_to_ai(self, errors_only: bool = False):
        if not self._last_issues:
            self._status_label.setText("⚠ Run diagnostics first")
            return
        from ..llm_connector import build_report_prompt
        prompt = build_report_prompt(self._last_issues, self._last_env, errors_only)
        self._start_ai_request(prompt)

    def _ask_ai_selected(self):
        if not self._selected_issue:
            self._status_label.setText("⚠ Click an issue in the report first")
            return
        from ..llm_connector import build_ask_prompt
        prompt = build_ask_prompt(self._selected_issue, self._last_env)
        self._start_ai_request(prompt)

    def _start_ai_request(self, prompt: str):
        if not self.plugin.settings.has_api_key():
            self._status_label.setText("⚠ Configure an AI API key in Settings")
            return

        # Cancel any running request
        if self._ai_worker and self._ai_worker.isRunning():
            self._ai_worker.cancel()
            self._ai_worker.quit()

        self._ai_response_widget.setVisible(True)
        self._ai_text.setPlainText("⏳ Waiting for AI response…")
        self._btn_send_ai.setEnabled(False)
        self._btn_ask_ai.setEnabled(False)

        from ..llm_connector import LLMConnector
        connector = LLMConnector(self.plugin.settings)
        self._ai_worker = connector.send_request(
            prompt,
            on_done=self._on_ai_done,
            on_error=self._on_ai_error,
        )

    def _on_ai_done(self, response: str):
        self._ai_text.setPlainText(response)
        self._btn_send_ai.setEnabled(True)
        if self._selected_issue:
            self._btn_ask_ai.setEnabled(True)
        self._status_label.setText("✅ AI response received")

    def _on_ai_error(self, error: str):
        self._ai_text.setPlainText(f"❌ AI Error:\n{error}")
        self._btn_send_ai.setEnabled(True)
        if self._selected_issue:
            self._btn_ask_ai.setEnabled(True)
        self._status_label.setText("⚠ AI request failed")

    def _clear_ai_response(self):
        self._ai_response_widget.setVisible(False)
        self._ai_text.clear()

    # ── Knowledge lookup ──────────────────────────────────────────────────────

    def _start_knowledge_lookup(self, issues: list):
        try:
            from ..knowledge_lookup import KnowledgeLookupWorker
            github_token = self.plugin.settings.get_github_token()
            worker = KnowledgeLookupWorker(issues, github_token)
            worker.result_ready.connect(self._on_lookup_result)
            worker.log.connect(lambda msg: None)  # silently log
            # Keep reference to avoid GC
            self._lookup_worker = worker
            worker.start()
        except Exception:
            pass

    def _on_lookup_result(self, issue_id: str, refs: list):
        """Aggiorna le known_issues dell'issue nel report."""
        try:
            for issue in self._last_issues:
                if issue.id == issue_id:
                    issue.known_issues = refs
                    break
            # Refresh HTML report
            from ..report_builder import build_report_html
            self._text.setHtml(build_report_html(self._last_issues, self._last_env))
        except Exception:
            pass
