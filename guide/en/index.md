# tokboard

**See where your Claude Code tokens go** - by day, by project, by token type and by model.

tokboard is a Claude Code plugin. It logs every session in the background. You look at the result in your browser or in your terminal.

Nothing leaves your machine.

![The HTML dashboard](images/dashboard.png)

## The problem it solves

A raw token count mixes two very different things:

- cheap **cache reads**, which barely matter;
- the tokens that **count against your rate limit**.

tokboard shows you both numbers, side by side.

Ran out of weekly limit and can't tell why? tokboard splits your cache creation by cause and tracks the current week against the range where past weeks hit the limit.

## What you get

- **A quota view**, not just a volume view.
- **Four slicings** of each day: by project, token type, model or git branch.
- **Every session**, grouped by project, with its subagents.
- **A weekly-limit meter** and the causes of cache creation.
- **Tokens per branch** for projects that used more than one.
- **Two interfaces**, one data layer: a browser page and a terminal app.

## Where to go next

| You want to... | Read |
|---|---|
| Get it running | [Install](install.md) |
| See your first dashboard | [First look](first-look.md) |
| Understand the numbers | [Token types and the quota line](concepts.md) |
| Know if you are close to the weekly limit | [The weekly-limit meter](weekly-meter.md) |
| Use the browser page | [The HTML dashboard](dashboard.md) |
| Use the terminal app | [The terminal app](tui.md) |
| Name your projects, label sessions | [Settings](settings.md) |
| Know what is stored | [Your data](your-data.md) |
| Update to a new version | [Upgrading](upgrading.md) |
| Fix a problem | [FAQ](faq.md) |
