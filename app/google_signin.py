"""Google sign-in for the app (plan R7, P12): an optional way to open One Production, next to the PIN.

The owner creates an OAuth client of type "Desktop app" in their own Google Cloud project and
enters its client ID (+ secret, kept in Windows Credential Manager) and the email addresses
allowed in. Then the lock screen offers "Sign in with Google":

  1. start()   PKCE verifier + state; a one-time listener on http://127.0.0.1:<random port>/
               (loopback redirect, as Google prescribes for installed apps); returns the
               consent URL the browser opens.
  2. Google redirects the browser to the listener with ?code&state; the listener checks the state,
     keeps the code and closes.
  3. status()  exchanges the code (+ verifier) for tokens, verifies the ID token with Google's
               tokeninfo endpoint (audience = our client ID, issuer = Google, email verified, not
               expired) and checks the email against the allowed list -> the app unlocks.
Nothing but the client ID / allowed list (profile.json) and the secret (Credential Manager) is
kept; the tokens are dropped right after the check. This is NOT a provider (Claude/Codex) login -
those stay with their own official clients (D2, D3).
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import keyring

from .accounts import KEYRING_SERVICE

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
SECRET_NAME = "google-oauth-secret"
WAIT = 300                                   # seconds a started sign-in stays open
CLIENT_ID_END = ".apps.googleusercontent.com"
DONE_PAGE = (b"<!doctype html><meta charset=utf-8><title>One Production</title><body style='font:16px system-ui;"
             b"margin:3em'><h2>Signed in</h2><p>You can close this tab and go back to One Production.</p>")
BAD_PAGE = b"<!doctype html><meta charset=utf-8><title>One Production</title><body>This sign-in link is not valid any more."


class SignInError(Exception):
    pass


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class _Listener:
    """One-shot loopback HTTP server for Google's redirect."""

    def __init__(self, state: str):
        self.state, self.code, self.error = state, None, None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):                                    # noqa: N802 - http.server API
                q = parse_qs(urlparse(self.path).query)
                ok = q.get("state", [""])[0] == outer.state
                if ok and q.get("code"):
                    outer.code = q["code"][0]
                elif ok:
                    outer.error = q.get("error", ["Google did not send a code"])[0]
                self.send_response(200 if ok else 400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(DONE_PAGE if ok else BAD_PAGE)
                if ok:
                    threading.Thread(target=outer.server.shutdown, daemon=True).start()

            def log_message(self, *args):                       # keep codes out of the logs
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except OSError:
            pass


class GoogleSignIn:
    def __init__(self, profile, transport: httpx.BaseTransport | None = None):
        self.profile, self.transport = profile, transport
        self.pending: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- the owner's settings
    def config(self) -> dict:
        return self.profile.google_config()

    @staticmethod
    def secret() -> str | None:
        try:
            return keyring.get_password(KEYRING_SERVICE, SECRET_NAME)
        except keyring.errors.KeyringError:
            return None

    def configure(self, client_id: str, client_secret: str | None, allowed: list[str], required: bool) -> dict:
        client_id = (client_id or "").strip()
        if not client_id.endswith(CLIENT_ID_END) or len(client_id) > 200:
            raise SignInError(f"The client ID ends in {CLIENT_ID_END} (Google Cloud > Credentials > OAuth client, type Desktop app).")
        emails = sorted({e.strip().lower() for e in allowed or [] if e and e.strip()})
        bad = [e for e in emails if "@" not in e or " " in e]
        if not emails or bad:
            raise SignInError("List at least one allowed email address" + (f" (not valid: {', '.join(bad)})" if bad else "") + ".")
        if client_secret:
            keyring.set_password(KEYRING_SERVICE, SECRET_NAME, client_secret.strip())
        elif not self.secret():
            raise SignInError("Paste the client secret too (it is kept in Windows Credential Manager).")
        return self.profile.set_google({"client_id": client_id, "allowed": emails, "required": bool(required)})

    def remove(self) -> dict:
        try:
            keyring.delete_password(KEYRING_SERVICE, SECRET_NAME)
        except keyring.errors.PasswordDeleteError:
            pass
        return self.profile.set_google(None)

    # ---------------------------------------------------------------- the flow
    def start(self) -> dict:
        cfg = self.config()
        if not cfg.get("client_id"):
            raise SignInError("Google sign-in is not set up.")
        with self._lock:
            for st in [s for s, p in self.pending.items() if p["until"] < time.time()]:
                self.pending.pop(st)["listener"].close()
            state = secrets.token_urlsafe(24)
            verifier, challenge = _pkce()
            listener = _Listener(state)
            redirect = f"http://127.0.0.1:{listener.port}/"
            self.pending[state] = {"verifier": verifier, "listener": listener, "redirect": redirect,
                                   "until": time.time() + WAIT, "result": None}
        url = AUTH_URL + "?" + urlencode({
            "client_id": cfg["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": "openid email",
            "code_challenge": challenge, "code_challenge_method": "S256", "state": state, "prompt": "select_account"})
        return {"url": url, "state": state}

    def status(self, state: str) -> dict:
        p = self.pending.get(state)
        if not p:
            return {"status": "failed", "error": "This sign-in is not open any more - start again."}
        if p["result"]:
            return p["result"]
        lst = p["listener"]
        if lst.error:
            return self._finish(state, {"status": "failed", "error": f"Google said: {lst.error}"})
        if not lst.code:
            if p["until"] < time.time():
                return self._finish(state, {"status": "failed", "error": "The sign-in timed out - start again."})
            return {"status": "waiting"}
        try:
            email = self._verify(lst.code, p)
        except SignInError as exc:
            return self._finish(state, {"status": "failed", "error": str(exc)})
        profile = self.profile.unlock_external(f"google:{email}")
        return self._finish(state, {"status": "done", "email": email, "profile": profile})

    def _finish(self, state: str, result: dict) -> dict:
        p = self.pending.get(state)
        if p:
            p["result"] = result
            p["listener"].close()
        return result

    def _verify(self, code: str, p: dict) -> str:
        cfg = self.config()
        secret = self.secret()
        try:
            with httpx.Client(transport=self.transport, timeout=20) as c:
                tok = c.post(TOKEN_URL, data={"code": code, "client_id": cfg["client_id"], "client_secret": secret or "",
                                              "code_verifier": p["verifier"], "grant_type": "authorization_code",
                                              "redirect_uri": p["redirect"]})
                if tok.status_code != 200:
                    raise SignInError(f"Google refused the sign-in code ({tok.status_code}).")
                id_token = (tok.json() or {}).get("id_token")
                if not id_token:
                    raise SignInError("Google sent no ID token.")
                info = c.get(TOKENINFO_URL, params={"id_token": id_token})
        except httpx.HTTPError as exc:
            raise SignInError(f"Google could not be reached ({exc.__class__.__name__}).") from None
        if info.status_code != 200:
            raise SignInError("Google did not confirm the ID token.")
        claims = info.json() or {}
        if claims.get("aud") != cfg["client_id"]:
            raise SignInError("The ID token was made for a different app.")
        if claims.get("iss") not in ISSUERS:
            raise SignInError("The ID token was not issued by Google.")
        if str(claims.get("email_verified")).lower() != "true":
            raise SignInError("That Google email address is not verified.")
        try:
            if int(claims.get("exp", 0)) < time.time():
                raise SignInError("The ID token has expired.")
        except ValueError:
            raise SignInError("The ID token has no valid expiry.") from None
        email = str(claims.get("email") or "").lower()
        if email not in cfg.get("allowed", []):
            raise SignInError(f"{email or 'That account'} is not on the allowed list.")
        return email
