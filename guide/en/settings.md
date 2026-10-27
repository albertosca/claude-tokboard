# Settings

Everything here is optional.

The config file is `~/.claude/metrics/config.json`.

## Language

The interface speaks English and Brazilian Portuguese.

It follows your terminal's locale. To force one:

```json
{ "language": "pt" }
```

Accepted: `"en"`, `"pt"`, or a tag like `"pt-BR"`.

## Naming your projects

A session is logged under the directory it ran in.

That is not always the project.

List the folders your projects live in:

```json
{ "project_roots": ["~/code", "~/work"] }
```

Every folder directly inside a listed root is one project, however deep the session went.

| Session ran in | Without `project_roots` | With `~/code` listed |
|---|---|---|
| `~/code/my-api/docs/vendor` | vendor | my-api |

Recent versions already record the project root, so this mostly fixes older history.

## AI task labels

Claude can label each session with a short summary, like "fixing auth redirect bug".

It is **off by default**.

Why? Each new label is a `claude -p` call on **your own quota**.

Turn it on with a budget:

```bash
/tokboard-tui --label-budget 10
```

- `N` is how many **new** labels per run.
- Labels already made are cached, and free.

Labels are cached per language.

## Filters

Both scripts take:

| Flag | Effect |
|---|---|
| `--project` | Only sessions of this project path. |
| `--tag` | Only sessions with this tag. |
| `--days N` | Only the last N days. The full history is the default. |

## Tagging a session

Set `CLAUDE_METRIC_TAG` before starting Claude Code to tag its sessions. Then filter with `--tag`.
