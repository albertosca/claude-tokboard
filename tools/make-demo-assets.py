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
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
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
    (["t"], 2.8),  # by model
    (["g"], 1.4),  # over to the session table
    (["down", "down", "down"], 0.7),
    (["enter"], 1.0),  # expand a project's group
    (["down"], 0.5),  # onto an actual session row, not the group node
    (["enter"], 2.6),  # its detail panel fills
    (["g"], 1.4),  # back to the chart, still sliced by model
    # One "p", last: every press raises its own notification, so cycling
    # back round to the default period would stack five of them on screen.
    # Last also means the session table above still shows every period.
    (["p"], 2.6),  # a different period - the slicing survives it
]

# Project names the committed fixture invents. At least one has to show up
# in every frame, or the frame did not render the data it claims to.
FIXTURE_PROJECTS = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta")
TERMINAL_SIZE = (132, 32)
GIF_WIDTH = 1100
# The last 12 bytes of every complete PNG: the zero-length IEND chunk.
PNG_END = b"\x00\x00\x00\x00IEND\xaeB`\x82"
PAGE_SIZE = (1400, 620)  # the HTML page's content ends well above 1000px


def seed_home(home: Path) -> None:
    sys.path[:0] = [str(REPO / "tests"), str(REPO / "lib")]
    from fixtures.build_fixture import build_fixture

    build_fixture(str(home), datetime.now().astimezone())


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


def rasterize(src: Path, dest: Path, width: int, height: int) -> None:
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
                    f"--screenshot={dest}",
                    f"file://{src}",
                ],
                check=True,
                capture_output=True,
                # Chrome writes the PNG in seconds and then may never exit, so
                # this is how long the run waits on a process that is
                # probably already done. Short is safe: an unwritten or
                # truncated PNG still fails the IEND check below, loudly.
                timeout=20,
            )
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    if not dest.exists() or not dest.read_bytes().endswith(PNG_END):
        raise SystemExit(f"no complete PNG at {dest} - chrome did not render it")


def make_screenshot(home: Path, work: Path) -> None:
    """The HTML dashboard, generated by the real entry point."""
    env = dict(os.environ, HOME=str(home), PYTHONPATH=str(REPO / "lib"))
    # sys.executable, never a bare "python3": python3 on PATH may be an asdf
    # shim, which resolves through $HOME/.asdf and exits 126 the moment HOME
    # is overridden - as it is here.
    subprocess.run(
        [sys.executable, str(REPO / "dashboard_html.py"), "--html", "--label-budget", "0", "--all"],
        env=env,
        cwd=str(REPO),
        check=True,
        capture_output=True,
        timeout=120,
    )
    page = home / ".claude" / "metrics" / "dashboard.html"
    leaked = [w for w in ("albertosca", "/Users/") if w in page.read_text()]
    if leaked:
        raise SystemExit(f"refusing to publish a page mentioning {leaked}")
    shot = work / "dashboard.png"
    rasterize(page, shot, *PAGE_SIZE)
    # Into assets/ only once it is whole: a run that dies mid-rasterize must
    # not leave the committed screenshot truncated or missing.
    shutil.move(str(shot), str(ASSETS / "dashboard.png"))


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
    app = tui.MetricsTUIApp(mr, tui.load_data(args, mr=mr), args=args)
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
    """
    env = dict(os.environ, HOME=str(home), PYTHONPATH=str(REPO / "lib"))
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
        env=env,
        cwd=str(REPO),
        check=True,
        timeout=180,
    )
    return [hold for _, hold in BEATS]


def refuse_if_it_shows_real_data(work: Path, count: int) -> None:
    """The guard that the leak above should have had.

    Positive control first: every frame has to name a project the FIXTURE
    invents, so a run that silently rendered nothing cannot pass. Then the
    denylist, built from the reader's own metrics file - the real names are
    read here and never written anywhere.
    """
    real = set()
    for name in (mr_metrics(), mr_subagents()):
        if not Path(name).exists():
            continue
        for line in Path(name).read_text().splitlines():
            path = json.loads(line).get("project_path") if line.strip() else None
            if path:
                real.add(Path(str(path).rstrip("/")).name)
    for i in range(count):
        svg = (work / f"f{i:02d}.svg").read_text()
        if not any(p in svg for p in FIXTURE_PROJECTS):
            raise SystemExit(f"frame {i} names no fixture project - did it render?")
        shown = sorted(n for n in real if n and n in svg)
        if shown:
            raise SystemExit(f"frame {i} shows real projects: {shown}")


def svg_size(svg: str) -> tuple[int, int]:
    """The window to rasterize an exported frame in: its own viewBox,
    rounded up. A hard-coded size either crops the bottom of the frame -
    where the key bar lives - or pads it with blank space, and it silently
    goes wrong the moment TERMINAL_SIZE changes."""
    match = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    if match is None:
        raise SystemExit("exported frame has no viewBox - cannot size it")
    return math.ceil(float(match.group(1))), math.ceil(float(match.group(2)))


def make_recording(home: Path, work: Path) -> None:
    holds = shoot_tui(home, work)
    refuse_if_it_shows_real_data(work, len(holds))
    for i in range(len(holds)):
        print(f"  frame {i + 1}/{len(holds)}", flush=True)
        svg = work / f"f{i:02d}.svg"
        rasterize(svg, work / f"f{i:02d}.png", *svg_size(svg.read_text()))
    # The concat demuxer ignores the final entry's duration, so the last
    # screen would flash past unless the file is named once more.
    listing = "".join(f"file 'f{i:02d}.png'\nduration {h:.2f}\n" for i, h in enumerate(holds))
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
    shutil.move(str(work / "tui.gif"), str(ASSETS / "tui.gif"))


def main() -> None:
    if not CHROME.exists():
        raise SystemExit(f"need Chrome to rasterize: {CHROME} not found")
    if shutil.which("ffmpeg") is None:
        raise SystemExit("need ffmpeg to encode the GIF")
    ASSETS.mkdir(exist_ok=True)
    tmp = workspace()
    try:
        home, work = Path(tmp) / "home", Path(tmp) / "work"
        home.mkdir()
        work.mkdir()
        seed_home(home)
        print("rendering the HTML dashboard...", flush=True)
        make_screenshot(home, work)
        print("recording the TUI...", flush=True)
        make_recording(home, work)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for name in ("dashboard.png", "tui.gif"):
        print(f"{name}: {(ASSETS / name).stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
