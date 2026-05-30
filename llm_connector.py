"""
QGIS Doctor — LLM Connector (Phase 3).

Supporta provider: anthropic, openai, google, custom.
Tutte le chiamate di rete avvengono in QThread separato.
"""

import json
import urllib.request
import urllib.error
from urllib.parse import urlencode

from PyQt5.QtCore import QThread, pyqtSignal


# ── Default models per provider ──────────────────────────────────────────────

PROVIDER_DEFAULTS = {
    "anthropic": "claude-opus-4-5",
    "openai":    "gpt-4o",
    "google":    "gemini-1.5-pro",
    "custom":    "",
}

PROVIDER_ENDPOINTS = {
    "anthropic": "https://api.anthropic.com/v1/messages",
    "openai":    "https://api.openai.com/v1/chat/completions",
    "google":    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
    "custom":    "",
}


# ── Prompt builders ───────────────────────────────────────────────────────────

def build_ask_prompt(issue, env: dict) -> str:
    """Prompt for 'Ask AI about selected' — single issue."""
    return f"""You are an expert QGIS consultant. A user has a specific issue in their QGIS project and needs help.

## User's QGIS Environment
- QGIS version: {env.get('qgis_version', 'unknown')}
- OS: {env.get('os', 'unknown')}
- Project CRS: {env.get('project_crs', 'unknown')}
- Number of layers: {env.get('n_layers', 'unknown')}
- Snapping: {env.get('snapping', 'unknown')}

## Issue Detected
**ID:** {issue.id}
**Category:** {issue.category}
**Severity:** {issue.severity}
**Type:** {issue.issue_type}
**Title:** {issue.title}
{f'**Layer:** {issue.layer_name}' if issue.layer_name else ''}

**Description:**
{issue.explanation}

**Current suggestion:**
{issue.suggestion}

**Technical detail:**
{issue.technical_detail}

## Your Task
Please provide:
1. A clear explanation of what this issue means and why it matters for this user's specific project
2. Step-by-step instructions to fix it (be specific to their environment)
3. How to verify the fix worked
4. Any related pitfalls to avoid

Be practical and direct. The user is likely a GIS professional, archaeologist, or researcher — not necessarily a software developer."""


def build_report_prompt(issues: list, env: dict, errors_only: bool = False) -> str:
    """Prompt for 'Send to AI' — full report or errors only."""
    if errors_only:
        filtered = [i for i in issues if i.severity == "ERROR"]
        title = "errors only"
    else:
        filtered = issues
        title = "full diagnostic report"

    issue_lines = []
    for issue in filtered:
        layer_str = f"\n  Layer: {issue.layer_name}" if issue.layer_name else ""
        issue_lines.append(
            f"[{issue.severity}] {issue.id} — {issue.title}{layer_str}\n"
            f"  Type: {issue.issue_type}\n"
            f"  {issue.explanation[:300]}{'...' if len(issue.explanation) > 300 else ''}"
        )

    issues_block = "\n\n".join(issue_lines) if issue_lines else "No issues found."

    active_plugins = ", ".join(env.get("active_plugins", [])[:10]) or "none listed"

    return f"""You are an expert QGIS consultant. Analyze this QGIS Doctor diagnostic report ({title}) and help the user fix their project.

## QGIS Environment
- Version: {env.get('qgis_version', 'unknown')}
- OS: {env.get('os', 'unknown')}
- Project: {env.get('project_name', 'unknown')}
- Project CRS: {env.get('project_crs', 'unknown')}
- Layers: {env.get('n_layers', 0)}
- Snapping: {env.get('snapping', 'unknown')}
- Active plugins: {active_plugins}

## Issues Found ({len(filtered)} total)

{issues_block}

## Your Task
1. Start with a brief overall assessment (2–3 sentences)
2. List the issues in priority order (most critical first), with:
   - Why this issue matters in this specific project context
   - Step-by-step fix instructions
   - How to verify the fix
3. If multiple issues are related (e.g., all CRS problems), group them and suggest a unified approach
4. End with a "Quick wins" section — issues that take under 2 minutes to fix

Be practical, direct, and specific to this user's environment. Avoid generic QGIS documentation — explain it as if talking to a colleague sitting next to you."""


# ── Worker thread ─────────────────────────────────────────────────────────────

