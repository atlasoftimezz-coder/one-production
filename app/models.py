"""Which model and which effort (thinking depth) each kind of account can use.

One table for every place the app calls a model: plain chat, agent mode and the team
(master + workers). The user picks per account; "auto" (None) leaves the client's default.

Claude logins and API keys share the Claude names. Verified 2026-10-05:
  Claude Code 2.1.284  --model <alias|full name>  --effort low|medium|high|xhigh|max
  Agent SDK 0.2.163    ClaudeAgentOptions(model=..., effort=...)
  Messages API         output_config.effort (Haiku 4.5 rejects it; Opus 5.5 defaults to medium)
Codex advertises its own models and efforts (app-server `model/list`); they are cached on the
account and refreshed on request, because they change with the user's plan.
"""
from __future__ import annotations

import re

EFFORTS = ["low", "medium", "high", "xhigh", "max"]
CLAUDE_MODELS = [
    {"id": "opus", "label": "Opus 5.5", "api": "claude-opus-5-5", "efforts": EFFORTS},
    {"id": "sonnet", "label": "Sonnet 5.5", "api": "claude-sonnet-5-5", "efforts": EFFORTS},
    {"id": "haiku", "label": "Haiku 4.5", "api": "claude-haiku-4-5-20251001", "efforts": []},
    {"id": "fable", "label": "Fable 5.1", "api": "claude-fable-5-1", "efforts": EFFORTS},   # most capable, 2.5x Opus price
]
API_IDS = {m["id"]: m["api"] for m in CLAUDE_MODELS}
CLAUDE_IDS = [m["id"] for m in CLAUDE_MODELS]
CODEX_FALLBACK_EFFORTS = ["low", "medium", "high", "xhigh"]
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")       # what may reach a command line


def codex_models(raw: list[dict] | None) -> list[dict]:
    """app-server Model objects -> our shape. Hidden models are left out."""
    out = []
    for m in raw or []:
        mid = m.get("id") or m.get("model")
        if not mid or m.get("hidden") or not _SAFE.match(str(mid)):
            continue
        efforts = []
        for e in m.get("supportedReasoningEfforts") or []:
            name = e.get("reasoningEffort") if isinstance(e, dict) else e
            if isinstance(name, str) and _SAFE.match(name):
                efforts.append(name)
        out.append({"id": mid, "label": m.get("displayName") or mid, "efforts": efforts,
                    "default_effort": m.get("defaultReasoningEffort"), "default": bool(m.get("isDefault"))})
    return out


def choices(provider: str, cached_codex: list[dict] | None = None) -> dict:
    """What the pickers offer for one account."""
    if provider in ("claude", "anthropic_api"):
        return {"models": [{k: m[k] for k in ("id", "label", "efforts")} for m in CLAUDE_MODELS],
                "free_model": False}
    if provider == "codex":
        models = cached_codex or []
        dflt = next((m for m in models if m.get("default")), None)
        label = f"Codex default ({dflt['label']})" if dflt else "Codex default"
        efforts = (dflt or {}).get("efforts") or CODEX_FALLBACK_EFFORTS
        return {"models": [{"id": None, "label": label, "efforts": efforts}, *models],
                "free_model": True}
    return {"models": [], "free_model": False}


def check(provider: str, model: str | None, effort: str | None, cached_codex: list[dict] | None = None) -> None:
    """Raise ValueError for a choice this kind of account cannot use."""
    if provider in ("claude", "anthropic_api"):
        if model is not None and model not in CLAUDE_IDS:
            raise ValueError("unknown model")
        if effort is not None and effort not in EFFORTS:
            raise ValueError("unknown effort level")
    elif provider == "codex":
        for v, what in ((model, "model"), (effort, "effort level")):
            if v is not None and not _SAFE.match(v):
                raise ValueError(f"unknown {what}")
    elif model is not None or effort is not None:
        raise ValueError("this account has no model choice")


def effort_for(provider: str, model: str | None, effort: str | None,
               cached_codex: list[dict] | None = None) -> str | None:
    """The effort to actually send: dropped where the model would reject it (Haiku 4.5) or
    where Codex says the model does not offer it."""
    if not effort:
        return None
    if provider in ("claude", "anthropic_api"):
        m = next((x for x in CLAUDE_MODELS if x["id"] == (model or "sonnet")), None)
        return effort if m and effort in m["efforts"] else None
    if provider == "codex" and model and cached_codex:
        m = next((x for x in cached_codex if x["id"] == model), None)
        if m and m["efforts"] and effort not in m["efforts"]:
            return None
    return effort
