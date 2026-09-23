"""Everything the two dashboards say, in both languages they speak.

One catalog, not one per interface: the HTML dashboard and the TUI already
duplicate too much, and the strings they share ("sem modelo", the period
names) would drift apart in two copies. The language is resolved once per
run and passed to every renderer explicitly - there is no module-level
"current language" here on purpose, so a test can render both languages in
one process without touching the environment.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

LANGUAGES = ("en", "pt")

MESSAGES: dict[str, dict[str, str]] = {
    # Placeholder labels the data layer produces.
    "no_model": {"en": "no model", "pt": "sem modelo"},
    "no_project": {"en": "no project", "pt": "sem projeto"},
    "others": {"en": "others", "pt": "outros"},
    # Plurals: tn() picks .one for exactly 1, .other for everything else.
    "sessions.one": {"en": "{n} session", "pt": "{n} sessão"},
    "sessions.other": {"en": "{n} sessions", "pt": "{n} sessões"},
    "past_projects.one": {"en": "+ {n} past project", "pt": "+ {n} projeto histórico"},
    "past_projects.other": {"en": "+ {n} past projects", "pt": "+ {n} projetos históricos"},
    "first_used.one": {"en": " · first used {n} day ago", "pt": " · 1º uso há {n} dia"},
    "first_used.other": {"en": " · first used {n} days ago", "pt": " · 1º uso há {n} dias"},
    # Not a plural form (no .one/.other counterpart on purpose) - zero
    # days since first use, and also the clock-skew case (first_seen in
    # the future) rather than printing "-1 days" (backlog #8).
    "first_used.today": {"en": " · first used today", "pt": " · 1º uso hoje"},
    # The quota line. "in"/"out" rather than "input"/"output": those two
    # words already name token types in the same panel, and the quota's
    # input side also counts cache writes.
    "quota_line": {
        "en": "Quota (approx.): {input} in · {output} out",
        "pt": "Quota (aprox.): {input} entrada · {output} saída",
    },
    # The tooltip on the quota line above - missed by the plan's own
    # replacement table (found by the leak detector, not the plan).
    "quota_tooltip": {
        "en": "Approximation based on the Anthropic API's rate-limit rule "
        "(input + cache_creation count towards ITPM, cache_read does not, "
        "output is a separate limit). Claude Code does not publish its "
        "own quota's exact formula.",
        "pt": "Aproximação baseada na regra de rate-limit da API da "
        "Anthropic (input + cache_creation contam pra ITPM, cache_read "
        "não conta, output é um limite separado). O Claude Code não "
        "publica a fórmula exata da própria quota.",
    },
    # The detail panel's close button - also missed by the plan's table.
    "detail.close": {"en": "Close", "pt": "Fechar"},
    # Charts.
    "chart_title.pane": {"en": "Tokens per day (by project)", "pt": "Tokens por dia (por projeto)"},
    "chart_title.tokens": {
        "en": "Tokens per day (by token type)",
        "pt": "Tokens por dia (por tipo de token)",
    },
    "chart_title.model": {"en": "Tokens per day (by model)", "pt": "Tokens por dia (por modelo)"},
    "mode.pane": {"en": "By project", "pt": "Por projeto"},
    "mode.tokens": {"en": "By token type", "pt": "Por tipo de token"},
    "mode.model": {"en": "By model", "pt": "Por modelo"},
    "partial": {"en": " (partial, {pct}%)", "pt": " (parcial, {pct}%)"},
    # Tabs and periods.
    "tab.sessions": {"en": "Sessions", "pt": "Sessões"},
    "tab.panorama": {"en": "Overview", "pt": "Panorama"},
    "period.ontem": {"en": "yesterday", "pt": "ontem"},
    "period.desde-segunda": {"en": "since Monday", "pt": "desde segunda"},
    "period.15-dias": {"en": "15 days", "pt": "15 dias"},
    "period.mes": {"en": "last month", "pt": "último mês"},
    "period.tudo": {"en": "all", "pt": "tudo"},
    # Session table and detail panel.
    "col.date": {"en": "date", "pt": "data"},
    "col.task": {"en": "task", "pt": "tarefa"},
    "col.duration": {"en": "duration", "pt": "duração"},
    "col.composition": {"en": "composition", "pt": "composição"},
    "col.tmux": {"en": "window", "pt": "aba"},
    "detail.no_subagents": {
        "en": "No subagents in this session.",
        "pt": "Nenhum subagente nesta sessão.",
    },
    "detail.subagents_title": {
        "en": "Subagents dispatched ({n})",
        "pt": "Subagentes despachados ({n})",
    },
    "no_transcript": {"en": "(no saved transcript)", "pt": "(sem transcript salvo)"},
    # Project panel.
    "project_totals_title": {"en": "Total tokens per project", "pt": "Total de tokens por projeto"},
    # This note used to say "ordenado por atividade mais recente primeiro",
    # which stopped being true when the panel moved to sorting by spend.
    "project_totals_sort_note": {
        "en": "sorted by total tokens, most first",
        "pt": "ordenado por total de tokens, do maior para o menor",
    },
    "open_now": {"en": "open now", "pt": "aberto agora"},
    "legend.open_session_html": {"en": "● session open now", "pt": "● sessão aberta agora"},
    "legend.open_session_tui": {
        "en": "█ session open now   ▓ past",
        "pt": "█ sessão aberta agora   ▓ histórico",
    },
    "fewer": {"en": "▾ fewer", "pt": "▾ menos"},
    "no_data": {"en": "No data.", "pt": "Sem dados."},
    # TUI key bar and notifications.
    "key.quit": {"en": "Quit", "pt": "Sair"},
    "key.chart_mode": {"en": "Chart mode", "pt": "Alterna modo do gráfico"},
    "key.more_projects": {"en": "More/fewer projects", "pt": "Mais/menos projetos"},
    "key.toggle_tab": {"en": "Charts/Sessions", "pt": "Gráficos/Sessões"},
    "key.cycle_period": {"en": "period", "pt": "período"},
    "notify.period": {"en": "period: {name}", "pt": "período: {name}"},
    # The token-dedup correction boundary note.
    "backfill.never_applied": {
        "en": "token duplication not yet corrected in the history "
        "(data before {cutoff} may be inflated)",
        "pt": "duplicação de tokens ainda não corrigida no histórico "
        "(dados anteriores a {cutoff} podem estar inflados)",
    },
    "backfill.applied": {
        "en": "duplication corrected for sessions that ended before {cutoff} "
        "(applied on {applied}); sessions with no saved transcript are still inflated",
        "pt": "duplicação corrigida para sessões encerradas antes de {cutoff} "
        "(aplicado em {applied}); sessões sem transcript salvo continuam com contagem inflada",
    },
    "backfill.retention": {
        "en": "; data before {date} may still be inflated, with no transcript to correct it",
        "pt": "; dados antes de {date} podem ainda estar inflados, sem transcript pra corrigir",
    },
    "page_title": {
        "en": "tokboard — Claude Code metrics",
        "pt": "tokboard — métricas do Claude Code",
    },
}


def t(key: str, lang: str, **values: Any) -> str:
    return MESSAGES[key][lang].format(**values)


def tn(key: str, count: int, lang: str, **values: Any) -> str:
    form = "one" if count == 1 else "other"
    return t(f"{key}.{form}", lang, n=count, **values)


# POSIX order: the first non-empty one wins, and an empty one counts as unset.
_LOCALE_VARIABLES = ("LC_ALL", "LC_MESSAGES", "LANG")


def resolve_language(config: Mapping[str, Any], environ: Mapping[str, str]) -> str:
    """The run's language: config first, then the locale, then English.

    A config value that is not a known language is ignored, not an error -
    the same tolerance project_roots has: a typo in the config never stops
    a dashboard from opening. A language TAG counts too, not just a bare
    "en"/"pt": normalised (stripped, lower-cased, "_" -> "-") and split on
    "-", its PRIMARY subtag must equal a known language exactly - "pt-BR"
    (the tag the page's own <html lang> uses) and "PT_BR" both resolve to
    "pt", but "portuguese" does not, because a substring match would also
    accept nonsense no real tag produces.
    """
    configured = config.get("language")
    if isinstance(configured, str):
        normalized = configured.strip().lower().replace("_", "-")
        primary = normalized.split("-", 1)[0]
        if primary in LANGUAGES:
            return primary
    for name in _LOCALE_VARIABLES:
        value = environ.get(name, "")
        if value:
            return "pt" if value.lower().startswith("pt") else "en"
    return "en"


# Never strftime("%b"): it follows the PROCESS locale, so on a machine in
# pt_BR it prints "set" for September inside an English interface.
_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")  # fmt: skip

# Portuguese keeps exactly the formats the dashboards used before this
# module existed. Every directive here prints digits, so none of them
# depends on the process locale.
_PT_TICKS = {
    "minute": "%H:%M",
    "hour": "%d/%m %Hh",
    "day": "%d/%m",
    "week": "S%V/%y",
    "month": "%m/%Y",
    "year": "%Y",
}


def tick_label(granularity: str, bucket_start: datetime, lang: str) -> str:
    if lang == "pt":
        return bucket_start.strftime(_PT_TICKS[granularity])
    month = _MONTHS_EN[bucket_start.month - 1]
    if granularity == "minute":
        return bucket_start.strftime("%H:%M")
    if granularity == "hour":
        return f"{month} {bucket_start.day} {bucket_start:%H}:00"
    if granularity == "day":
        return f"{month} {bucket_start.day}"
    if granularity == "week":
        return bucket_start.strftime("W%V/%y")
    if granularity == "month":
        return f"{month} {bucket_start.year}"
    return str(bucket_start.year)


def session_date(timestamp: str | None, lang: str) -> str:
    if not timestamp:
        return "-"
    try:
        moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone()
    except (ValueError, AttributeError, TypeError):
        return "-"
    if lang == "pt":
        return moment.strftime("%d/%m %H:%M")
    return f"{_MONTHS_EN[moment.month - 1]} {moment.day} {moment:%H:%M}"
