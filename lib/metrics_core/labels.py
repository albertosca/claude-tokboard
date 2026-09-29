"""AI-generated session/subagent label caching and generation - the
only part of metrics_core that shells out to `claude -p`. See
LabelBudget for the per-run cap on real API calls this makes."""

from __future__ import annotations

import json
import os as os  # re-exported: tests monkeypatch labels.os.replace directly
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, cast

TASK_LABELS_FILE = str(Path("~/.claude/metrics/task_labels.json").expanduser())
AGENT_LABELS_FILE = str(Path("~/.claude/metrics/agent_labels.json").expanduser())

# id -> language -> label. Each cache file on disk mirrors this shape once
# migrated (see _migrate); the pre-migration shape was id -> label (a plain
# string), always meaning Portuguese.
LabelCache = dict[str, dict[str, str]]


def find_away_summary(transcript_path: str | None) -> str | None:
    if not transcript_path or not Path(transcript_path).exists():
        return None
    last_content = None
    try:
        with Path(transcript_path).open() as transcript_file:
            for line in transcript_file:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") == "system" and entry.get("subtype") == "away_summary":
                    last_content = entry.get("content")
    except Exception:
        return None
    return last_content


def cold_summary_source(transcript_path: str | None, maximum_turns: int = 6) -> str | None:
    if not transcript_path or not Path(transcript_path).exists():
        return None
    texts: list[str] = []
    try:
        with Path(transcript_path).open() as transcript_file:
            for line in transcript_file:
                if len(texts) >= maximum_turns:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") not in ("user", "assistant"):
                    continue
                message = entry.get("message") or {}
                content = message.get("content")
                if isinstance(content, str):
                    texts.append(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            texts.append(block.get("text", ""))
                            break
    except Exception:
        return None
    joined = " ".join(text for text in texts if text).strip()
    return joined or None


def warm_summary_source(transcript_path: str | None, maximum_turns: int = 4) -> str | None:
    """The LAST maximum_turns of assistant text, in chronological order.

    A subagent has no internal_name or away_summary (those are session-only
    concepts), so get_agent_label used to fall back straight to
    cold_summary_source - the FIRST few turns. For a subagent dispatched by
    a skill template (task-reviewer, code-reviewer, subagent-driven-
    development), those first turns are the templated dispatch instructions
    ("Review one task... Follow the reviewer rubric at .../task-reviewer-
    prompt.md... Work in the git worktree... Do NOT cd... Do not commit..."),
    identical across every dispatch of that skill and specific to nothing.
    Checked against a sample of agent_labels.json (2026-09-04): about half
    of the labels were __no_label__, and the ones that did exist clustered
    into near-duplicates like "Revisão de implementação de tarefa" /
    "Revisar implementação de tarefa" - all from that same boilerplate
    prefix.
    The LAST assistant turns are the closest thing a subagent has to a
    session's away_summary: whatever it actually concluded, concretely
    ("Spec Compliance: Compliant - test-only diff confirmed empty...",
    "Caught. Now mutation 4: ... sign flip on duration"). get_agent_label
    tries this first and falls back to cold_summary_source only when a
    subagent produced no text at all (aborted before any output).
    """
    if not transcript_path or not Path(transcript_path).exists():
        return None
    texts: list[str] = []
    try:
        with Path(transcript_path).open() as transcript_file:
            lines = transcript_file.readlines()
        for line in reversed(lines):
            if len(texts) >= maximum_turns:
                break
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "assistant":
                continue
            message = entry.get("message") or {}
            content = message.get("content")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block.get("text", ""))
                        break
    except Exception:
        return None
    # Chronological order for the prompt, even though the scan above ran
    # newest-first - a label built from "conclusion, then earlier context"
    # reads worse to the summarizer than "context, then conclusion".
    joined = " ".join(text for text in reversed(texts) if text).strip()
    return joined or None


