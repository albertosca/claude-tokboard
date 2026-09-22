# Publishing this repo

CI, README, badges, license, the visual assets and the privacy passes are all
done. What is left is one decision and the visibility switch itself - and the
switch is Alberto's call, not something to automate.

## Keeping the visual assets true

`assets/dashboard.png` and `assets/tui.gif` are both generated, never hand
captured, by:

```bash
python3 tools/make-demo-assets.py
```

It renders both from this repo's own synthetic test fixture into a throwaway
HOME, so no real session data can reach them. Re-run it whenever the
dashboards change enough that the images stop being true. It takes about
eight minutes, almost all of it waiting for headless Chrome to exit.

"No real data can reach them" is enforced, not intended. Before encoding, the
script reads the project names out of the reader's own metrics files and
refuses to continue if any of them appears in a frame - and refuses just as
loudly if a frame names none of the fixture's projects, since a frame that
rendered nothing would otherwise pass the first check. Both arms were
canaried. The guard exists because an earlier version of this script leaked:
metrics_core resolves METRICS_FILE from HOME at IMPORT time, so setting
os.environ["HOME"] afterwards aimed it at the real ~/.claude/metrics, and the
recording came out full of real projects and real token counts. It never
reached a commit. The TUI is now driven in a subprocess that has HOME in its
environment before Python starts.

## Make it public

```bash
gh repo edit albertosca/claude-tokboard --visibility public --accept-visibility-change-consequences
```

Or via the web UI: **Settings -> General -> Danger Zone -> Change repository
visibility -> Make public**.

## Before you click it

- [ ] **The history was rewritten and the GitHub repo recreated on 2026-09-22 - before it ever went public.** `git filter-repo` removed `docs/superpowers/` (specs, plans, project log, backlog) from every commit, and replaced the author's home directory and seven real project names in every remaining file and commit message. The planning docs now live in a private repository and are symlinked into the author's checkout, so every path under `docs/superpowers/` that the code or `CLAUDE.md` mentions exists for him and for nobody else - that is deliberate, not a broken link.

      The repo was deleted and recreated rather than force-pushed because GitHub keeps every pull request's head ref, read-only, pointing at the old commits: a force-push would have left the pre-rewrite history browsable through the PR pages. Recreating was the only self-service way to purge it; it cost the history of four PRs (three CI canaries and the one that added CI) and the old Actions runs.

      Verified on the rewritten history before pushing: zero occurrences of the replaced strings in any commit's content or message, zero commits touching `docs/superpowers/`, the final tree identical to the pre-rewrite one except for that directory, and the full suite green. The pre-rewrite history is kept locally as a git bundle, and the old-to-new commit map lives with the private docs.

- [ ] **The leak detector reads its denylist from the reader's own metrics** (`tests/test_fixture_isolation.py`), so no real project name is committed anywhere. With no real data - as in CI - it skips visibly instead of passing.

- [ ] You're fine with your email in the commit metadata (declined for scrubbing on 2026-08-05, informed choice - noted here so it is a decision, not a discovery).

- [ ] The CI and Lint badges at the top of `README.md` are green on `main` - they keep working once the repo is public, no extra step needed.

## After

Nothing else is required. The description and topics are already set. Sharing the link is still open: the original launch spec suggested direct link-sharing plus a plugin-developer community if one exists, and that was never decided.
