🇺🇸 [English](README.md) · 🇧🇷 [Português](README.pt.md)

[![CI](https://github.com/albertosca/claude-tokboard/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/albertosca/claude-tokboard/actions/workflows/ci.yml)
[![Lint](https://github.com/albertosca/claude-tokboard/actions/workflows/lint.yml/badge.svg?branch=main)](https://github.com/albertosca/claude-tokboard/actions/workflows/lint.yml)
[![Coverage](https://raw.githubusercontent.com/albertosca/claude-tokboard/main/.github/badges/coverage.svg)](https://github.com/albertosca/claude-tokboard/blob/main/.github/badges/coverage.svg)

# tokboard

**See where your Claude Code tokens go** - by day, by project, by token type and by model.

A Claude Code plugin that logs every session and subagent in the background and shows it back to you two ways: an HTML dashboard in your browser, and a full terminal app.

![The HTML dashboard: a stacked trend chart by project, with tabs for token type and model, above a session table grouped by project](assets/dashboard.png)

<sub>The HTML dashboard. All screenshots are rendered from the repo's synthetic test data - no real sessions.</sub>

## What you get

- **One day, three slicings.** The trend chart splits each day's total by project, by token type or by model. A day stays the same height in every tab, so switching re-slices it instead of rescaling it.
- **A quota view, not just a volume view.** Most tokens are cheap cache reads. A separate line estimates how much of your rate-limit quota a period used, following Anthropic's documented rule for what counts.
- **An honest last bar.** The bucket still in progress is labelled with how much of it has elapsed (and hatched, in the browser), so a half-finished week never reads as a drop.
- **Session history.** Every session grouped by project, with its subagents, duration and token breakdown - filterable by period.
- **Click to highlight.** Click a legend entry or a bar to spotlight one project, token type or model across the chart.

![The terminal app: a session table with a detail panel, then the trend chart cycling through its three slicings of a day](assets/tui.gif)

<sub>The terminal app - same data, keyboard-driven: <kbd>g</kbd> charts/sessions, <kbd>t</kbd> chart mode, <kbd>p</kbd> period, <kbd>q</kbd> quit.</sub>

> The interface is in Brazilian Portuguese.

## Install

Install it through Claude Code's plugin system (see the [Claude Code plugin docs](https://docs.claude.com/en/docs/claude-code)). Then:

| Command | What it does |
|---|---|
| `/tokboard` | Builds the HTML dashboard and opens it in your browser. No dependencies. |
| `/tokboard-tui` | Sets up the terminal app on first run, then hands you the command to launch it in your own terminal. |

The browser opens by itself on macOS; elsewhere, open `~/.claude/metrics/dashboard.html`.

<details>
<summary><code>/tokboard-tui</code> fails on first run with an <code>ensurepip</code> error (Debian/Ubuntu)</summary>

The auto-install needs the `python3-venv` package, which isn't always preinstalled. Run `sudo apt install python3-venv` (or the version-specific `python3.X-venv` the error names) and try again. `/tokboard` is unaffected either way.
</details>

## Optional settings

**AI task labels.** Claude can label each session with a short summary ("fixing auth redirect bug"). It's off by default, because each new label is a `claude -p` call on your own quota.

Turn it on with `--label-budget N` (N new labels per run; cached ones are free), via `/tokboard-tui --label-budget N` or by running a dashboard script directly.

**Filters.** Both scripts also take `--project`, `--tag` and `--days`/`--all` to narrow what they show.

**Naming your projects.** A session is logged under whatever directory it ran in, so work inside `my-api/docs/vendor` would show up as "vendor". Tell tokboard where your projects live, in `~/.claude/metrics/config.json`:

```json
{ "project_roots": ["~/code", "~/work"] }
```

Any folder directly inside a listed root is one project, however deep the session went. Newer sessions record their project root automatically; this setting is what names older ones.

**tmux.** If you run Claude Code inside tmux, each session shows the window it ran in. Nothing to configure.

## How it's built

- **One data layer, two interfaces.** Both dashboards read from `lib/metrics_core/`, pure standard-library Python - every number is computed once and tested once.
- **100% statement and branch coverage, enforced.** A single uncovered line fails CI.
- **The page is tested by using it.** The rendering tests drive the real generated dashboard in jsdom - clicking, filtering, highlighting - and assert on computed styles, not on markup.
- **Strict static checks.** `ruff` with ten rule families and `mypy --strict` on every push.
- **Careful with your data.** Migrations over your irreplaceable logs run as dry runs first, are checked against an independent oracle, and write only after a verified backup.

## Your data

- Everything lives in `~/.claude/metrics/`: the session and subagent logs, label caches and the rendered dashboard.
- Nothing leaves your machine - unless you turn on AI labels, which send each labelled session to Claude to be summarized.
- Uninstalling stops new logging and keeps what's already there.

## Contributing

```bash
pip install -r requirements-dev.txt -r requirements-tui.txt
npm install                     # jsdom, for the rendering tests
pytest                          # full suite - 100% coverage is a hard gate
ruff check . && ruff format --check .
mypy --strict .
```

No build step. `dashboard_html.py` and `dashboard_tui.py` are the two entry points. PRs are welcome - for anything beyond a small fix, open an issue first so we can agree on the direction.

## License

[MIT](LICENSE)
