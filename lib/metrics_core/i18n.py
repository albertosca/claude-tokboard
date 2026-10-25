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
        "en": "Quota (approx., this machine only): {input} in · {output} out",
        "pt": "Quota (aprox., só esta máquina): {input} entrada · {output} saída",
    },
    # The tooltip on the quota line above - missed by the plan's own
    # replacement table (found by the leak detector, not the plan).
    "quota_tooltip": {
        "en": "Approximation based on the Anthropic API's rate-limit rule "
        "(input + cache_creation count towards ITPM, cache_read does not, "
        "output is a separate limit). Claude Code does not publish its "
        "own quota's exact formula. Only the Claude Code sessions logged on "
        "this machine are counted; the account's quota also covers claude.ai "
        "and other devices, so a limit can be hit with less than this showing.",
        "pt": "Aproximação baseada na regra de rate-limit da API da "
        "Anthropic (input + cache_creation contam pra ITPM, cache_read "
        "não conta, output é um limite separado). O Claude Code não "
        "publica a fórmula exata da própria quota. Só contam as sessões do "
        "Claude Code registradas nesta máquina; a quota da conta também cobre "
        "o claude.ai e outros aparelhos, então o limite pode estourar com "
        "menos do que isto mostra.",
    },
    # The weekly-limit meter. The hypothesis wording is part of the contract:
    # the panel never states a single cap or a predicted hour.
    "meter.heading": {
        "en": "Weekly limit meter (hypothesis)",
        "pt": "Medidor do limite semanal (hipótese)",
    },
    "meter.name": {
        "en": "Cache creation in the current weekly window",
        "pt": "Cache creation na janela semanal atual",
    },
    "meter.window": {"en": "Window starting {start}", "pt": "Janela iniciada em {start}"},
    "meter.inferred": {"en": " (inferred)", "pt": " (inferida)"},
    "meter.tiers": {
        "en": "5-minute {five_minute} · 1-hour {one_hour}",
        "pt": "5 minutos {five_minute} · 1 hora {one_hour}",
    },
    "meter.range": {
        "en": "Accumulated at the first rejection of past weeks: {minimum} to {maximum}",
        "pt": "Acumulado na primeira rejeição das semanas passadas: {minimum} a {maximum}",
    },
    "meter.points.one": {"en": "Hypothesis, {n} point", "pt": "Hipótese, {n} ponto"},
    "meter.points.other": {"en": "Hypothesis, {n} points", "pt": "Hipótese, {n} pontos"},
    "meter.status.insufficient": {
        "en": "Not enough data to estimate the cap",
        "pt": "Dados insuficientes para estimar o limite",
    },
    "meter.status.contradicted": {
        "en": "Contradicted: a full week went past this range without a rejection",
        "pt": "Contradita: uma semana completa passou desta faixa sem rejeição",
    },
    "meter.status.no_data": {
        "en": "No calls stored yet",
        "pt": "Nenhuma chamada registrada ainda",
    },
    "meter.status.no_anchor": {
        "en": "No weekly rejection recorded yet, so the window cannot be placed",
        "pt": "Nenhuma rejeição semanal registrada, então a janela não pode ser posicionada",
    },
    "meter.caveat": {
        "en": "Lower bound: only the Claude Code sessions on this machine are counted.",
        "pt": "Limite inferior: só contam as sessões do Claude Code desta máquina.",
    },
    "meter.five_hour": {
        "en": "Last 5 hours: {accumulated} cache creation · {rejected} rejected "
        "5-hour windows in the last 7 days",
        "pt": "Últimas 5 horas: {accumulated} de cache creation · {rejected} janelas "
        "de 5 horas rejeitadas nos últimos 7 dias",
    },
    "meter.col.window": {"en": "Window", "pt": "Janela"},
    "meter.col.accumulated": {"en": "Accumulated", "pt": "Acumulado"},
    "meter.col.tiers": {"en": "5-minute / 1-hour", "pt": "5 minutos / 1 hora"},
    "meter.col.outcome": {"en": "Outcome", "pt": "Resultado"},
    "meter.outcome.rejected": {
        "en": "Rejected at {accumulated}",
        "pt": "Rejeitada em {accumulated}",
    },
    "meter.outcome.clear": {"en": "No rejection", "pt": "Sem rejeição"},
    "meter.outcome.open": {"en": "In progress", "pt": "Em andamento"},
    "meter.outcome.partial": {"en": " · partial data", "pt": " · dados parciais"},
    "meter.table_caption": {"en": "Recent weekly windows", "pt": "Janelas semanais recentes"},
    # Why the cache was written (cold start, rewrite, incremental).
    "causes.heading": {
        "en": "Where the cache creation came from",
        "pt": "De onde veio o cache creation",
    },
    "causes.note": {
        "en": "Cold start: the first call of a conversation. Rewrite: a later call that wrote "
        "at least half of its own context while the cache served less than half of the previous "
        "one, so the cache was lost. Incremental: normal growth.",
        "pt": "Partida a frio: a primeira chamada de uma conversa. Reescrita: uma chamada "
        "posterior que gravou ao menos metade do próprio contexto enquanto o cache serviu menos "
        "da metade do anterior, ou seja, o cache se perdeu. "
        "Incremental: crescimento normal.",
    },
    "causes.table_caption": {
        "en": "Cache creation by cause, per weekly window",
        "pt": "Cache creation por causa, por janela semanal",
    },
    "causes.col.window": {"en": "Window", "pt": "Janela"},
    "causes.col.total": {"en": "Total", "pt": "Total"},
    "causes.col.cold_start": {"en": "Cold start", "pt": "Partida a frio"},
    "causes.col.rewrite": {"en": "Rewrite", "pt": "Reescrita"},
    "causes.col.incremental": {"en": "Incremental", "pt": "Incremental"},
    "causes.col.after_idle": {"en": "Rewrite after idle", "pt": "Reescrita após pausa"},
    "causes.col.main_short": {"en": "Main 5-minute tier", "pt": "Principal na camada de 5 min"},
    "causes.col.subagent_short": {
        "en": "Subagents 5-minute tier",
        "pt": "Subagentes na camada de 5 min",
    },
    "causes.share": {"en": "{amount} ({percent}%)", "pt": "{amount} ({percent}%)"},
    "causes.none": {"en": "none", "pt": "nenhuma"},
    "causes.tier_alert": {
        "en": "The main conversation is writing {percent}% of its cache creation to the "
        "5-minute tier this window: each pause of 5 minutes now costs a rewrite.",
        "pt": "A conversa principal está gravando {percent}% do cache creation na camada de "
        "5 minutos nesta janela: cada pausa de 5 minutos agora custa uma reescrita.",
    },
    "causes.sizes_caption": {
        "en": "Subagent runs by number of calls (all stored history)",
        "pt": "Execuções de subagente por número de chamadas (todo o histórico guardado)",
    },
    "causes.col.size": {"en": "Calls per run", "pt": "Chamadas por execução"},
    "causes.col.runs": {"en": "Runs", "pt": "Execuções"},
    "causes.col.creation": {"en": "Cache creation", "pt": "Cache creation"},
    "causes.col.rewrite_share": {"en": "Of it rewrites", "pt": "Dele, reescritas"},
    "causes.size.upto": {"en": "up to {bound}", "pt": "até {bound}"},
    "causes.size.between": {"en": "{low} to {high}", "pt": "{low} a {high}"},
    "causes.size.over": {"en": "over {bound}", "pt": "mais de {bound}"},
    "causes.first_caption": {
        "en": "Size of the first request of a main conversation, by Claude Code version",
        "pt": "Tamanho da primeira requisição de uma conversa principal, por versão do Claude Code",
    },
    "causes.by_project_caption": {
        "en": "Size of the first request of a main conversation, by project",
        "pt": "Tamanho da primeira requisição de uma conversa principal, por projeto",
    },
    "causes.idle_caption": {
        "en": "Sessions that rewrote the most cache after an idle pause",
        "pt": "Sessões que mais reescreveram cache depois de uma pausa",
    },
    "causes.col.session": {"en": "Session", "pt": "Sessão"},
    "causes.col.idle_tokens": {"en": "Rewritten after idle", "pt": "Reescrito após pausa"},
    "causes.col.resumes": {"en": "Resumes", "pt": "Retomadas"},
    "causes.col.project": {"en": "Project", "pt": "Projeto"},
    "causes.col.version": {"en": "Version", "pt": "Versão"},
    "causes.col.conversations": {"en": "Conversations", "pt": "Conversas"},
    "causes.col.median": {"en": "Median first request", "pt": "Mediana da primeira requisição"},
    # Tokens per git branch inside a project.
    "branches.heading": {"en": "Tokens by branch", "pt": "Tokens por branch"},
    "branches.note": {
        "en": "Projects with at least two branches in the period. A session counts whole "
        "under the last branch it showed.",
        "pt": "Projetos com pelo menos dois branches no período. Uma sessão conta inteira no "
        "último branch que mostrou.",
    },
    "branches.detached": {"en": "(detached)", "pt": "(destacado)"},
    "branches.none": {"en": "(no branch)", "pt": "(sem branch)"},
    "branches.others": {"en": "others ({count})", "pt": "outros ({count})"},
    "branches.caption": {"en": "{project}: tokens by branch", "pt": "{project}: tokens por branch"},
    "branches.col.branch": {"en": "Branch", "pt": "Branch"},
    "branches.col.sessions": {"en": "Sessions", "pt": "Sessões"},
    "branches.col.tokens": {"en": "Tokens", "pt": "Tokens"},
    "branches.col.share": {"en": "Share", "pt": "Parcela"},
    "causes.first_jump": {
        "en": "The first request grew by {percent}% or more from {before} to {after}.",
        "pt": "A primeira requisição cresceu {percent}% ou mais de {before} para {after}.",
    },
    # The detail panel's close button - also missed by the plan's table.
    "detail.close": {"en": "Close", "pt": "Fechar"},
    # Why an output figure carries a "≥": the hooks' incomplete_usage_messages
    # (transcript_ingest.count_incomplete). "output" stays untranslated, as in
    # the composition legend it points at.
    "floor_note.one": {
        "en": "Claude Code didn't record the final usage of {n} message; output is a minimum",
        "pt": "O Claude Code não registrou o uso final de {n} mensagem; o output é um mínimo",
    },
    "floor_note.other": {
        "en": "Claude Code didn't record the final usage of {n} messages; output is a minimum",
        "pt": "O Claude Code não registrou o uso final de {n} mensagens; o output é um mínimo",
    },
    # Charts.
    "chart_title.pane": {"en": "Tokens per day (by project)", "pt": "Tokens por dia (por projeto)"},
    "chart_title.tokens": {
        "en": "Tokens per day (by token type)",
        "pt": "Tokens por dia (por tipo de token)",
    },
    "chart_title.model": {"en": "Tokens per day (by model)", "pt": "Tokens por dia (por modelo)"},
    "chart_too_narrow": {"en": "Too narrow for the chart", "pt": "Estreito demais para o gráfico"},
    "mode.pane": {"en": "By project", "pt": "Por projeto"},
    "mode.tokens": {"en": "By token type", "pt": "Por tipo de token"},
    "mode.model": {"en": "By model", "pt": "Por modelo"},
    "partial": {"en": " (partial, {pct}%)", "pt": " (parcial, {pct}%)"},
    # What the trend chart's shaded band marks (2026-10-05 review).
    "chart.band_note": {
        "en": "shaded band: the last 7 bars",
        "pt": "faixa sombreada: as últimas 7 barras",
    },
    # The TUI chart's in-progress marker, one per granularity, printed on a
    # line of its own under the x axis (see TrendChart._draw_chart for why
    # not on the last tick). The short form is what a narrow chart gets.
    "partial_bar.minute": {
        "en": "last bar: {pct}% of the minute so far",
        "pt": "última barra: {pct}% do minuto até agora",
    },
    "partial_bar.hour": {
        "en": "last bar: {pct}% of the hour so far",
        "pt": "última barra: {pct}% da hora até agora",
    },
    "partial_bar.day": {
        "en": "last bar: {pct}% of the day so far",
        "pt": "última barra: {pct}% do dia até agora",
    },
    "partial_bar.week": {
        "en": "last bar: {pct}% of the week so far",
        "pt": "última barra: {pct}% da semana até agora",
    },
    "partial_bar.month": {
        "en": "last bar: {pct}% of the month so far",
        "pt": "última barra: {pct}% do mês até agora",
    },
    "partial_bar.year": {
        "en": "last bar: {pct}% of the year so far",
        "pt": "última barra: {pct}% do ano até agora",
    },
    "partial_bar.short": {"en": "last bar: {pct}%", "pt": "última barra: {pct}%"},
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
    # Shown in the empty detail pane until a session is opened (2026-10-05
    # review: the right-hand half of the page started blank, with no hint).
    "detail.hint": {
        "en": "Select a session to see its subagents.",
        "pt": "Selecione uma sessão para ver os subagentes.",
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
    """`count` always chooses singular/plural; the `{n}` placeholder shows
    `count` itself unless the caller passes its own `n` (e.g. a count large
    enough to need format_tokens's k/M/B/T formatting - this module cannot
    format it here, since format.py imports FROM i18n)."""
    form = "one" if count == 1 else "other"
    values.setdefault("n", count)
    return t(f"{key}.{form}", lang, **values)


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
