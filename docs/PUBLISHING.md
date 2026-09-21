# Publishing this repo

CI, README, badges, license and the privacy passes are done. What is left is
one asset, one decision, and the visibility switch itself - and the switch is
Alberto's call, not something to automate.

## Still open

- [ ] **A terminal recording of the TUI.** The 2026-08-05 polish spec promised
      a screenshot AND a GIF; only the screenshot exists (`assets/dashboard.png`,
      rendered from the synthetic fixture on 2026-09-21). The recording needs an
      interactive terminal, so it cannot be produced headlessly. With
      [vhs](https://github.com/charmbracelet/vhs) or
      [asciinema](https://asciinema.org), against a throwaway HOME so no real
      session data is recorded:

      ```bash
      # Build a HOME holding only the committed synthetic fixture,
      # then drive the TUI against it.
      DEMO=$(mktemp -d)
      python3 - "$DEMO" <<'EOF'
      import sys
      from datetime import datetime
      from pathlib import Path
      sys.path[:0] = ["tests", "lib"]
      from fixtures.build_fixture import build_fixture
      build_fixture(sys.argv[1], datetime.now().astimezone())
      EOF
      HOME="$DEMO" PYTHONPATH=lib python3 dashboard_tui.py --label-budget 0
      ```

      Record that, save it as `assets/tui.gif`, and add it to both READMEs
      beside the dashboard screenshot.

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
