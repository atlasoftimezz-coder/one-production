"""Connected AI accounts (plan R1).

The registry (data_dir/accounts.json) holds labels and settings only. Secrets never go in
it: CLI logins live in each account's own profile folder, managed by the official client;
API keys live in Windows Credential Manager via `keyring`.
"""
from __future__ import annotations

import json
import secrets
import shutil
import threading
import time
from pathlib import Path

import keyring

from . import models as M
from . import terms

PROVIDERS = {
    "claude": {"label": "Claude", "needs_login": True, "models": ["sonnet", "opus", "haiku", "fable"]},
    "codex": {"label": "Codex (ChatGPT)", "needs_login": True, "models": []},
    "anthropic_api": {"label": "Anthropic API key", "needs_login": False, "models": ["sonnet", "opus", "haiku", "fable"]},
}
KEYRING_SERVICE = "OneProduction"
DEFAULT_WARN, DEFAULT_SWITCH = 70, 85
SUBSCRIPTIONS = {"claude", "codex"}
# Plan D7/R8: hopping between two accounts of the SAME subscription service to get past its
# limit ("chaining") is allowed only when the owner switches it on AND the terms check
# (app/terms.json, docs/terms-check.md) says the provider allows it. On 2026-10-06 neither
# Anthropic nor OpenAI does, so it stays off. Switching to a different service, or to a
# pay-per-use API key, is ordinary use and is allowed.
SAME_SERVICE_SWITCHING = False          # the default; the effective rule is chaining_allowed()


def chaining_allowed(provider: str, owner_allows: bool = False) -> bool:
    return SAME_SERVICE_SWITCHING or terms.chaining_allowed(provider, owner_allows)


def switch_allowed(from_acc: dict, to_acc: dict, owner_allows: bool = False) -> bool:
    """May work move from one account to the other after a limit? (D7: not between two
    accounts of the same subscription service unless the owner and the provider's terms allow it.)"""
    if to_acc["id"] == from_acc["id"]:
        return False
    same_service = to_acc["provider"] in SUBSCRIPTIONS and to_acc["provider"] == from_acc["provider"]
    return not same_service or chaining_allowed(to_acc["provider"], owner_allows)


CAP_FIELDS = ("cap_task_tokens", "cap_day_tokens", "cap_day_usd")


class AccountError(ValueError):
    pass


def key_name(acc_id: str) -> str:
    return f"anthropic-api:{acc_id}"


def token_name(acc_id: str) -> str:
    return f"claude-token:{acc_id}"


TOKEN_PREFIX = "sk-ant-oat"             # what `claude setup-token` prints
API_KEY_PREFIX = "sk-ant-api"


def check_token(token: str) -> str:
    """A Claude sign-in token from `claude setup-token` (the account owner's own subscription)."""
    token = (token or "").strip()
    if token.startswith(API_KEY_PREFIX):
        raise AccountError("that is an API key, not a sign-in token - add it as an Anthropic API key")
    if not token.startswith(TOKEN_PREFIX) or len(token) < 40 or any(c.isspace() for c in token):
        raise AccountError("that doesn't look like a sign-in token: run `claude setup-token` and paste "
                           "the whole line that starts with sk-ant-oat")
    return token


def claude_env(acc: dict) -> dict[str, str]:
    """Environment for Claude Code on a Claude login: its own config folder, plus the sign-in
    token when the account uses one instead of the browser login. Always set (empty = none,
    verified on Claude Code 2.1.284), so a token in the PC's own environment can't sign every
    account in as the same person."""
    token = Accounts.oauth_token(acc) if acc.get("token_hint") else None
    return {"CLAUDE_CONFIG_DIR": acc["profile_dir"], "CLAUDE_CODE_OAUTH_TOKEN": token or ""}


