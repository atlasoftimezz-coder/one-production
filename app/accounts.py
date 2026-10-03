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

PROVIDERS = {
    "claude": {"label": "Claude", "needs_login": True, "models": ["sonnet", "opus", "haiku"]},
    "codex": {"label": "Codex (ChatGPT)", "needs_login": True, "models": []},
    "anthropic_api": {"label": "Anthropic API key", "needs_login": False, "models": ["sonnet", "opus", "haiku"]},
}
KEYRING_SERVICE = "OneProduction"


class AccountError(ValueError):
    pass


def key_name(acc_id: str) -> str:
    return f"anthropic-api:{acc_id}"


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
               "enabled": True, "created": time.time(), "status": "unchecked", "reason": None,
               "rate_limit": None, "profile_dir": None, "key_hint": None}
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
        allowed = PROVIDERS[acc["provider"]]["models"]
        if allowed and model not in allowed:
            raise AccountError("unknown model")
        return self._update(acc_id, model=model)

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

    def record_rate_limit(self, acc_id: str, windows: dict, status: str) -> None:
        self._update(acc_id, rate_limit={"windows": windows, "status": status, "at": time.time()})

    def mark_auth_error(self, acc_id: str, reason: str) -> None:
        self._update(acc_id, status="error", reason=reason)

    @staticmethod
    def api_key(acc: dict) -> str | None:
        return keyring.get_password(KEYRING_SERVICE, key_name(acc["id"]))

    @staticmethod
    def public(acc: dict) -> dict:
        """What the browser may see: no secrets, no absolute paths."""
        return {k: v for k, v in acc.items() if k not in ("profile_dir",)}