# Substrings (checked lowercased) that mean the model refused/couldn't
# summarize instead of producing a real label ("Não há texto para
# resumir", "I cannot complete this request...") - found 2026-07-23 in
# 3 of 10 real backfill calls. Caching these verbatim as a "label" is
# worse than having no label at all - they read as a real (if odd) label
# to anyone looking at the table, not as an error.
_REFUSAL_MARKERS = (
    "não há texto",
    "nao ha texto",
    "no text",
    "nenhum texto",
    "sem texto",
    "não foi fornecido",
    "nao foi fornecido",
    "not provided",
    "no content",
    "cannot complete",
    "i cannot",
    "i'm sorry",
    "im sorry",
    "desculpe",
    "please provide",
    "forneça o texto",
    "forneca o texto",
    "not enough information",
    "não tenho informações suficientes",
    "as an ai",
    "i don't have",
    "não consigo",
    "nao consigo",
    "i need more",
    "preciso de mais",
)


def _looks_like_bad_label(label: str) -> bool:
    lowered = label.lower()
    if any(marker in lowered for marker in _REFUSAL_MARKERS):
        return True
    # A real label is 3-5 words; anything this long is the model
    # rambling/explaining instead of labeling, even if it isn't an
    # outright refusal.
    return len(label) > 120


