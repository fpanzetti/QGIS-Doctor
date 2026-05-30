"""
QGIS Doctor — Knowledge Lookup (Phase 4).

Cerca su GitHub Issues e GIS StackExchange per issue di tipo "known_bug".
Eseguito in QThread separato. Max 5 chiamate totali per analisi.
"""

import json
import urllib.request
import urllib.parse
import urllib.error
from dataclasses import dataclass, field

from PyQt5.QtCore import QThread, pyqtSignal


@dataclass
class KnownIssueRef:
    source: str           # "github" | "stackexchange"
    title: str
    url: str
    status: str           # "open" | "closed" | "answered" | "unknown"
    resolved_in: str      # versione se disponibile, altrimenti ""
    snippet: str          # breve estratto del testo


class KnowledgeLookupWorker(QThread):
    """
    Cerca riferimenti per una lista di issue di tipo known_bug.
    Emette results(issue_id, refs) per ogni issue processata.
    """

    result_ready = pyqtSignal(str, list)   # (issue_id, [KnownIssueRef])
    finished = pyqtSignal()
    log = pyqtSignal(str)                  # log messaggi (rate limit ecc.)

    def __init__(self, issues: list, github_token: str = ""):
        super().__init__()
        self.issues = [i for i in issues if i.issue_type == "known_bug"]
        self.github_token = github_token
        self._calls_made = 0
        self._max_calls = 5
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        for issue in self.issues:
            if self._cancelled or self._calls_made >= self._max_calls:
                break
            try:
                refs = self._lookup_issue(issue)
                if refs:
                    self.result_ready.emit(issue.id, refs)
            except Exception as e:
                self.log.emit(f"Lookup error for {issue.id}: {e}")
        self.finished.emit()

    def _lookup_issue(self, issue) -> list:
        refs = []

        # Usa i search_keywords se disponibili, altrimenti costruisci dai campi
        if issue.search_keywords:
            query = " ".join(issue.search_keywords[:3])
        else:
            # Costruisci query dagli ID e parole chiave del titolo
            words = issue.title.split()[:5]
            query = f"QGIS {' '.join(words)}"

        # GitHub search
        if self._calls_made < self._max_calls and not self._cancelled:
            try:
                gh_refs = self._search_github(query, issue)
                refs.extend(gh_refs)
                self._calls_made += 1
            except Exception as e:
                self.log.emit(f"GitHub search failed: {e}")

        # GIS StackExchange
        if self._calls_made < self._max_calls and not self._cancelled:
            try:
                se_refs = self._search_stackexchange(query)
                refs.extend(se_refs)
                self._calls_made += 1
            except Exception as e:
                self.log.emit(f"StackExchange search failed: {e}")

        return refs

    def _search_github(self, query: str, issue) -> list:
        # Cerca su GitHub Issues di qgis/QGIS
        encoded = urllib.parse.quote(f"{query} repo:qgis/QGIS")
        url = f"https://api.github.com/search/issues?q={encoded}&per_page=3&sort=relevance"

        req = urllib.request.Request(url)
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("User-Agent", "QGIS-Doctor-Plugin/0.3.0")
        if self.github_token:
            req.add_header("Authorization", f"token {self.github_token}")

        with urllib.request.urlopen(req, timeout=10) as resp:
            # Log rate limit
            remaining = resp.headers.get("X-RateLimit-Remaining", "?")
            self.log.emit(f"GitHub X-RateLimit-Remaining: {remaining}")
            data = json.loads(resp.read().decode("utf-8"))

        refs = []
        for item in data.get("items", [])[:3]:
            state = item.get("state", "unknown")
            # Cerca versione risoluzione nel testo
            body = item.get("body", "") or ""
            resolved_in = ""
            import re
            m = re.search(r"fixed in[:\s]+(\d+\.\d+[\.\d]*)", body, re.IGNORECASE)
            if m:
                resolved_in = m.group(1)

            refs.append(KnownIssueRef(
                source="github",
                title=item.get("title", "")[:120],
                url=item.get("html_url", ""),
                status=state,
                resolved_in=resolved_in,
                snippet=(body[:200] + "..." if len(body) > 200 else body),
            ))
        return refs

    def _search_stackexchange(self, query: str) -> list:
        params = urllib.parse.urlencode({
            "intitle": query[:100],
            "site": "gis",
            "pagesize": 3,
            "order": "desc",
            "sort": "relevance",
            "filter": "withbody",
        })
        url = f"https://api.stackexchange.com/2.3/search?{params}"

        req = urllib.request.Request(url)
        req.add_header("User-Agent", "QGIS-Doctor-Plugin/0.3.0")
        req.add_header("Accept-Encoding", "identity")

        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        refs = []
        for item in data.get("items", [])[:3]:
            is_answered = item.get("is_answered", False)
            status = "answered" if is_answered else "open"
            body = item.get("body", "") or ""
            # Rimuovi HTML tags semplice
            import re
            clean_body = re.sub(r"<[^>]+>", " ", body)[:200]

            refs.append(KnownIssueRef(
                source="stackexchange",
                title=item.get("title", "")[:120],
                url=item.get("link", ""),
                status=status,
                resolved_in="",
                snippet=clean_body + ("..." if len(body) > 200 else ""),
            ))
        return refs
