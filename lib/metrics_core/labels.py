"""AI-generated session/subagent label caching and generation - the
only part of metrics_core that shells out to `claude -p`. See
LabelBudget for the per-run cap on real API calls this makes."""

import json
import os
import subprocess
import threading

TASK_LABELS_FILE = os.path.expanduser("~/.claude/metrics/task_labels.json")
AGENT_LABELS_FILE = os.path.expanduser("~/.claude/metrics/agent_labels.json")


def find_away_summary(transcript_path):
    if not transcript_path or not os.path.exists(transcript_path):
        return None
    last_content = None
    try:
        with open(transcript_path) as f:
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


def cold_summary_source(transcript_path, max_turns=6):
    if not transcript_path or not os.path.exists(transcript_path):
        return None
    texts = []
    try:
        with open(transcript_path) as f:
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


def _looks_like_bad_label(label):
    lowered = label.lower()
    if any(marker in lowered for marker in _REFUSAL_MARKERS):
        return True
    # A real label is 3-5 words; anything this long is the model
    # rambling/explaining instead of labeling, even if it isn't an
    # outright refusal.
    return len(label) > 120


def _run_label_prompt(prompt):
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


def compress_to_label(source_text):
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


def load_label_cache():
    if not os.path.exists(TASK_LABELS_FILE):
        return {}
    try:
        with open(TASK_LABELS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_label_cache(cache):
    os.makedirs(os.path.dirname(TASK_LABELS_FILE), exist_ok=True)
    with open(TASK_LABELS_FILE, "w") as f:
        json.dump(cache, f, indent=2)


_NO_LABEL = "__no_label__"


class LabelBudget:
    # Caps how many NEW labels (each one a real `claude -p --model haiku`
    # subprocess call, i.e. real API usage) a single run generates -
    # 2026-07-22 feedback: the backfill was burning real quota, and
    # unlimited runs kept getting killed mid-flight anyway (session-limit
    # boundaries), never actually finishing. A bounded run completes for
    # real within one invocation instead of always being cut off, and
    # whatever's left over just waits for the next run - the cache means
    # nothing already-labeled gets redone. None = no cap (old behavior).
    def __init__(self, limit):
        self.remaining = limit
        self._lock = threading.Lock()

    def try_consume(self):
        if self.remaining is None:
            return True
        with self._lock:
            if self.remaining > 0:
                self.remaining -= 1
                return True
            return False


def get_task_label(record, cache, budget):
    session_id = record.get("session_id")
    if not session_id:
        return None
    if session_id in cache:
        cached = cache[session_id]
        return None if cached == _NO_LABEL else cached

    internal_name = record.get("internal_name")
    if internal_name and len(internal_name.split()) <= 6:
        cache[session_id] = internal_name
        return internal_name

    source = internal_name or find_away_summary(record.get("transcript_path"))
    if not source:
        source = cold_summary_source(record.get("transcript_path"))
    if not source:
        cache[session_id] = _NO_LABEL
        return None

    # Budget exhausted: leave this session UNCACHED (don't write
    # _NO_LABEL) so the next run picks it up instead of skipping it
    # forever.
    if not budget.try_consume():
        return None

    label = compress_to_label(source)
    cache[session_id] = label or _NO_LABEL
    return label


def load_agent_label_cache():
    if not os.path.exists(AGENT_LABELS_FILE):
        return {}
    try:
        with open(AGENT_LABELS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_agent_label_cache(cache):
    os.makedirs(os.path.dirname(AGENT_LABELS_FILE), exist_ok=True)
    with open(AGENT_LABELS_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def get_agent_label(subagent_record, cache, budget):
    agent_id = subagent_record.get("agent_id")
    if not agent_id:
        return None
    if agent_id in cache:
        cached = cache[agent_id]
        return None if cached == _NO_LABEL else cached

    source = cold_summary_source(subagent_record.get("agent_transcript_path"))
    if not source:
        cache[agent_id] = _NO_LABEL
        return None

    if not budget.try_consume():
        return None

    label = compress_to_label(source)
    cache[agent_id] = label or _NO_LABEL
    return label


def group_subagents_by_session(subagent_records):
    grouped = {}
    for record in subagent_records:
        session_id = record.get("session_id")
        if session_id is None:
            continue
        grouped.setdefault(session_id, []).append(record)
    return grouped
