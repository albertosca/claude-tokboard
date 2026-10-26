#!/usr/bin/env python3
"""Regenerate the README's screenshot and terminal recording.

Both assets are rendered from this repo's own synthetic test fixture into a
throwaway HOME, so no real session data can reach them. Run this whenever
the dashboards change enough that the images stop being true:

    python3 tools/make-demo-assets.py

Lives in tools/ rather than scripts/ on purpose. Everything under scripts/
is inside the 100% coverage gate; this is a developer utility that shells
out to a browser and a video encoder, and writing unit tests for that would
buy nothing. It is still linted and type-checked like the rest of the repo.

Requires Google Chrome (for both rasterizing steps) and ffmpeg (to encode
the GIF). vhs is the obvious tool for the recording and is deliberately NOT
used: on this machine vhs 0.12.0 runs a tape to completion, reports
"Creating <file>", and writes nothing at all - no GIF, no mp4, not even a
frames directory - so it cannot be told apart from a success. Driving the
app through Textual's own run_test/export_screenshot is both fewer moving
parts and the same mechanism the test suite already trusts.
"""

from __future__ import annotations

import contextlib
import html
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ASSETS = REPO / "assets"
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

# (keys pressed before the shot, how long to hold it, in seconds). It OPENS
# on the chart, not on the session table the app starts on: the first frame
# is what a README shows before the animation runs and in every static
# preview, and the session table over this small fixture is mostly empty.
BEATS: list[tuple[list[str], float]] = [
    (["g"], 2.6),  # chart, sliced by project
    (["t"], 2.4),  # by token type
    (["t"], 2.4),  # by model
    (["t"], 2.8),  # by branch
    (["g"], 1.4),  # over to the session table
    (["down", "down", "down"], 0.7),
    (["enter"], 1.0),  # expand a project's group
    (["down"], 0.5),  # onto an actual session row, not the group node
    (["enter"], 2.6),  # its detail panel fills
    (["g"], 1.4),  # back to the chart, still sliced by branch
    # One "p", last: every press raises its own notification, so cycling
    # back round to the default period would stack five of them on screen.
    # Last also means the session table above still shows every period.
    (["p"], 2.6),  # a different period - the slicing survives it
]

# Project names the committed fixture invents. At least one has to show up
# in every frame, or the frame did not render the data it claims to.
FIXTURE_PROJECTS = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta")
# 70 rows, not 32 (2026-10-26): the overview tab now opens on the weekly-limit
# meter and the cache-cause tables, and the trend chart sits below them. At 32
# rows frame 0 showed no chart at all; the chart's legend needs about 56 and
# the "last bar" line under its axis about 70.
TERMINAL_SIZE = (132, 70)
GIF_WIDTH = 1100
# The last 12 bytes of every complete PNG: the zero-length IEND chunk.
PNG_END = b"\x00\x00\x00\x00IEND\xaeB`\x82"
# 1700 wide, not 1400 (2026-09-23): the session table sits in a pane 62%
# of the page wide, and at 1400 it clipped its last columns mid-word
# ("compos."). At 1700 the pane fits every column through "duration".
# 1470 high (2026-10-26, was 620): with the weekly-limit meter, the
# cache-causes tables and the per-branch panel filled in, the page runs to
# about 1450 px, and 620 cut the screenshot off right under the meter's heading.
PAGE_SIZE = (1700, 1470)


def seed_home(home: Path) -> None:
    sys.path[:0] = [str(REPO / "tests"), str(REPO / "lib")]
    from fixtures.build_fixture import build_fixture

    build_fixture(str(home), datetime.now().astimezone())


# Generic model ids for the by-model view. The shared fixture carries a
# per-model map on only two rows on purpose (the tests need the "no model"
# band reachable), which left the README's by-model frames one grey band.
DEMO_MODELS = ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5")

# The shared fixture has no git_branch either. Handed out in turn over the
# sessions in time order, so consecutive sessions differ: the branch column
# fills, the "By branch" chart has bands, and a project with two or more
# sessions lands on two branches, which is what the per-branch panel needs.
DEMO_BRANCHES = ("main", "feature/export", "fix/redirect")