class Accounts:
    def __init__(self, data_dir: Path):
        self.file = data_dir / "accounts.json"
        self.profiles = data_dir / "profiles"
        self.profiles.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ---- storage
    def _load(self) -> list[dict]:
        if not self.file.is_file():
            return []
        return json.loads(self.file.read_text(encoding="utf-8"))

    def _save(self, items: list[dict]) -> None:
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=1), encoding="utf-8")
        tmp.replace(self.file)

    def list(self) -> list[dict]:
        with self._lock:
            return sorted(self._load(), key=lambda a: a["order"])

    def get(self, acc_id: str) -> dict:
        for a in self.list():
            if a["id"] == acc_id:
                return a
        raise AccountError("unknown account")

    def _update(self, acc_id: str, **changes) -> dict:
        with self._lock:
            items = self._load()
            for a in items:
                if a["id"] == acc_id:
                    a.update(changes)
                    self._save(items)
                    return a
        raise AccountError("unknown account")

    # ---- create / remove
    def add(self, provider: str, label: str = "", api_key: str | None = None,
            model: str | None = None) -> dict:
        if provider not in PROVIDERS:
            raise AccountError("unknown provider")
        if provider == "anthropic_api" and not (api_key or "").strip():
            raise AccountError("paste the API key")
        acc_id = f"{provider.replace('_', '-')}-{secrets.token_hex(3)}"
        acc = {"id": acc_id, "provider": provider, "label": (label or "").strip()[:60],
               "email": None, "plan": None, "model": model or (PROVIDERS[provider]["models"] or [None])[0],
               "effort": None, "enabled": True, "created": time.time(), "status": "unchecked", "reason": None,
               "rate_limit": None, "profile_dir": None, "key_hint": None,
               "warn_pct": DEFAULT_WARN, "switch_pct": DEFAULT_SWITCH, "budget_tokens_5h": None}
        if PROVIDERS[provider]["needs_login"]:
            folder = self.profiles / acc_id
            folder.mkdir(parents=True)
            acc["profile_dir"] = str(folder)
        else:
            key = api_key.strip()
            keyring.set_password(KEYRING_SERVICE, key_name(acc_id), key)
            acc["key_hint"] = "…" + key[-4:]
        with self._lock:
            items = self._load()
            acc["order"] = max([a["order"] for a in items], default=-1) + 1
            items.append(acc)
            self._save(items)
        return acc

    def remove(self, acc_id: str) -> None:
        acc = self.get(acc_id)
        with self._lock:
            self._save([a for a in self._load() if a["id"] != acc_id])
        if acc["provider"] == "anthropic_api":
            try:
                keyring.delete_password(KEYRING_SERVICE, key_name(acc_id))
            except keyring.errors.PasswordDeleteError:
                pass
        if acc.get("token_hint"):
            try:
                keyring.delete_password(KEYRING_SERVICE, token_name(acc_id))
            except keyring.errors.PasswordDeleteError:
                pass
        if acc.get("profile_dir"):
            folder = Path(acc["profile_dir"]).resolve()
            if folder.parent == self.profiles.resolve():      # never delete outside profiles\
                shutil.rmtree(folder, ignore_errors=True)

    def set_enabled(self, acc_id: str, enabled: bool) -> dict:
        return self._update(acc_id, enabled=bool(enabled))

    def rename(self, acc_id: str, label: str) -> dict:
        return self._update(acc_id, label=(label or "").strip()[:60])

    def set_model(self, acc_id: str, model: str | None) -> dict:
        acc = self.get(acc_id)
        model = model or None                       # "" = the client's default (Codex)
        if acc["provider"] != "codex" and model is None:
            raise AccountError("unknown model")
        try:
            M.check(acc["provider"], model, None)
        except ValueError as e:
            raise AccountError(str(e))
        return self._update(acc_id, model=model)

    def set_effort(self, acc_id: str, effort: str | None) -> dict:
        """None / "auto" = the client's own default."""
        acc = self.get(acc_id)
        effort = None if effort in (None, "", "auto") else effort
        try:
            M.check(acc["provider"], None, effort)
        except ValueError as e:
            raise AccountError(str(e))
        return self._update(acc_id, effort=effort)

    def set_codex_models(self, acc_id: str, models: list[dict]) -> dict:
        return self._update(acc_id, codex_models=models, codex_models_at=time.time())

    def set_limits(self, acc_id: str, warn_pct=None, switch_pct=None, budget_tokens_5h=-1) -> dict:
        acc = self.get(acc_id)
        warn = acc.get("warn_pct", DEFAULT_WARN) if warn_pct is None else int(warn_pct)
        switch = acc.get("switch_pct", DEFAULT_SWITCH) if switch_pct is None else int(switch_pct)
        if not (1 <= warn < switch <= 100):
            raise AccountError("the warning level must be below the switch level (1-100)")
        changes = {"warn_pct": warn, "switch_pct": switch}
        if budget_tokens_5h != -1:
            if budget_tokens_5h is not None and int(budget_tokens_5h) <= 0:
                raise AccountError("the token budget must be a positive number")
            changes["budget_tokens_5h"] = None if budget_tokens_5h is None else int(budget_tokens_5h)
        return self._update(acc_id, **changes)

    def set_caps(self, acc_id: str, caps: dict) -> dict:
        """Spend caps (R10). A missing key leaves that cap alone; null clears it."""
        self.get(acc_id)
        changes = {}
        for k in CAP_FIELDS:
            if k not in caps:
                continue
            v = caps[k]
            if v is not None:
                v = float(v) if k == "cap_day_usd" else int(v)
                if v <= 0:
                    raise AccountError("a spend cap must be a positive number (or empty for no cap)")
            changes[k] = v
        return self._update(acc_id, **changes)

    def reorder(self, ids: list[str]) -> list[dict]:
        with self._lock:
            items = self._load()
            known = {a["id"] for a in items}
            if set(ids) != known:
                raise AccountError("the new order must list every account exactly once")
            pos = {i: n for n, i in enumerate(ids)}
            for a in items:
                a["order"] = pos[a["id"]]
            self._save(items)
        return self.list()

    def record_status(self, acc_id: str, result: dict) -> dict:
        changes = {"status": "ok" if result.get("ok") else "error",
                   "reason": None if result.get("ok") else result.get("reason"),
                   "checked": time.time()}
        if result.get("email"):
            changes["email"] = result["email"]
        if result.get("plan"):
            changes["plan"] = result["plan"]
        if result.get("method"):
            changes["method"] = result["method"]
        return self._update(acc_id, **changes)

    def record_rate_limit(self, acc_id: str, windows: dict, status: str = "allowed",
                          at: float | None = None) -> None:
        self._update(acc_id, rate_limit={"windows": windows, "status": status, "at": at or time.time()})

    def stop_live_usage(self, acc_id: str, reason: str) -> None:
        self._update(acc_id, live_usage_off=reason)

    def mark_auth_error(self, acc_id: str, reason: str) -> None:
        self._update(acc_id, status="error", reason=reason)

    def set_token(self, acc_id: str, token: str) -> dict:
        """Use a sign-in token from `claude setup-token` instead of the browser login. The
        token goes to Windows Credential Manager; the registry keeps only its last 4 characters."""
        acc = self.get(acc_id)
        if acc["provider"] != "claude":
            raise AccountError("sign-in tokens are only for Claude logins")
        token = check_token(token)
        keyring.set_password(KEYRING_SERVICE, token_name(acc_id), token)
        return self._update(acc_id, token_hint="…" + token[-4:])

    def clear_token(self, acc_id: str) -> dict:
        self.get(acc_id)
        try:
            keyring.delete_password(KEYRING_SERVICE, token_name(acc_id))
        except keyring.errors.PasswordDeleteError:
            pass
        return self._update(acc_id, token_hint=None)

    @staticmethod
    def api_key(acc: dict) -> str | None:
        return keyring.get_password(KEYRING_SERVICE, key_name(acc["id"]))

    @staticmethod
    def oauth_token(acc: dict) -> str | None:
        return keyring.get_password(KEYRING_SERVICE, token_name(acc["id"]))

    @staticmethod
    def public(acc: dict) -> dict:
        """What the browser may see: no secrets, no absolute paths."""
        return {k: v for k, v in acc.items() if k not in ("profile_dir",)}
