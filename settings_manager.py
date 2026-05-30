"""
QGIS Doctor — Settings manager.
Legge e scrive tutte le impostazioni con QgsSettings sotto il prefisso /qgis_doctor/.
"""

try:
    from qgis.core import QgsSettings
    _HAS_QGSSETTINGS = True
except ImportError:
    _HAS_QGSSETTINGS = False


class SettingsManager:
    PREFIX = "/qgis_doctor/"

    # ── Internals ────────────────────────────────────────────────────────────

    def _get(self, key: str, default):
        if not _HAS_QGSSETTINGS:
            return default
        s = QgsSettings()
        return s.value(self.PREFIX + key, default)

    def _set(self, key: str, value):
        if not _HAS_QGSSETTINGS:
            return
        s = QgsSettings()
        s.setValue(self.PREFIX + key, value)

    # ── AI Connection ────────────────────────────────────────────────────────

    def get_provider(self) -> str:
        """Returns: 'anthropic' | 'openai' | 'google' | 'custom'"""
        return self._get("provider", "anthropic")

    def set_provider(self, p: str):
        self._set("provider", p)

    def get_api_key(self) -> str:
        return self._get("api_key", "")

    def set_api_key(self, key: str):
        self._set("api_key", key)

    def get_custom_endpoint(self) -> str:
        return self._get("custom_endpoint", "")

    def set_custom_endpoint(self, url: str):
        self._set("custom_endpoint", url)

    def get_model(self) -> str:
        """Modello da usare — se vuoto usa il default del provider."""
        return self._get("model", "")

    def set_model(self, model: str):
        self._set("model", model)

    # ── Behaviour ────────────────────────────────────────────────────────────

    def get_language(self) -> str:
        """Returns: 'en' | 'it' | 'fr' | 'de' | 'es'"""
        return self._get("language", "en")

    def set_language(self, lang: str):
        self._set("language", lang)

    def get_online_lookup_enabled(self) -> bool:
        return self._get("online_lookup", True) in (True, "true", "True", "1")

    def set_online_lookup_enabled(self, v: bool):
        self._set("online_lookup", v)

    def get_github_token(self) -> str:
        return self._get("github_token", "")

    def set_github_token(self, token: str):
        self._set("github_token", token)

    def get_show_welcome(self) -> bool:
        val = self._get("show_welcome", True)
        return val in (True, "true", "True", "1")

    def set_show_welcome(self, v: bool):
        self._set("show_welcome", v)

    # ── Utility ──────────────────────────────────────────────────────────────

    def has_api_key(self) -> bool:
        return bool(self.get_api_key().strip())
