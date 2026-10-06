"""Local profile and optional PIN lock (plan R7, local login for the MVP).

The session token already makes sure only the tab start.bat opened can talk to the app.
The PIN is for the person in front of the PC: with a PIN set, the app starts locked and
nothing but "unlock" works until the PIN is entered. "Lock now" locks it again.

    profile.json   {"name": ..., "pin": {"salt": hex, "hash": hex}}   - never the PIN itself
    5 wrong PINs   -> unlocking pauses for a minute (and doubles after each further miss)
Google sign-in (P12, app/google_signin.py) is a second way in: profile.json keeps its client ID,
the allowed emails and whether it is required at start - never a token.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import time
from pathlib import Path

PIN_RE = re.compile(r"^\d{4,12}$")
FREE_TRIES = 5


class ProfileError(Exception):
    pass


class Throttled(ProfileError):
    def __init__(self, seconds: float):
        super().__init__(f"too many wrong PINs - try again in {int(seconds) + 1} seconds")
        self.seconds = seconds


def _hash(pin: str, salt: bytes) -> bytes:
    return hashlib.scrypt(pin.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)


class Profile:
    def __init__(self, data_dir: Path):
        self.file = data_dir / "profile.json"
        self._lock = threading.Lock()
        self.unlocked = False
        self.fails = 0
        self.blocked_until = 0.0

    def _load(self) -> dict:
        if self.file.exists():
            try:
                return json.loads(self.file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return {"name": ""}

    def _save(self, data: dict) -> None:
        self.file.write_text(json.dumps(data), encoding="utf-8")

    @property
    def has_pin(self) -> bool:
        return bool(self._load().get("pin"))

    def google_config(self) -> dict:
        return dict(self._load().get("google") or {})

    def set_google(self, cfg: dict | None) -> dict:
        with self._lock:
            d = self._load()
            if cfg:
                d["google"] = cfg
            else:
                d.pop("google", None)
            self._save(d)
        return self.public()

    @property
    def locked(self) -> bool:
        return (self.has_pin or bool(self.google_config().get("required"))) and not self.unlocked

    def unlock_external(self, who: str) -> dict:
        """Unlocked by a verified Google sign-in (google_signin.py did the checks)."""
        with self._lock:
            self.fails, self.blocked_until, self.unlocked = 0, 0.0, True
            self.signed_in = who
        return self.public()

    def public(self) -> dict:
        d = self._load()
        g = d.get("google") or {}
        return {"name": d.get("name") or "", "has_pin": bool(d.get("pin")), "locked": self.locked,
                "google": {"configured": bool(g.get("client_id")), "required": bool(g.get("required")),
                           "client_id": g.get("client_id") or "", "allowed": list(g.get("allowed") or [])},
                "signed_in": getattr(self, "signed_in", "")}

    def set_name(self, name: str) -> dict:
        with self._lock:
            d = self._load()
            d["name"] = (name or "").strip()[:40]
            self._save(d)
        return self.public()

    def _check(self, d: dict, pin: str) -> bool:
        p = d.get("pin") or {}
        try:
            want, salt = bytes.fromhex(p["hash"]), bytes.fromhex(p["salt"])
        except (KeyError, ValueError):
            return False
        return hmac.compare_digest(_hash(pin or "", salt), want)

    def _throttle(self) -> None:
        wait = self.blocked_until - time.time()
        if wait > 0:
            raise Throttled(wait)

    def _miss(self) -> None:
        self.fails += 1
        if self.fails >= FREE_TRIES:
            self.blocked_until = time.time() + 60 * 2 ** (self.fails - FREE_TRIES)

    def set_pin(self, current: str | None, new: str | None) -> dict:
        """Set (no PIN yet), change (current + new) or remove (current, new=None)."""
        with self._lock:
            d = self._load()
            if d.get("pin"):
                self._throttle()
                if not self._check(d, current or ""):
                    self._miss()
                    raise ProfileError("the current PIN is wrong")
                self.fails = 0
            if new is None:
                d.pop("pin", None)
            else:
                if not PIN_RE.match(new):
                    raise ProfileError("a PIN is 4 to 12 digits")
                salt = os.urandom(16)
                d["pin"] = {"salt": salt.hex(), "hash": _hash(new, salt).hex()}
            self._save(d)
            self.unlocked = True                 # whoever just set or changed it is at the PC
        return self.public()

    def unlock(self, pin: str) -> dict:
        with self._lock:
            d = self._load()
            if not d.get("pin"):
                self.unlocked = True
                return self.public()
            self._throttle()
            if not self._check(d, pin or ""):
                self._miss()
                raise ProfileError("wrong PIN")
            self.fails, self.blocked_until, self.unlocked = 0, 0.0, True
        return self.public()

    def lock(self) -> dict:
        self.unlocked = False
        self.signed_in = ""
        return self.public()
