"""
QGIS Doctor — Report Builder.

Genera:
  - HTML per il QTextEdit del dock (build_report_html)
  - Markdown per "Copy for AI" (build_clipboard_markdown)
"""

from datetime import datetime


# ── Emoji / severity mapping ──────────────────────────────────────────────────

_ICON = {
    "ERROR":   "🔴",
    "WARNING": "🟡",
    "INFO":    "🔵",
}

_ISSUE_TYPE_LABEL = {
    "user_error":     "User configuration error",
    "known_bug":      "Possible QGIS bug",
    "config_warning": "Configuration warning",
    "info":           "Information",
}


# ── HTML report (shown in dock QTextEdit) ─────────────────────────────────────

def build_report_html(issues: list, env: dict) -> str:
    if not issues:
        return _html_no_issues()

    errors   = [i for i in issues if i.severity == "ERROR"]
    warnings = [i for i in issues if i.severity == "WARNING"]
    infos    = [i for i in issues if i.severity == "INFO"]

    sections = []

    if errors:
        sections.append(_html_section("🔴 Errors", errors, "#c0392b"))
    if warnings:
        sections.append(_html_section("🟡 Warnings", warnings, "#e67e22"))
    if infos:
        sections.append(_html_section("🔵 Info", infos, "#2980b9"))

    summary_line = (
        f"<p style='color:#555; font-size:11px; margin-top:12px;'>"
        f"Found {len(errors)} error(s), {len(warnings)} warning(s), "
        f"{len(infos)} info — "
        f"project: <b>{env.get('project_name','?')}</b> — "
        f"QGIS {env.get('qgis_version','?')}</p>"
    )

    return (
        "<html><body style='font-family:system-ui,sans-serif; font-size:13px; "
        "margin:8px; color:#222;'>"
        + summary_line
        + "".join(sections)
        + "</body></html>"
    )


def _html_section(title: str, issues: list, color: str) -> str:
    items = "".join(_html_issue(i) for i in issues)
    return (
        f"<h3 style='color:{color}; border-bottom:1px solid {color}; "
        f"padding-bottom:4px; margin-top:16px;'>{title} ({len(issues)})</h3>"
        + items
    )


def _html_no_issues() -> str:
    return (
        "<html><body style='font-family:system-ui,sans-serif; font-size:13px; "
        "margin:24px; color:#222; text-align:center;'>"
        "<p style='font-size:40px;'>✅</p>"
        "<h2 style='color:#1a7a1a;'>No issues found</h2>"
        "<p style='color:#555;'>Your QGIS project looks healthy.<br>"
        "Run diagnostics again after loading new layers or changing settings.</p>"
        "</body></html>"
    )


def _esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _nl2br(text: str) -> str:
    """Converte newline in <br> e rispetta l'indentazione con &nbsp;"""
    lines = _esc(text).split("\n")
    result = []
    for line in lines:
        # Preserva spazi iniziali come &nbsp;
        stripped = line.lstrip(" ")
        spaces = len(line) - len(stripped)
        result.append("&nbsp;" * spaces + stripped)
    return "<br>".join(result)