def _run_label_prompt(prompt: str) -> str | None:
    try:
        # `nice` so the labeling subprocess never competes for CPU with
        # interactive work (Vim/tmux/LSPs/the calling session itself) - each
        # call is a full Claude CLI (Node process) boot, not a cheap thread.
        #
        # --setting-sources "" --tools "" --system-prompt <minimal>
        # (2026-07-23, "você revisou uso de tokens nesse rotulamento?"):
        # without these, every call auto-discovers Alberto's global
        # CLAUDE.md + the full default Claude Code system prompt as
        # cache-creation input - tens of thousands of cache tokens for a
        # single "summarize in 3-5 words" call. This task needs zero
        # project context and zero tools, so replacing the system prompt
        # entirely and disabling setting-source/tool loading drops that to
        # zero cache tokens - roughly a tenth of the cost per label,
        # multiplied across every session in the backfill.
        result = subprocess.run(
            [
                "nice",
                "-n",
                "15",
                "claude",
                "-p",
                prompt,
                "--model",
                "haiku",
                "--setting-sources",
                "",
                "--tools",
                "",
                "--system-prompt",
                "You are a concise text summarizer.",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    label = result.stdout.strip().strip('"').strip("'")
    return label or None


# One (initial prompt, retry prompt) pair per language. "pt" is byte-
# identical to the two prompts this module used before languages existed -
# every label cached before this feature shipped was generated with these
# exact words, and re-generating an already-cached label in Portuguese
# (e.g. after a cache miss on a new field) must read the same to a human
# comparing old and new labels side by side.
LABEL_PROMPTS: dict[str, tuple[str, str]] = {
    "pt": (
        "Resuma o texto abaixo em um rotulo curto de 3 a 5 palavras, "
        "em portugues, sem pontuacao final, responda so com o rotulo:\n\n",
        "O texto abaixo pode ser um trecho de codigo, log, ou erro - "
        "mesmo assim, gere um rotulo curto de 3 a 5 palavras em "
        "portugues descrevendo do que se trata. NUNCA recuse, NUNCA "
        "peca mais contexto, NUNCA explique - responda apenas com o "
        "rotulo, mesmo que o texto pareca incompleto:\n\n",
    ),
    "en": (
        "Summarize the text below as a short label of 3 to 5 words, in English, "
        "no final punctuation, reply with the label only:\n\n",
        "The text below may be a code, log or error excerpt - even so, produce a "
        "short label of 3 to 5 words in English describing what it is about. "
        "NEVER refuse, NEVER ask for more context, NEVER explain - reply with the "
        "label only, even if the text looks incomplete:\n\n",
    ),
}


def compress_to_label(source_text: Any, *, lang: str) -> str | None:
    if not source_text:
        return None
    if not isinstance(source_text, str):
        return None
    prompt_prefix, retry_prefix = LABEL_PROMPTS[lang]
    prompt = prompt_prefix + source_text[:2000]
    label = _run_label_prompt(prompt)

    # One retry, with a firmer prompt, when the first attempt refused or
    # rambled (2026-07-23) - "tentando endereçar a resposta pra conseguir
    # um bom rótulo". Real second-order source text (a truncated log/code
    # excerpt, not a natural-language summary already) is exactly the
    # case that confuses the model into refusing or over-explaining; a
    # blunter instruction that pre-empts "I can't find text to
    # summarize" tends to recover a usable label instead of wasting the
    # attempt.
    if label is None or _looks_like_bad_label(label):
        retry_prompt = retry_prefix + source_text[:2000]
        retry_label = _run_label_prompt(retry_prompt)
        if retry_label and not _looks_like_bad_label(retry_label):
            label = retry_label
        elif label is not None and _looks_like_bad_label(label):
            # Both attempts were bad - "se for difícil, tratando ele pra
            # não aparecer também": no label is better than a refusal
            # text masquerading as one.
            label = None

    if not label:
        return None
    # The model doesn't always follow "responda só com o rótulo" -
    # sometimes it echoes back a whole clarifying question or a
    # multi-paragraph answer instead of a 3-5 word label (found
    # 2026-07-21: one label was several sentences long, breaking the
    # session table's layout). Truncate defensively rather than trusting
    # the model's output length - this is a display safeguard, not an
    # attempt to fix the model's behavior.
    maximum_label_chars = 60
    if len(label) > maximum_label_chars:
        label = label[:maximum_label_chars].rsplit(" ", 1)[0].rstrip(".,;:") + "…"
    return label or None


def _migrate(raw: Any) -> LabelCache:
    """Turns whatever JSON a cache file held into today's id -> language ->
    label shape.

    A pre-i18n file is id -> label (a plain string), always meaning
    Portuguese - every one of those becomes {"pt": label}, one line, nothing
    special-cased: the existing get_task_label/get_agent_label pipeline
    already produces the right result for English when only "pt" is cached
    (see the "Label cache" section of the design spec). A dict value is
    already-migrated shape and is kept, dropping any pair that isn't a
    str:str language:label mapping. Anything else (not a dict at all, or a
    per-id value that is neither a string nor a dict) is dropped rather than
    raising - reads stay tolerant, as they always have.
    """
    if not isinstance(raw, dict):
        return {}
    migrated: LabelCache = {}
    for key, value in raw.items():
        if isinstance(value, str):
            migrated[key] = {"pt": value}
        elif isinstance(value, dict):
            migrated[key] = {
                lang: label
                for lang, label in value.items()
                if isinstance(lang, str) and isinstance(label, str)
            }
    return migrated


def load_label_cache() -> LabelCache:
    if not Path(TASK_LABELS_FILE).exists():
        return {}
    try:
        with Path(TASK_LABELS_FILE).open() as cache_file:
            raw = json.load(cache_file)
    except (json.JSONDecodeError, OSError):
        return {}
    return _migrate(raw)


_NO_LABEL = "__no_label__"

# Guards every write to a label cache dict AND every json.dump of one.
# dashboard_html.py's main() shares one cache dict between the main
# thread (which periodically calls save_*_cache while labeling is still
# in flight - see its SAVE_EVERY comment) and a single ThreadPoolExecutor
# worker thread (which writes cache[key] = ... as each get_*_label call
# resolves). Without this lock, json.dump's internal dict iteration can
# observe the worker thread inserting a new key mid-iteration and raise
# "RuntimeError: dictionary changed size during iteration" - reproduced
# directly (2026-09-08) running dashboard_html.py for real against a
# cache with genuinely new, uncached agent ids to write. The same lock now
# also guards the one-time pre-i18n backup and the atomic replace in
# _atomic_save, for the same reason: those touch the file another thread's
# save could be racing to write too.
_CACHE_LOCK = threading.Lock()


def _holds_old_shape(path: Path) -> bool:
    """True when the file at `path` is still id -> label (a plain string) -
    the shape every cache had before languages existed. Used only to decide
    whether a pre-i18n backup is still owed; a file that fails to parse is
    treated as not-old-shape, the same tolerant default reads use."""
    try:
        with path.open() as cache_file:
            raw = json.load(cache_file)
    except (json.JSONDecodeError, OSError):
        return False
    return isinstance(raw, dict) and any(isinstance(value, str) for value in raw.values())


def _atomic_save(path: str, cache: LabelCache) -> None:
    """Writes `cache` to `path` without ever leaving it half-written, and -
    the first time a file in the old id -> label shape is about to be
    overwritten - keeps one copy of it as `<path>.pre-i18n`, made once and
    never overwritten again.

    The temp file is created with tempfile.mkstemp, not a fixed `<path>.tmp`
    name: dashboard_html.py and dashboard_tui.py are separate PROCESSES,
    and a fixed name is a race between them - two processes saving the same
    cache around the same time would share one temp file, and whichever
    finished its os.replace first would leave the other's `open("w")` (or
    the second os.replace) either truncating a file that's already gone or
    silently clobbering the target with a half-written one. mkstemp opens
    its file with O_EXCL in the SAME directory as the target (so os.replace
    stays an atomic same-filesystem rename), guaranteeing a unique name per
    call regardless of what any other process is doing at the same moment.
    The temp file is removed if the write fails partway, so a crash never
    leaves a stray temp file behind.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target.with_name(target.name + ".pre-i18n")
    with _CACHE_LOCK:
        if target.exists() and not backup.exists() and _holds_old_shape(target):
            shutil.copy2(target, backup)
        file_descriptor, temporary_file_name = tempfile.mkstemp(
            dir=target.parent, prefix=target.name + ".", suffix=".tmp"
        )
        temporary_file_path = Path(temporary_file_name)
        try:
            with os.fdopen(file_descriptor, "w") as temporary_file:
                json.dump(cache, temporary_file, indent=2)
            # os.replace, not Path.replace (PTH105): a failure test simulates
            # a disk-full replace by monkeypatching this exact name.
            os.replace(temporary_file_path, target)  # noqa: PTH105
        except BaseException:
            temporary_file_path.unlink(missing_ok=True)
            raise


def save_label_cache(cache: LabelCache) -> None:
    _atomic_save(TASK_LABELS_FILE, cache)


class LabelBudget:
    # Caps how many NEW labels (each one a real `claude -p --model haiku`
    # subprocess call, i.e. real API usage) a single run generates -
    # 2026-07-22 feedback: the backfill was burning real quota, and
    # unlimited runs kept getting killed mid-flight anyway (session-limit
    # boundaries), never actually finishing. A bounded run completes for
    # real within one invocation instead of always being cut off, and
    # whatever's left over just waits for the next run - the cache means
    # nothing already-labeled gets redone. None = no cap (old behavior).
    def __init__(self, limit: int | None):
        self.remaining = limit
        self._lock = threading.Lock()

    def try_consume(self) -> bool:
        if self.remaining is None:
            return True
        with self._lock:
            if self.remaining > 0:
                self.remaining -= 1
                return True
            return False


def _other_language_label(cache: LabelCache, entry_id: str) -> str | None:
    """A real, model-written (or reused) label already cached for `entry_id`
    under any language OTHER than the one being asked for right now - the
    caller has already checked the requested language isn't cached, so
    every value here belongs to some other language. Returns None if the
    id has no entry, or every entry it has is the `_NO_LABEL` marker."""
    return next(
        (value for value in cache.get(entry_id, {}).values() if value != _NO_LABEL),
        None,
    )


def get_task_label(
    record: dict[str, Any], cache: LabelCache, budget: LabelBudget, *, lang: str
) -> str | None:
    session_id = record.get("session_id")
    if not session_id:
        return None
    if lang in cache.get(session_id, {}):
        cached = cache[session_id][lang]
        return None if cached == _NO_LABEL else cached

    internal_name = record.get("internal_name")
    if internal_name and len(internal_name.split()) <= 6:
        internal_name = cast(str, internal_name)
        with _CACHE_LOCK:
            cache.setdefault(session_id, {})[lang] = internal_name
        return internal_name

    source = internal_name or find_away_summary(record.get("transcript_path"))
    if not source:
        source = cold_summary_source(record.get("transcript_path"))
    if not source:
        other_label = _other_language_label(cache, session_id)
        if other_label is not None:
            # No source text left to regenerate from (Claude Code already
            # pruned the transcript) - but this session already has a real,
            # model-written label in another language. Caching _NO_LABEL
            # for the requested language here would be permanent: the
            # session's only source is gone, so a future run could never
            # recover it either. Returning the other language's label
            # (untranslated, exactly as a session name would be) beats
            # blanking a label that already exists. Deliberately not
            # written to the cache for `lang` - this is a display fallback,
            # not a cached fact about `lang`, so nothing here can go stale.
            return other_label
        with _CACHE_LOCK:
            cache.setdefault(session_id, {})[lang] = _NO_LABEL
        return None

    # Budget exhausted: leave this session UNCACHED (don't write
    # _NO_LABEL) so the next run picks it up instead of skipping it
    # forever.
    if not budget.try_consume():
        return None

    label = compress_to_label(source, lang=lang)
    with _CACHE_LOCK:
        cache.setdefault(session_id, {})[lang] = label or _NO_LABEL
    return label


def load_agent_label_cache() -> LabelCache:
    if not Path(AGENT_LABELS_FILE).exists():
        return {}
    try:
        with Path(AGENT_LABELS_FILE).open() as cache_file:
            raw = json.load(cache_file)
    except (json.JSONDecodeError, OSError):
        return {}
    return _migrate(raw)


def save_agent_label_cache(cache: LabelCache) -> None:
    _atomic_save(AGENT_LABELS_FILE, cache)


def get_agent_label(
    subagent_record: dict[str, Any], cache: LabelCache, budget: LabelBudget, *, lang: str
) -> str | None:
    agent_id = subagent_record.get("agent_id")
    if not agent_id:
        return None
    if lang in cache.get(agent_id, {}):
        cached = cache[agent_id][lang]
        return None if cached == _NO_LABEL else cached

    transcript_path = subagent_record.get("agent_transcript_path")
    # warm_summary_source (the LAST turns - what the subagent actually
    # concluded) first, cold_summary_source (the FIRST turns - usually a
    # skill's templated dispatch instructions for a subagent) only when
    # the subagent produced no text at all. See warm_summary_source's own
    # docstring for the real-data evidence behind this ordering.
    source = warm_summary_source(transcript_path) or cold_summary_source(transcript_path)
    if not source:
        other_label = _other_language_label(cache, agent_id)
        if other_label is not None:
            # Same reasoning as get_task_label's identical fallback: no
            # source left to regenerate from, but a real label already
            # exists in another language - return it rather than caching a
            # permanent, unrecoverable _NO_LABEL. Not written to the cache
            # for `lang`.
            return other_label
        with _CACHE_LOCK:
            cache.setdefault(agent_id, {})[lang] = _NO_LABEL
        return None

    if not budget.try_consume():
        return None

    label = compress_to_label(source, lang=lang)
    with _CACHE_LOCK:
        cache.setdefault(agent_id, {})[lang] = label or _NO_LABEL
    return label


def group_subagents_by_session(
    subagent_records: list[dict[str, Any]],
) -> dict[Any, list[dict[str, Any]]]:
    grouped: dict[Any, list[dict[str, Any]]] = {}
    for record in subagent_records:
        session_id = record.get("session_id")
        if session_id is None:
            continue
        grouped.setdefault(session_id, []).append(record)
    return grouped
