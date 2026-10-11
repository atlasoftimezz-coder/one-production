"""Stand-in for the real `claude` and `codex` CLIs, reproducing the output formats captured
from Claude Code 2.1.284 and codex-cli 0.157.1 on 2026-10-03.

    fake_cli.py claude <args...>      fake_cli.py codex <args...>

Behaviour comes from environment variables set by the tests:
    FAKE_MODE   ok | limit | auth | transient | midstream | slow
    FAKE_FAILS  for "transient": how many calls fail before one succeeds (default: always)
    FAKE_STATE  file counting calls (so tests can prove a prompt was or wasn't re-sent)
    FAKE_LOG    file the received stdin prompt is appended to
    FAKE_EMAIL  email to report after a fake login
    FAKE_UTIL   Claude five_hour utilisation to report (default 0.42 in replies, 0.34 in /usage)
    FAKE_CODEX_PCT  Codex 5-hour used_percent written to its session file (default 35)
    FAKE_REPLY  exact reply text for Claude (default: echoes the user's last line)
    FAKE_CODEX_REPLY  exact reply text for Codex (default: echoes the user's last line)
    FAKE_MODE_CLAUDE / FAKE_MODE_CODEX  FAKE_MODE for one of the two only (P11 hand-over)
    FAKE_WRITE  "path=text": a run that may edit (Claude --permission-mode acceptEdits, Codex
                --sandbox workspace-write) writes that file in its working folder (P11 coder)
Login state is a file "logged_in" inside the account's own folder (CLAUDE_CONFIG_DIR /
CODEX_HOME), which is exactly what proves the folders keep accounts apart.
"""
import json
import os
import sys
import time
from pathlib import Path

# Always in the future, so the app never treats these windows as already reset.
RESET_5H = int(time.time() // 3600 * 3600 + 3 * 3600)
RESET_7D = int(time.time() // 3600 * 3600 + 4 * 86400)


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def bump() -> int:
    state = os.environ.get("FAKE_STATE")
    if not state:
        return 1
    p = Path(state)
    n = int(p.read_text()) + 1 if p.exists() else 1
    p.write_text(str(n))
    return n


def write_file():
    """FAKE_WRITE="path=text": what a coding run leaves in its working folder (P11)."""
    spec = os.environ.get("FAKE_WRITE")
    if spec:
        path, _, text = spec.partition("=")
        p = Path.cwd() / path
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def last_user_line(prompt: str) -> str:
    return prompt.strip().rsplit("User:", 1)[-1].split("Assistant:", 1)[0].strip()


def claude(args, home: Path):
    flag = home / "logged_in"
    # CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) signs in without the login file. Real
    # 2.1.284, 2026-10-11: auth status says loggedIn / "oauth_token" with no email (it doesn't
    # check the token), and /usage prints only the session cost summary, no limit windows.
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if args[:2] == ["auth", "status"]:
        if token:
            out({"loggedIn": True, "authMethod": "oauth_token", "apiProvider": "firstParty",
                 "configDirectory": str(home)})
        elif flag.exists():
            out({"loggedIn": True, "authMethod": "claude.ai", "email": flag.read_text(),
                 "subscriptionType": "pro", "configDirectory": str(home)})
        else:
            out({"loggedIn": False, "authMethod": "none", "configDirectory": str(home)})
        return 0
    if args[:2] == ["auth", "login"]:
        flag.write_text(os.environ.get("FAKE_EMAIL", "friend@example.com"))
        return 0
    if "-p" not in args:
        return 2
    if "/usage" in args:                      # answered locally by the real CLI: no turn, no cost
        if token:
            out({"type": "result", "subtype": "success", "is_error": False, "num_turns": 0,
                 "total_cost_usd": 0, "result": "Total cost:            $0.0000\nUsage:                 0 input, 0 output"})
            return 0
        if not flag.exists():
            sys.stderr.write("Not logged in · Please run /login\n")
            return 1
        spends = os.environ.get("FAKE_MODE") == "usage_spends"
        pct = round(float(os.environ.get("FAKE_UTIL", "0.34")) * 100)
        import datetime as _dt
        def _when(**kw):                      # same text format as the real CLI, always in the future
            d = _dt.datetime.now() + _dt.timedelta(**kw)
            return f"{d:%b} {d.day}, {d.hour % 12 or 12}:{d:%M}{'am' if d.hour < 12 else 'pm'}"
        out({"type": "result", "subtype": "success", "is_error": False,
             "num_turns": 1 if spends else 0, "total_cost_usd": 0.01 if spends else 0,
             "result": "You are currently using your subscription to power your Claude Code usage\n\n"
                       f"Current session: {pct}% used · resets {_when(hours=2)} (Asia/Kolkata)\n"
                       f"Current week (all models): 18% used · resets {_when(days=3)} (Asia/Kolkata)\n"})
        return 0
    prompt = sys.stdin.read()
    if log := os.environ.get("FAKE_LOG"):
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"args": args, "prompt": prompt,
                                 "env": {k: v for k, v in os.environ.items() if k.startswith("CLAUDE_CODE_")}}) + "\n")
    if not flag.exists() and not token:
        sys.stderr.write("Not logged in · Please run /login\n")
        return 1
    n = bump()
    mode = os.environ.get("FAKE_MODE_CLAUDE") or os.environ.get("FAKE_MODE", "ok")
    if "acceptEdits" in args:
        write_file()
    out({"type": "system", "subtype": "init", "model": "claude-sonnet-5-5"})
    rl = {"type": "rate_limit_event", "rate_limit_info": {
        "status": "rejected" if mode == "limit" else "allowed", "rateLimitType": "five_hour",
        "unifiedWindows": {"five_hour": {"utilization": 1.0 if mode == "limit" else float(os.environ.get("FAKE_UTIL", "0.42")),
                                         "resetsAt": RESET_5H},
                           "seven_day": {"utilization": 0.10, "resetsAt": RESET_7D}}}}
    usage = {"input_tokens": 12, "output_tokens": 30, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}

    def result(err=False, status=None, text=""):
        out({"type": "result", "subtype": "error" if err else "success", "is_error": err,
             "api_error_status": status, "result": text, "usage": usage, "total_cost_usd": 0.0012,
             "modelUsage": {"claude-sonnet-5-5": {}}})

    if mode == "slow":
        time.sleep(60)
        return 0
    if mode == "limit":
        out(rl)
        result(True, 429, "Claude AI usage limit reached")
        return 1
    if mode == "transient" and n <= int(os.environ.get("FAKE_FAILS", "99")):
        result(True, 529, "Overloaded")
        return 1
    if mode == "auth":
        result(True, 401, "Invalid authentication credentials")
        return 1
    words = (os.environ.get("FAKE_REPLY") or f"Fake Claude says: {last_user_line(prompt)}").split(" ")
    if mode == "midstream":
        for w in words[:3]:
            out({"type": "stream_event", "event": {"type": "content_block_delta", "index": 1,
                                                   "delta": {"type": "text_delta", "text": w + " "}}})
        result(True, 500, "Internal server error")
        return 1
    out({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                           "delta": {"type": "thinking_delta", "thinking": "hmm"}}})
    for w in words:
        out({"type": "stream_event", "event": {"type": "content_block_delta", "index": 1,
                                               "delta": {"type": "text_delta", "text": w + " "}}})
        time.sleep(0.02)
    out(rl)
    result(text=" ".join(words))
    return 0


