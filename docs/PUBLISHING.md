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

- [ ] **The working tree is scrubbed; the git history is not.** Three passes
      have run over the tree - 2026-08-05 and 2026-09-13 (secrets, real project
      names and the username in code comments) and 2026-09-21 (absolute paths
      and real project names in `docs/superpowers/`). None of them rewrote
      history, deliberately: `git filter-repo` would change every SHA, and the
      plans, `PROJECT-LOG.md` and `BACKLOG.md` cite those SHAs throughout.

      So `git log -p` still shows the author's home directory and two other
      project names. That is an accepted trade, not an oversight - the username
      is already published on purpose in `LICENSE`, `plugin.json` and every
      commit's author field. If you decide otherwise, rewriting history is the
      only thing that actually removes it, and it has to happen BEFORE the repo
      goes public.

- [ ] **`tests/test_fixture_isolation.py` names two real projects on purpose.**
      Its denylist asserts they never reach the rendered page. Leave them: a
      denylist of names that exist nowhere passes whether the fixture is clean
      or not.

- [ ] You're fine with your email in the commit metadata (declined for scrubbing
      on 2026-08-05, informed choice - noted here so it is a decision, not a
      discovery).

- [ ] The CI and Lint badges at the top of `README.md` are green on `main` -
      they keep working once the repo is public, no extra step needed.

## After

Nothing else is required. Optional follow-ups, not blockers:

- Repo description and topics (**Settings -> General**) - the description is
  the one-line pitch GitHub search shows, and it is English-only (the
  Portuguese lives in `README.pt.md`).
- Share the link wherever you'd planned to. The original spec,
  `docs/superpowers/specs/2026-08-05-public-launch-polish-design.md`, suggested
  direct link-sharing plus a plugin-developer community if one exists - that
  part is still open, not decided here.
