"""One redaction function for everything that leaves memory: logs now, and later
handoff.md, the switch log, CSV exports and every file added to a backup zip.

Redacted spans read [REDACTED:<type>] so it is clear something was removed and what kind.
"""
from __future__ import annotations

import logging
import re
import threading

# Order matters: specific provider formats before the generic ones.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai-key",    re.compile(r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{20,}")),
    ("google-key",    re.compile(r"AIza[0-9A-Za-z_\-]{30,}")),
    ("github-token",  re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,}")),
    ("aws-key",       re.compile(r"AKIA[0-9A-Z]{16}")),
    ("jwt",           re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    # These two keep their label (group "keep") and blank only the value.
    ("bearer",        re.compile(r"(?i)(?P<keep>\bbearer\s+)[A-Za-z0-9._\-~+/]{16,}=*")),
    ("password",      re.compile(r"(?i)(?P<keep>\b(?:password|passwd|pwd|secret|api_key|apikey|token)"
                                 r"[\"']?\s*[:=]\s*[\"']?)[^\s\"',;]{6,}")),
]

_extra: set[str] = set()
_lock = threading.Lock()


def register_secret(value: str) -> None:
    """Redact this exact value everywhere from now on (e.g. the session token)."""
    if value and len(value) >= 8:
        with _lock:
            _extra.add(value)


def redact(text: str) -> str:
    if not text:
        return text
    with _lock:
        extras = sorted(_extra, key=len, reverse=True)
    for value in extras:
        text = text.replace(value, "[REDACTED:session-token]")
    for kind, pattern in _PATTERNS:
        if "keep" in pattern.groupindex:
            text = pattern.sub(lambda m, k=kind: m.group("keep") + f"[REDACTED:{k}]", text)
        else:
            text = pattern.sub(f"[REDACTED:{kind}]", text)
    return text


class RedactingFilter(logging.Filter):
    """Attach to log handlers so no record is written with a secret in it."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        clean = redact(msg)
        if clean != msg:
            record.msg, record.args = clean, None
        return True