def codex(args, home: Path):
    flag = home / "logged_in"
    if args[:2] == ["login", "status"]:
        if flag.exists():
            print("Logged in using ChatGPT")
            return 0
        print("Not logged in")
        return 1
    if args[:1] == ["login"]:
        flag.write_text(os.environ.get("FAKE_EMAIL", "friend@example.com"))
        return 0
    if args[:1] != ["exec"]:
        return 2
    prompt = sys.stdin.read()
    if log := os.environ.get("FAKE_LOG"):
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"args": args, "prompt": prompt,
                                 "env": {k: v for k, v in os.environ.items() if k.startswith("CLAUDE_CODE_")}}) + "\n")
    if not flag.exists():
        sys.stderr.write("Not logged in. Run `codex login`.\n")
        return 1
    n = bump()
    mode = os.environ.get("FAKE_MODE_CODEX") or os.environ.get("FAKE_MODE", "ok")
    if "workspace-write" in args:
        write_file()
    out({"type": "thread.started", "thread_id": "fake"})
    out({"type": "turn.started"})
    if mode == "limit":
        out({"type": "error", "message": "You've hit your usage limit. Try again later."})
        return 1
    if mode == "transient" and n <= int(os.environ.get("FAKE_FAILS", "99")):
        out({"type": "error", "message": "stream disconnected: connection reset (503)"})
        return 1
    # Like the real CLI (when not --ephemeral): the session file records exact limits.
    if "--ephemeral" not in args:
        sess = home / "sessions" / "2026" / "10" / "03"
        sess.mkdir(parents=True, exist_ok=True)
        pct = float(os.environ.get("FAKE_CODEX_PCT", "35"))
        (sess / f"rollout-2026-10-03T10-00-{n:02d}-fake.jsonl").write_text(
            json.dumps({"type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
                "limit_id": "codex", "primary": {"used_percent": pct, "window_minutes": 300, "resets_at": 4102444800},
                "secondary": {"used_percent": 10.0, "window_minutes": 10080, "resets_at": 4102444800}}}}) + "\n",
            encoding="utf-8")
    out({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message",
                                            "text": os.environ.get("FAKE_CODEX_REPLY")
                                            or f"Fake Codex says: {last_user_line(prompt)}"}})
    out({"type": "turn.completed", "usage": {"input_tokens": 50, "cached_input_tokens": 10, "output_tokens": 8}})
    return 0


if __name__ == "__main__":
    tool, rest = sys.argv[1], sys.argv[2:]
    if os.environ.get("FAKE_ARGS"):                  # tests read back the exact command line
        # Appends from two fake processes at once (a chat and a sign-in status check) can
        # interleave on Windows, so hold an exclusive lock on the first byte while writing.
        import msvcrt
        with open(os.environ["FAKE_ARGS"], "a+b") as f:
            for _ in range(500):
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.01)
            f.seek(0, 2)
            f.write((json.dumps(rest) + "\n").encode("utf-8"))
            f.flush()
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    if tool == "claude":
        sys.exit(claude(rest, Path(os.environ["CLAUDE_CONFIG_DIR"])))
    sys.exit(codex(rest, Path(os.environ["CODEX_HOME"])))
