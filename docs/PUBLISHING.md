# Publishing this repo

Everything else is done - CI, README, badges, license, a privacy audit
across the full git history (not just the current tree). The only
remaining step is visibility, and that's Alberto's call, not something to
automate.

## Make it public

```bash
gh repo edit albertosca/claude-tokboard --visibility public --accept-visibility-change-consequences
```

Or via the web UI: **Settings -> General -> Danger Zone -> Change repository
visibility -> Make public**.

## Before you click it

- [ ] You're fine with the full commit history being visible, not just the
      current tree - it's been through two privacy passes (2026-08-05 and
      2026-09-13), but nobody's audit replaces your own final look.
- [ ] The CI and Lint badges at the top of `README.md` are already green on
      `main` (they were, as of the last push) - they'll keep working once
      the repo is public, no extra step needed.

## After

Nothing else is required. Optional follow-ups, not blockers:

- Add repo topics on GitHub (**Settings -> General -> Topics**) for
  discoverability - e.g. `claude-code`, `claude-code-plugin`, `python`.
- Share the link wherever you'd planned to. The original spec on this,
  `docs/superpowers/specs/2026-08-05-public-launch-polish-design.md`,
  suggested direct link-sharing plus a plugin-developer community if one
  exists - that part is still open, not decided here.