def split_by_model(record: dict[str, object]) -> dict[str, dict[str, int]]:
    """A full per-model map for one record, deterministic per session.

    Each of the four token totals is split across two or three models by
    weights drawn from a generator seeded with the session id, so a re-run
    draws the same picture. Every field sums EXACTLY to the record's own
    total - compute_model_totals takes whatever a map does not cover as
    "no model", and the backfill's verifier assumes a map never claims more
    than its row.
    """
    rng = random.Random(str(record.get("session_id")))
    models = rng.sample(DEMO_MODELS, rng.choice((2, 3)))
    weights = [rng.randint(1, 6) for _ in models]
    split: dict[str, dict[str, int]] = {model: {} for model in models}
    for field in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"):
        raw = record.get(field)
        total = raw if isinstance(raw, int) else 0
        shares = [total * weight // sum(weights) for weight in weights]
        shares[0] += total - sum(shares)
        for model, share in zip(models, shares, strict=True):
            split[model][field] = share
    return split


def stage_showcase(home: Path, now: datetime) -> None:
    """Make the throwaway HOME look like a settled install, not a new one.

    Two things the shared fixture leaves out on purpose, because tests rely
    on their absence, and that the README should not show a visitor:

    - backfill_state.json. Without it both dashboards open on the "token
      duplication not yet corrected" warning. A tokens_by_model map on
      every session, so the by-model chart shows models instead of one
      "no model" band.

    Every path here is built from `home` and checked to be inside it; none
    is taken from metrics_core's module constants, which point at the
    REAL ~/.claude/metrics in this process.

    BACKFILL STATE HONESTY (backlog 2026-09-24 #9): this used to write
    mr.DEFAULT_CUTOFF ("2026-08-13") as the state's cutoff. describe_backfill_
    state() renders EXACTLY two shapes - the "not yet corrected" warning, or
    "duplication corrected for sessions that ended before {cutoff} (applied
    on {applied})" - there is no third, silent shape (read describe_
    backfill_state itself: any state with a string cutoff AND a string
    applied_at renders the second one, unconditionally, regardless of how
    many rows corrected_rows says it touched). DEFAULT_CUTOFF is a real
    production date that has nothing to do with this fixture's own dates,
    and build_fixture's oldest record (~50 days before `now`) usually falls
    BEFORE it - so the demo claimed a correction had been applied to a
    session that this synthetic fixture never duplicated in the first
    place, on every machine where "now" made the arithmetic land that way.

    The fix is the shape the backlog item itself points at: a cutoff that
    predates every session this fixture writes. "sessions that ended before
    {cutoff}" then describes zero real sessions - vacuously true, not a
    fabricated claim about any session actually shown - and corrected_rows=0
    stops being an assertion made by fiat and becomes the literal, provable
    count of rows below that boundary (there are none). Computed from the
    fixture's OWN data (the earliest timestamp on any row, minus a day),
    not a second hard-coded constant that could drift out of sync with
    build_fixture the next time someone adds an older record.
    """
    metrics = home / ".claude" / "metrics"
    sessions = metrics / "sessions.jsonl"
    state = metrics / "backfill_state.json"
    for path in (sessions, state):
        if home.resolve() not in path.resolve().parents:
            raise SystemExit(f"refusing to write outside the throwaway HOME: {path}")

    rows = [json.loads(line) for line in sessions.read_text().splitlines() if line.strip()]
    for position, row in enumerate(sorted(rows, key=lambda session: session["timestamp_start"])):
        row["tokens_by_model"] = split_by_model(row)
        row["git_branch"] = DEMO_BRANCHES[position % len(DEMO_BRANCHES)]
    sessions.write_text("".join(json.dumps(row) + "\n" for row in rows))

    sys.path[:0] = [str(REPO / "lib")]
    import metrics_core as mr

    earliest_session_moment = min(
        datetime.fromisoformat(value)
        for row in rows
        for value in (row["timestamp_start"], row["timestamp_end"])
    )
    cutoff_before_every_session = (earliest_session_moment - timedelta(days=1)).date().isoformat()

    mr.write_backfill_state(
        cutoff_before_every_session,
        (now - timedelta(days=2)).isoformat(),
        [],
        uncorrectable_rows=0,
        earliest_corrected=None,
        path=str(state),
    )


# Cache creation, in tokens, that each past weekly window had piled up when it
# first rejected (newest window first), and what the open window holds so far.
# Same scale as the fixture's own sessions on purpose: the page's quota line
# sums their cache creation over the whole history (192), and one window
# holding more than that would make the screenshot contradict itself.
REJECTION_TARGETS = (52, 56, 48)
REJECTION_HOURS_BEFORE_RESET = (30, 44, 36)
OPEN_WINDOW_TARGET = 27
CONVERSATIONS_PER_WINDOW = 3
# One conversation: a cold start, two incremental calls, an idle pause longer
# than the 1-hour cache, then the resume that rewrites it and two more calls.
CALL_OFFSET_MINUTES = (0, 3, 6, 100, 104, 108)
CALL_WEIGHTS = (6, 1, 1, 4, 1, 1)
CALL_MODELS = ("claude-opus-5", "claude-sonnet-5")
SUBAGENT_MODEL = "claude-haiku-4-5"


def _window_calls(
    generator: random.Random,
    window_index: int,
    window_start: datetime,
    last_moment: datetime,
    target: int,
) -> list[dict[str, object]]:
    """Calls for one weekly window, adding up to `target` cache-creation tokens.

    The first conversation starts exactly at the window's start: a window only
    counts toward the meter's range when the earliest stored call is no later
    than its start, so the oldest window needs a call there. A short span
    (the open window right after a reset) squeezes the offsets instead of
    running past `last_moment`.
    """
    span_minutes = (last_moment - window_start).total_seconds() / 60
    slot = span_minutes / CONVERSATIONS_PER_WINDOW
    squeeze = min(1.0, span_minutes / (CONVERSATIONS_PER_WINDOW * (CALL_OFFSET_MINUTES[-1] + 10)))
    weights = CALL_WEIGHTS * CONVERSATIONS_PER_WINDOW
    shares = [target * weight // sum(weights) for weight in weights]
    shares[0] += target - sum(shares)
    rows: list[dict[str, object]] = []
    for conversation_index in range(CONVERSATIONS_PER_WINDOW):
        subagent = conversation_index == CONVERSATIONS_PER_WINDOW - 1
        model = SUBAGENT_MODEL if subagent else generator.choice(CALL_MODELS)
        conversation = f"demo-conversation-{window_index}-{conversation_index}"
        for call_index, offset in enumerate(CALL_OFFSET_MINUTES):
            moment = window_start + timedelta(minutes=conversation_index * slot + offset * squeeze)
            created = shares[conversation_index * len(CALL_OFFSET_MINUTES) + call_index]
            rows.append(
                {
                    "session_id": conversation,
                    "conversation": conversation,
                    "message_id": f"demo-message-{window_index}-{conversation_index}-{call_index}",
                    "timestamp": moment.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "model": model,
                    "sidechain": 1 if subagent else 0,
                    "claude_code_version": "2.1.310" if window_index <= 1 else "2.1.300",
                    "input": 1,
                    "output": generator.randint(1, 2),
                    "cache_read": generator.randint(2, 6),
                    # Subagents write the 5-minute tier, main conversations the 1-hour one.
                    "cache_5m": created if subagent else 0,
                    "cache_1h": 0 if subagent else created,
                }
            )
    return rows


def stage_calls(home: Path, now: datetime) -> None:
    """Give the throwaway HOME a per-call store, so the weekly-limit meter and
    the cache-causes panel show something instead of "No calls stored yet".

    The shared fixture has no calls.jsonl or limit-hits.jsonl on purpose
    (tests rely on their absence). Three past weekly windows each end in a
    rejection, which gives the meter its range, and the open window holds a
    part of that range. Deterministic: seeded, and every moment is derived
    from `now` on the Monday 05:00 UTC reset grid. Every path is checked to be
    inside `home`.
    """
    metrics = home / ".claude" / "metrics"
    calls_path = metrics / "calls.jsonl"
    hits_path = metrics / "limit-hits.jsonl"
    for path in (calls_path, hits_path):
        if home.resolve() not in path.resolve().parents:
            raise SystemExit(f"refusing to write outside the throwaway HOME: {path}")

    generator = random.Random("demo-calls")
    now_utc = now.astimezone(timezone.utc)
    next_reset = now_utc.replace(hour=5, minute=0, second=0, microsecond=0)
    next_reset += timedelta(days=(7 - next_reset.weekday()) % 7)
    if next_reset <= now_utc:
        next_reset += timedelta(days=7)

    calls: list[dict[str, object]] = []
    hits: list[dict[str, object]] = []
    for window_index in range(len(REJECTION_TARGETS) + 1):
        window_end = next_reset - timedelta(weeks=window_index)
        window_start = window_end - timedelta(weeks=1)
        if window_index == 0:
            target = OPEN_WINDOW_TARGET
            last_moment = now_utc - timedelta(minutes=20)
        else:
            target = REJECTION_TARGETS[window_index - 1]
            rejection = window_end - timedelta(hours=REJECTION_HOURS_BEFORE_RESET[window_index - 1])
            last_moment = rejection - timedelta(minutes=5)
            hits.append(
                {
                    "timestamp": rejection.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "rate_limit_type": "seven_day",
                    "resets_at": int(window_end.timestamp()),
                    "status": "rejected",
                }
            )
        calls.extend(_window_calls(generator, window_index, window_start, last_moment, target))

    calls.sort(key=lambda row: str(row["timestamp"]))
    calls_path.write_text("".join(json.dumps(row) + "\n" for row in calls))
    hits_path.write_text("".join(json.dumps(row) + "\n" for row in hits))


def mr_metrics() -> str:
    sys.path[:0] = [str(REPO / "lib")]
    import metrics_core as mr

    return str(mr.METRICS_FILE)


def mr_subagents() -> str:
    sys.path[:0] = [str(REPO / "lib")]
    import metrics_core as mr

    return str(mr.SUBAGENTS_FILE)


def workspace() -> str:
    """A scratch directory Chrome can actually read.

    NOT tempfile's default. On macOS that is $TMPDIR, which points inside
    the per-user sandbox under /var/folders/ - and Chrome, sandboxed
    itself, cannot read another process's directory there. It does not
    fail: it HANGS. Measured on 2026-09-21, both directions: the same page
    served from /var/folders exits 124 under a 40s timeout, and from
    /private/tmp exits 0 and writes the file.
    """
    return tempfile.mkdtemp(dir="/tmp" if sys.platform == "darwin" else None)


def rasterize(source: Path, destination: Path, width: int, height: int) -> None:
    """Screenshot a local file with headless Chrome.

    Chrome's EXIT is not the signal here, and reading it as one cost real
    time on 2026-09-21: a run sat for 14 minutes on its first screenshot
    with no output. Chrome had written the PNG within seconds and then
    simply never exited - measured afterwards across --headless, =new and
    =old, all three killed at a 35s timeout with a complete 62 KB PNG
    already on disk.

    So the artefact is the signal: give Chrome a deadline, then judge the
    file. A PNG that ends in its IEND chunk is whole no matter how the
    process died; anything else is a real failure.
    """
    profile = workspace()
    try:
        # Chrome not exiting is the EXPECTED path here, not an error.
        with contextlib.suppress(subprocess.TimeoutExpired):
            subprocess.run(
                [
                    str(CHROME),
                    "--headless",
                    "--disable-gpu",
                    "--hide-scrollbars",
                    "--no-first-run",
                    "--no-default-browser-check",
                    # Offline, hard. Textual's exported SVG declares an
                    # @font-face pointing at a CDN, and waiting on that
                    # fetch is what left frame 2 of the recording unwritten
                    # inside a 45s deadline. Failing DNS instantly also
                    # guarantees a published asset cannot have pulled
                    # anything off the network to make itself.
                    "--host-resolver-rules=MAP * ~NOTFOUND",
                    f"--user-data-dir={profile}",
                    "--force-device-scale-factor=2",
                    f"--window-size={width},{height}",
                    f"--screenshot={destination}",
                    f"file://{source}",
                ],
                check=True,
                capture_output=True,
                # Chrome writes the PNG in seconds and then may never exit, so
                # this is how long the run waits on a process that is
                # probably already done. Short is safe: an unwritten or
                # truncated PNG still fails the IEND check below, loudly.
                # 90s since 2026-10-26: the 1470px page with the meter and
                # tables did not finish inside 20s at load average ~11.
                timeout=90,
            )
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    if not destination.exists() or not destination.read_bytes().endswith(PNG_END):
        raise SystemExit(f"no complete PNG at {destination} - chrome did not render it")


def make_screenshot(home: Path, work: Path, lang: str) -> None:
    """The HTML dashboard, generated by the real entry point.

    LC_ALL is pinned to pt_BR.UTF-8 regardless of `lang`: config.json is
    what has to decide the language here, and an English render that only
    passed because the locale happened to agree with it would prove
    nothing.
    """
    environment = dict(
        os.environ, HOME=str(home), PYTHONPATH=str(REPO / "lib"), LC_ALL="pt_BR.UTF-8"
    )
    # sys.executable, never a bare "python3": python3 on PATH may be an asdf
    # shim, which resolves through $HOME/.asdf and exits 126 the moment HOME
    # is overridden - as it is here.
    subprocess.run(
        [sys.executable, str(REPO / "dashboard_html.py"), "--html", "--label-budget", "0", "--all"],
        env=environment,
        cwd=str(REPO),
        check=True,
        capture_output=True,
        timeout=120,
    )
    page = home / ".claude" / "metrics" / "dashboard.html"
    leaked = [marker for marker in ("albertosca", "/Users/") if marker in page.read_text()]
    if leaked:
        raise SystemExit(f"refusing to publish a page mentioning {leaked}")
    shot = work / "dashboard.png"
    rasterize(page, shot, *PAGE_SIZE)
    # Into assets/ only once it is whole: a run that dies mid-rasterize must
    # not leave the committed screenshot truncated or missing.
    shutil.move(str(shot), str(ASSETS / f"dashboard-{lang}.png"))


# Fixed 2026-09-28 (found while regenerating for backlog #9): MetricsTUIApp's
# third positional parameter is `arguments`, and load_data's is
# `metrics_core_module` - this inline script still called them `args` and
# `mr`, a rename in dashboard_tui.py this untested (see the module
# docstring) subprocess script never picked up. Confirmed by running: the
# recording step raised TypeError before this fix and produced a real GIF
# after it.
SHOOT_TUI = """
import asyncio, os, sys
from pathlib import Path
REPO, OUT = Path(sys.argv[1]), Path(sys.argv[2])
sys.path[:0] = [str(REPO / "lib"), str(REPO)]
import metrics_core as mr
import dashboard_tui as tui

BEATS = __import__("json").loads(sys.argv[3])

async def main():
    args = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    app = tui.MetricsTUIApp(mr, tui.load_data(args, metrics_core_module=mr), arguments=args)
    async with app.run_test(size=tuple(__import__("json").loads(sys.argv[4]))) as pilot:
        await pilot.pause()
        for i, keys in enumerate(BEATS):
            for key in keys:
                await pilot.press(key)
            await pilot.pause()
            (OUT / ("f%02d.svg" % i)).write_text(app.export_screenshot())

asyncio.run(main())
"""


def shoot_tui(home: Path, work: Path) -> list[float]:
    """Drive the TUI in a SUBPROCESS, with HOME in its environment.

    Not in-process, and this is the whole point. metrics_core resolves
    METRICS_FILE and SUBAGENTS_FILE from HOME at IMPORT time, so setting
    os.environ["HOME"] afterwards changes nothing - the module is already
    aimed at the real ~/.claude/metrics. An earlier version of this file
    did exactly that and produced a recording of the author's real
    projects and token counts. tests/test_tui_state.py documents the same
    trap and works around it by patching those constants; a fresh
    interpreter with HOME already set is the version that needs no
    workaround.

    LC_ALL is pinned to pt_BR.UTF-8 for both languages here too, for the
    same reason as in make_screenshot: it has to be config.json, not the
    locale, that decides.
    """
    environment = dict(
        os.environ, HOME=str(home), PYTHONPATH=str(REPO / "lib"), LC_ALL="pt_BR.UTF-8"
    )
    subprocess.run(
        [
            sys.executable,
            "-c",
            SHOOT_TUI,
            str(REPO),
            str(work),
            json.dumps([keys for keys, _ in BEATS]),
            json.dumps(list(TERMINAL_SIZE)),
        ],
        env=environment,
        cwd=str(REPO),
        check=True,
        timeout=180,
    )
    return [hold for _, hold in BEATS]


def _word_pattern(name: str) -> re.Pattern[str]:
    """A real name only counts as SHOWN if it appears as a whole word, not
    merely as a substring - a real project named "app" sits inside the
    English "approx." (Quota (approx.): ...) with nothing at all rendered
    from that project, and a raw substring check would refuse over it
    forever."""
    return re.compile(rf"(?<!\w){re.escape(name)}(?!\w)")


def _plain_text(svg: str) -> str:
    """The words a person reading the frame would actually see, stripped
    of markup. Mirrors tests/test_tui_i18n.py's `_strip`: Textual's SVG
    export joins every rendered word with U+00A0 (not a plain space) and
    may entity-escape characters inside <text> nodes, so unescaping first
    is what makes the word-boundary regex above see real boundaries
    instead of failing to match across an escaped run of characters.

    A <style> block goes first, content and all: Textual's export embeds the
    font-face CSS ("src: local(...)") there, and it is never drawn - a real
    project named "src" would otherwise be refused over it."""
    without_style = re.sub(r"<style\b.*?</style>", " ", svg, flags=re.DOTALL)
    return html.unescape(re.sub(r"<[^>]+>", " ", without_style)).replace("\xa0", " ")


def refuse_if_it_shows_real_data(work: Path, count: int, lang: str) -> None:
    """The guard that the leak above should have had.

    Positive control first: every frame has to name a project the FIXTURE
    invents, so a run that silently rendered nothing cannot pass. Then the
    denylist, built from the reader's own metrics file - the real names are
    read here and never written anywhere.

    A real name that is also a whole word inside one of THIS language's own
    catalog values (metrics_core.MESSAGES[key][lang], for every key) is
    dropped from the denylist before it is ever compared to a frame: such a
    name is part of the interface's own fixed vocabulary, indistinguishable
    from data no matter what is on screen, and a name like "projects" would
    otherwise refuse every single English frame forever, because the key
    bar's "More/fewer projects" is static chrome, not fixture output. Only
    the COUNT of names skipped this way is ever printed - never the names,
    since they are the reader's real, private project names.
    """
    real = set()
    for name in (mr_metrics(), mr_subagents()):
        if not Path(name).exists():
            continue
        for line in Path(name).read_text().splitlines():
            path = json.loads(line).get("project_path") if line.strip() else None
            if path:
                real.add(Path(str(path).rstrip("/")).name)

    sys.path[:0] = [str(REPO / "lib")]
    import metrics_core as mr

    # Placeholder tokens ({input}, {n}, {date}, ...) never render as
    # literal text - a real project named "input" or "date" must NOT be
    # excused just because that word appears inside a template's braces.
    catalog = " ".join(re.sub(r"\{[^{}]*\}", " ", texts[lang]) for texts in mr.MESSAGES.values())
    vocabulary = {name for name in real if name and _word_pattern(name).search(catalog)}
    if vocabulary:
        print(
            f"guard: {len(vocabulary)} real name(s) skipped (collide with UI vocabulary)",
            flush=True,
        )
    real -= vocabulary

    # An all-digit name ("2026") matches the year in every date a frame
    # renders, and no frame can be told apart from data by a number: the
    # same reason a vocabulary word is dropped. Counted, never named.
    numbers = {name for name in real if name.isdigit()}
    if numbers:
        print(
            f"guard: {len(numbers)} all-digit real name(s) skipped "
            "(indistinguishable from the numbers a frame renders)",
            flush=True,
        )
    real -= numbers

    for frame_index in range(count):
        svg = (work / f"f{frame_index:02d}.svg").read_text()
        if not any(project in svg for project in FIXTURE_PROJECTS):
            raise SystemExit(f"frame {frame_index} names no fixture project - did it render?")
        text = _plain_text(svg)
        shown = [name for name in real if name and _word_pattern(name).search(text)]
        if shown:
            raise SystemExit(f"frame {frame_index} shows {len(shown)} real project name(s)")


def svg_size(svg: str) -> tuple[int, int]:
    """The window to rasterize an exported frame in: its own viewBox,
    rounded up. A hard-coded size either crops the bottom of the frame -
    where the key bar lives - or pads it with blank space, and it silently
    goes wrong the moment TERMINAL_SIZE changes."""
    match = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    if match is None:
        raise SystemExit("exported frame has no viewBox - cannot size it")
    return math.ceil(float(match.group(1))), math.ceil(float(match.group(2)))


def make_recording(home: Path, work: Path, lang: str) -> None:
    holds = shoot_tui(home, work)
    refuse_if_it_shows_real_data(work, len(holds), lang)
    for frame_index in range(len(holds)):
        print(f"  frame {frame_index + 1}/{len(holds)}", flush=True)
        svg = work / f"f{frame_index:02d}.svg"
        rasterize(svg, work / f"f{frame_index:02d}.png", *svg_size(svg.read_text()))
    # The concat demuxer ignores the final entry's duration, so the last
    # screen would flash past unless the file is named once more.
    listing = "".join(
        f"file 'f{frame_index:02d}.png'\nduration {hold:.2f}\n"
        for frame_index, hold in enumerate(holds)
    )
    listing += f"file 'f{len(holds) - 1:02d}.png'\n"
    (work / "frames.txt").write_text(listing)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            "frames.txt",
            "-vf",
            f"scale={GIF_WIDTH}:-1:flags=lanczos,split[a][b];"
            "[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=3",
            "-loop",
            "0",
            "-y",
            str(work / "tui.gif"),
        ],
        cwd=str(work),
        check=True,
        timeout=180,
    )
    shutil.move(str(work / "tui.gif"), str(ASSETS / f"tui-{lang}.gif"))


def main() -> None:
    if not CHROME.exists():
        raise SystemExit(f"need Chrome to rasterize: {CHROME} not found")
    if shutil.which("ffmpeg") is None:
        raise SystemExit("need ffmpeg to encode the GIF")
    ASSETS.mkdir(exist_ok=True)
    temporary_directory = workspace()
    try:
        home, work = Path(temporary_directory) / "home", Path(temporary_directory) / "work"
        home.mkdir()
        work.mkdir()
        seed_home(home)
        stage_showcase(home, datetime.now().astimezone())
        stage_calls(home, datetime.now().astimezone())
        config_path = home / ".claude" / "metrics" / "config.json"
        for lang in ("en", "pt"):
            config_path.write_text(json.dumps({"language": lang}))
            print(f"rendering the HTML dashboard ({lang})...", flush=True)
            make_screenshot(home, work, lang)
            print(f"recording the TUI ({lang})...", flush=True)
            make_recording(home, work, lang)
    finally:
        shutil.rmtree(temporary_directory, ignore_errors=True)
    for lang in ("en", "pt"):
        for name in (f"dashboard-{lang}.png", f"tui-{lang}.gif"):
            print(f"{name}: {(ASSETS / name).stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