class LLMWorker(QThread):
    """Esegue la chiamata API in background. Mai bloccare il main thread."""

    chunk_received = pyqtSignal(str)   # testo parziale (streaming se supportato)
    finished = pyqtSignal(str)         # testo completo
    error = pyqtSignal(str)            # messaggio di errore

    def __init__(self, provider: str, api_key: str, model: str,
                 prompt: str, endpoint: str = ""):
        super().__init__()
        self.provider = provider
        self.api_key = api_key
        self.model = model or PROVIDER_DEFAULTS.get(provider, "")
        self.prompt = prompt
        self.endpoint = endpoint or PROVIDER_ENDPOINTS.get(provider, "")
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            response = self._call_api()
            if not self._cancelled:
                self.finished.emit(response)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            if e.code == 401:
                self.error.emit(f"Authentication failed (HTTP 401). Check your API key.\n{body}")
            elif e.code == 429:
                self.error.emit(f"Rate limit exceeded (HTTP 429). Try again later.\n{body}")
            elif e.code == 404:
                self.error.emit(f"Model not found (HTTP 404). Check the model name: {self.model}\n{body}")
            else:
                self.error.emit(f"HTTP {e.code}: {e.reason}\n{body}")
        except urllib.error.URLError as e:
            self.error.emit(f"Network error: {e.reason}. Check your internet connection.")
        except Exception as e:
            self.error.emit(f"Unexpected error: {e}")

    def _call_api(self) -> str:
        if self.provider == "anthropic":
            return self._call_anthropic()
        elif self.provider == "openai":
            return self._call_openai()
        elif self.provider == "google":
            return self._call_google()
        else:
            return self._call_openai_compatible()

    def _call_anthropic(self) -> str:
        payload = json.dumps({
            "model": self.model,
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": self.prompt}],
        }).encode("utf-8")

        req = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["content"][0]["text"]

    def _call_openai(self) -> str:
        payload = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": self.prompt}],
            "max_tokens": 2048,
        }).encode("utf-8")

        req = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]

    def _call_google(self) -> str:
        url = self.endpoint.replace("{model}", self.model)
        url = f"{url}?key={self.api_key}"
        payload = json.dumps({
            "contents": [{"parts": [{"text": self.prompt}]}],
            "generationConfig": {"maxOutputTokens": 2048},
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["candidates"][0]["content"]["parts"][0]["text"]

    def _call_openai_compatible(self) -> str:
        """Chiamata generica compatibile con OpenAI (custom endpoint)."""
        if not self.endpoint:
            raise ValueError("Custom endpoint not configured.")
        payload = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": self.prompt}],
            "max_tokens": 2048,
        }).encode("utf-8")

        req = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        # Try OpenAI format first
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            try:
                return data["content"][0]["text"]
            except (KeyError, IndexError):
                return str(data)


# ── Test connection helper ────────────────────────────────────────────────────

class TestConnectionWorker(LLMWorker):
    """Invia un prompt minimale per verificare che le credenziali funzionino."""

    def __init__(self, provider: str, api_key: str, model: str, endpoint: str = ""):
        test_prompt = "Reply with exactly the word: OK"
        super().__init__(provider, api_key, model, test_prompt, endpoint)


# ── Public connector class ────────────────────────────────────────────────────

class LLMConnector:
    """
    Facade per gestire il ciclo di vita dei worker LLM.
    Usata dal dock widget per avviare richieste e ricevere callback.
    """

    def __init__(self, settings_manager):
        self.settings = settings_manager
        self._worker = None

    def is_configured(self) -> bool:
        return self.settings.has_api_key()

    def cancel(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.quit()

    def send_request(self, prompt: str,
                     on_done=None, on_error=None) -> LLMWorker:
        """
        Avvia una richiesta LLM asincrona.
        on_done(text: str), on_error(msg: str) — chiamati nel main thread via signal.
        """
        self.cancel()  # cancella eventuale chiamata precedente

        provider = self.settings.get_provider()
        api_key = self.settings.get_api_key()
        model = self.settings.get_model() or PROVIDER_DEFAULTS.get(provider, "")
        endpoint = ""
        if provider == "custom":
            endpoint = self.settings.get_custom_endpoint()

        worker = LLMWorker(provider, api_key, model, prompt, endpoint)
        if on_done:
            worker.finished.connect(on_done)
        if on_error:
            worker.error.connect(on_error)
        self._worker = worker
        worker.start()
        return worker

    def test_connection(self, on_done=None, on_error=None) -> LLMWorker:
        """Verifica credenziali con una chiamata minimale."""
        self.cancel()
        provider = self.settings.get_provider()
        api_key = self.settings.get_api_key()
        model = self.settings.get_model() or PROVIDER_DEFAULTS.get(provider, "")
        endpoint = ""
        if provider == "custom":
            endpoint = self.settings.get_custom_endpoint()

        worker = TestConnectionWorker(provider, api_key, model, endpoint)
        if on_done:
            worker.finished.connect(on_done)
        if on_error:
            worker.error.connect(on_error)
        self._worker = worker
        worker.start()
        return worker
