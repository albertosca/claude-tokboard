"""AI-generated session/subagent label caching and generation - the
only part of metrics_core that shells out to `claude -p`. See
LabelBudget for the per-run cap on real API calls this makes."""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any, cast

TASK_LABELS_FILE = str(Path("~/.claude/metrics/task_labels.json").expanduser())
AGENT_LABELS_FILE = str(Path("~/.claude/metrics/agent_labels.json").expanduser())


def find_away_summary(transcript_path: str | None) -> str | None:
    if not transcript_path or not Path(transcript_path).exists():
        return None
    last_content = None
    try:
        with Path(transcript_path).open() as f:
            for line in f:
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


def cold_summary_source(transcript_path: str | None, max_turns: int = 6) -> str | None:
    if not transcript_path or not Path(transcript_path).exists():
        return None
    texts: list[str] = []
    try:
        with Path(transcript_path).open() as f:
            for line in f:
                if len(texts) >= max_turns:
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
    joined = " ".join(t for t in texts if t).strip()
    return joined or None


def warm_summary_source(transcript_path: str | None, max_turns: int = 4) -> str | None:
    """The LAST max_turns of assistant text, in chronological order.

    A subagent has no internal_name or away_summary (those are session-only
    concepts), so get_agent_label used to fall back straight to
    cold_summary_source - the FIRST few turns. For a subagent dispatched by
    a skill template (task-reviewer, code-reviewer, subagent-driven-
    development), those first turns are the templated dispatch instructions
    ("Review one task... Follow the reviewer rubric at .../task-reviewer-
    prompt.md... Work in the git worktree... Do NOT cd... Do not commit..."),
    identical across every dispatch of that skill and specific to nothing.
    Measured directly against this repo's own real subagent transcripts
    (2026-09-04): 838 agent labels sampled from ~/.claude/metrics/
    agent_labels.json, 49% were __no_label__ and the labels that did exist
    clustered into near-duplicates like "Revisão de implementação de
    tarefa" / "Revisar implementação de tarefa" (14 of ~514 unique real
    labels) - all from that same boilerplate prefix.
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
        with Path(transcript_path).open() as f:
            lines = f.readlines()
        for line in reversed(lines):
            if len(texts) >= max_turns:
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
    joined = " ".join(t for t in reversed(texts) if t).strip()
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
        # cache-creation input - measured 13,037 cache_creation +
        # 15,907 cache_read tokens and $0.030 for a single "summarize in
        # 3-5 words" call. This task needs zero project context and zero
        # tools, so replacing the system prompt entirely and disabling
        # setting-source/tool loading drops that to 0 cache tokens and
        # $0.0032 - a ~9x cost cut per label, multiplied across every
        # session in the backfill.
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


def compress_to_label(source_text: Any) -> str | None:
    if not source_text:
        return None
    if not isinstance(source_text, str):
        return None
    prompt = (
        "Resuma o texto abaixo em um rotulo curto de 3 a 5 palavras, "
        "em portugues, sem pontuacao final, responda so com o rotulo:\n\n" + source_text[:2000]
    )
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
        retry_prompt = (
            "O texto abaixo pode ser um trecho de codigo, log, ou erro - "
            "mesmo assim, gere um rotulo curto de 3 a 5 palavras em "
            "portugues descrevendo do que se trata. NUNCA recuse, NUNCA "
            "peca mais contexto, NUNCA explique - responda apenas com o "
            "rotulo, mesmo que o texto pareca incompleto:\n\n" + source_text[:2000]
        )
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
    max_label_chars = 60
    if len(label) > max_label_chars:
        label = label[:max_label_chars].rsplit(" ", 1)[0].rstrip(".,;:") + "…"
    return label or None


def load_label_cache() -> dict[str, str]:
    if not Path(TASK_LABELS_FILE).exists():
        return {}
    try:
        with Path(TASK_LABELS_FILE).open() as f:
            return cast("dict[str, str]", json.load(f))
    except (json.JSONDecodeError, OSError):
        return {}


def save_label_cache(cache: dict[str, str]) -> None:
    Path(TASK_LABELS_FILE).parent.mkdir(parents=True, exist_ok=True)
    with Path(TASK_LABELS_FILE).open("w") as f, _CACHE_LOCK:
        json.dump(cache, f, indent=2)


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
# cache with genuinely new, uncached agent ids to write.
_CACHE_LOCK = threading.Lock()


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


def get_task_label(
    record: dict[str, Any], cache: dict[str, str], budget: LabelBudget
) -> str | None:
    session_id = record.get("session_id")
    if not session_id:
        return None
    if session_id in cache:
        cached = cache[session_id]
        return None if cached == _NO_LABEL else cached

    internal_name = record.get("internal_name")
    if internal_name and len(internal_name.split()) <= 6:
        internal_name = cast(str, internal_name)
        with _CACHE_LOCK:
            cache[session_id] = internal_name
        return internal_name

    source = internal_name or find_away_summary(record.get("transcript_path"))
    if not source:
        source = cold_summary_source(record.get("transcript_path"))
    if not source:
        with _CACHE_LOCK:
            cache[session_id] = _NO_LABEL
        return None

    # Budget exhausted: leave this session UNCACHED (don't write
    # _NO_LABEL) so the next run picks it up instead of skipping it
    # forever.
    if not budget.try_consume():
        return None

    label = compress_to_label(source)
    with _CACHE_LOCK:
        cache[session_id] = label or _NO_LABEL
    return label


def load_agent_label_cache() -> dict[str, str]:
    if not Path(AGENT_LABELS_FILE).exists():
        return {}
    try:
        with Path(AGENT_LABELS_FILE).open() as f:
            return cast("dict[str, str]", json.load(f))
    except (json.JSONDecodeError, OSError):
        return {}


def save_agent_label_cache(cache: dict[str, str]) -> None:
    Path(AGENT_LABELS_FILE).parent.mkdir(parents=True, exist_ok=True)
    with Path(AGENT_LABELS_FILE).open("w") as f, _CACHE_LOCK:
        json.dump(cache, f, indent=2)


def get_agent_label(
    subagent_record: dict[str, Any], cache: dict[str, str], budget: LabelBudget
) -> str | None:
    agent_id = subagent_record.get("agent_id")
    if not agent_id:
        return None
    if agent_id in cache:
        cached = cache[agent_id]
        return None if cached == _NO_LABEL else cached

    transcript_path = subagent_record.get("agent_transcript_path")
    # warm_summary_source (the LAST turns - what the subagent actually
    # concluded) first, cold_summary_source (the FIRST turns - usually a
    # skill's templated dispatch instructions for a subagent) only when
    # the subagent produced no text at all. See warm_summary_source's own
    # docstring for the real-data evidence behind this ordering.
    source = warm_summary_source(transcript_path) or cold_summary_source(transcript_path)
    if not source:
        with _CACHE_LOCK:
            cache[agent_id] = _NO_LABEL
        return None

    if not budget.try_consume():
        return None

    label = compress_to_label(source)
    with _CACHE_LOCK:
        cache[agent_id] = label or _NO_LABEL
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
