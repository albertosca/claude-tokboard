"""Synthetic transcript entries for the per-call store tests."""

import json
from pathlib import Path
from typing import Any


def assistant_entry(
    message_id: str,
    *,
    input_tokens: int = 1,
    output_tokens: int = 10,
    cache_read: int = 50,
    cache_1h: int = 100,
    cache_5m: int = 0,
    timestamp: str = "2026-10-01T10:00:00.000Z",
    session_id: str = "session-1",
    sidechain: bool = False,
    model: str = "claude-sonnet-5-5",
    version: str = "2.1.300",
) -> dict[str, Any]:
    return {
        "type": "assistant",
        "uuid": f"uuid-{message_id}",
        "requestId": f"request-{message_id}",
        "sessionId": session_id,
        "timestamp": timestamp,
        "isSidechain": sidechain,
        "version": version,
        "message": {
            "id": message_id,
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_1h + cache_5m,
                "cache_creation": {
                    "ephemeral_1h_input_tokens": cache_1h,
                    "ephemeral_5m_input_tokens": cache_5m,
                },
            },
        },
    }


def limit_entry(
    *,
    timestamp: str = "2026-10-07T12:09:17.059Z",
    rate_limit_type: str = "seven_day",
    resets_at: int | None = 1791781200,
) -> dict[str, Any]:
    limits: dict[str, Any] = {
        "status": "rejected",
        "rateLimitType": rate_limit_type,
        "overageStatus": "rejected",
        "overageDisabledReason": "org_level_disabled_until",
    }
    if resets_at is not None:
        limits["resetsAt"] = resets_at
    return {
        "type": "assistant",
        "timestamp": timestamp,
        "isApiErrorMessage": True,
        "error": "rate_limit",
        "message": {"id": "error-1", "model": "<synthetic>", "usage": {"input_tokens": 0}},
        "quotaLimits": limits,
    }


def write_transcript(
    path: Path, entries: list[dict[str, Any]], *, final_newline: bool = True
) -> None:
    text = "\n".join(json.dumps(entry) for entry in entries)
    path.write_text(text + ("\n" if final_newline else ""))