def _html_issue(issue) -> str:
    icon = _ICON.get(issue.severity, "⚪")
    type_badge = _ISSUE_TYPE_LABEL.get(issue.issue_type, issue.issue_type)

    border_color = {
        "ERROR": "#e74c3c",
        "WARNING": "#e67e22",
        "INFO": "#3498db",
    }.get(issue.severity, "#ccc")

    layer_line = (
        f"<div style='color:#666; font-size:11px; margin:3px 0;'>"
        f"Layer: <b>{_esc(issue.layer_name)}</b></div>"
        if issue.layer_name else ""
    )

    explanation_html = (
        f"<div style='color:#333; margin:6px 0; line-height:1.5;'>"
        f"{_nl2br(issue.explanation)}</div>"
    )

    suggestion_html = (
        f"<div style='background:#f0fff0; border:1px solid #90c090; border-radius:3px; "
        f"padding:8px; margin-top:8px; font-size:12px; line-height:1.6;'>"
        f"<b style='color:#1a7a1a;'>Come risolvere:</b><br>"
        f"<span style='color:#1a5a1a; font-family:monospace;'>"
        f"{_nl2br(issue.suggestion)}</span></div>"
        if issue.suggestion else ""
    )

    # Known issues links
    known_issues_html = ""
    if issue.known_issues:
        links = []
        for ref in issue.known_issues[:3]:
            status_badge = {
                "closed": "✅ closed",
                "answered": "✅ answered",
                "open": "🔵 open",
            }.get(ref.status, ref.status)
            resolved = f" — fixed in {ref.resolved_in}" if ref.resolved_in else ""
            source_icon = "🐙" if ref.source == "github" else "📚"
            links.append(
                f"<a href='{ref.url}' style='color:#2980b9; text-decoration:none;'>"
                f"{source_icon} {_esc(ref.title[:80])}</a> "
                f"<span style='color:#888; font-size:10px;'>[{status_badge}{resolved}]</span>"
            )
        known_issues_html = (
            f"<div style='background:#eef4fb; border:1px solid #aec9e8; border-radius:3px; "
            f"padding:6px 8px; margin-top:6px; font-size:11px;'>"
            f"<b style='color:#1a5fa0;'>Known references:</b><br>"
            + "<br>".join(links) +
            f"</div>"
        )
    elif issue.issue_type == "known_bug":
        known_issues_html = (
            f"<div style='margin-top:4px;'>"
            f"<a href='qgisdoctor://report_bug/{_esc(issue.id)}' "
            f"style='color:#c0392b; font-size:11px; text-decoration:none;'>"
            f"🐛 Report this bug on GitHub</a></div>"
        )

    # Fix button for auto-fixable issues
    fix_button_html = ""
    if issue.auto_fixable:
        fix_button_html = (
            f"<div style='margin-top:4px;'>"
            f"<a href='qgisdoctor://fix/{_esc(issue.id)}/{_esc(issue.layer_name)}' "
            f"style='background:#2980b9; color:white; padding:2px 8px; border-radius:3px; "
            f"font-size:11px; text-decoration:none;'>🔧 Fix automatically</a></div>"
        )

    # Anchor for selecting this issue
    anchor = f"<a name='issue_{_esc(issue.id)}'></a>"

    return (
        f"{anchor}"
        f"<div id='issue_{_esc(issue.id)}' "
        f"style='margin:10px 0; padding:10px 12px; "
        f"background:#fafafa; border-left:4px solid {border_color}; "
        f"border-radius:2px; box-shadow: 1px 1px 3px #eee;'>"
        f"<div style='font-weight:bold; font-size:13px;'>"
        f"<a href='qgisdoctor://select/{_esc(issue.id)}' style='text-decoration:none; color:inherit;'>"
        f"{icon} [{_esc(issue.id)}] {_esc(issue.title)}</a></div>"
        f"<div style='color:#888; font-size:11px; margin:2px 0 6px 0;'>"
        f"{_esc(type_badge)}</div>"
        f"{layer_line}"
        f"{explanation_html}"
        f"{suggestion_html}"
        f"{known_issues_html}"
        f"{fix_button_html}"
        f"</div>"
    )


# ── Markdown report for clipboard ("Copy for AI") ────────────────────────────

def build_clipboard_markdown(issues: list, env: dict) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    errors_user  = [i for i in issues if i.severity == "ERROR" and i.issue_type == "user_error"]
    errors_bug   = [i for i in issues if i.severity == "ERROR" and i.issue_type == "known_bug"]
    warnings     = [i for i in issues if i.severity == "WARNING"]
    infos        = [i for i in issues if i.severity == "INFO"]

    lines = [
        "# QGIS Doctor — Diagnostic Report",
        f"**Project:** {env.get('project_name','Untitled')}",
        f"**QGIS version:** {env.get('qgis_version','?')}",
        f"**Date:** {now}",
        f"**OS:** {env.get('os','?')}",
        "",
        "## Issues found",
        "",
    ]

    def _section_md(title, items):
        if not items:
            return []
        out = [f"### {title} ({len(items)})", ""]
        for i in items:
            layer = f"\nLayer: **{i.layer_name}**" if i.layer_name else ""
            out += [
                f"#### {_ICON.get(i.severity,'⚪')} [{i.id}] {i.title}",
                f"*Type: {_ISSUE_TYPE_LABEL.get(i.issue_type, i.issue_type)}*{layer}",
                "",
                i.explanation,
                f"→ {i.suggestion}",
                "",
                f"<details><summary>Technical detail</summary>{i.technical_detail}</details>",
                "",
            ]
        return out

    lines += _section_md("🔴 ERRORS — User configuration", errors_user)
    lines += _section_md("🔴 ERRORS — Possible bugs", errors_bug)
    lines += _section_md("🟡 WARNINGS", warnings)
    lines += _section_md("🔵 INFO", infos)

    plugins_str = ", ".join(env.get("active_plugins", [])[:10]) or "none"
    lines += [
        "## Environment",
        "",
        f"- Project CRS: {env.get('project_crs', '?')}",
        f"- Layers loaded: {env.get('n_layers', '?')}",
        f"- Active plugins: {plugins_str}",
        f"- Snapping: {env.get('snapping', '?')}",
        "",
        "---",
        "*Generated by QGIS Doctor. Paste this into Claude, ChatGPT, or any AI assistant "
        "and ask: \"Analyze this QGIS diagnostic report. For each issue marked as a user "
        "configuration error, give me step-by-step instructions to fix it. Start with "
        "the most critical problems.\"*",
    ]

    return "\n".join(lines)
