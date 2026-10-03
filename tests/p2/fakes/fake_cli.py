"""Stand-in for the real `claude` and `codex` CLIs, reproducing the output formats captured
from Claude Code 2.1.284 and codex-cli 0.157.1 on 2026-10-03.

    fake_cli.py claude <args...>      fake_cli.py codex <args...>

Behaviour comes from environment variables set by the tests:
    FAKE_MODE   ok | limit | auth | transient | midstream | slow
    FAKE_FAILS  for "transient": how many calls fail before one succeeds (default: always)
    FAKE_STATE  file counting calls (so tests can prove a prompt was or wasn't re-sent)
    FAKE_LOG    file the received stdin prompt is appended to
    FAKE_EMAIL  email to report after a fake login
    FAKE_UTIL   Claude five_hour utilisation to report (default 0.42)
Login state is a file "logged_in" inside the account's own folder (CLAUDE_CONFIG_DIR /
CODEX_HOME), which is exactly what proves the folders keep accounts apart.
"""
import json
import os
import sys
import time
from pathlib import Path


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


def last_user_line(prompt: str) -> str:
    return prompt.strip().rsplit("User:", 1)[-1].split("Assistant:", 1)[0].strip()


def claude(args, home: Path):
    flag = home / "logged_in"
    if args[:2] == ["auth", "status"]:
        if flag.exists():
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
    prompt = sys.stdin.read()
    if log := os.environ.get("FAKE_LOG"):
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"args": args, "prompt": prompt}) + "\n")
    if not flag.exists():
        sys.stderr.write("Not logged in · Please run /login\n")
        return 1
    n = bump()
    mode = os.environ.get("FAKE_MODE", "ok")
    out({"type": "system", "subtype": "init", "model": "claude-sonnet-5-5"})
    rl = {"type": "rate_limit_event", "rate_limit_info": {
        "status": "rejected" if mode == "limit" else "allowed", "rateLimitType": "five_hour",
        "unifiedWindows": {"five_hour": {"utilization": 1.0 if mode == "limit" else float(os.environ.get("FAKE_UTIL", "0.42")),
                                         "resetsAt": 1791033600},
                           "seven_day": {"utilization": 0.10, "resetsAt": 1791208800}}}}
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
    words = f"Fake Claude says: {last_user_line(prompt)}".split(" ")
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
            fh.write(json.dumps({"args": args, "prompt": prompt}) + "\n")
    if not flag.exists():
        sys.stderr.write("Not logged in. Run `codex login`.\n")
        return 1
    n = bump()
    mode = os.environ.get("FAKE_MODE", "ok")
    out({"type": "thread.started", "thread_id": "fake"})
    out({"type": "turn.started"})
    if mode == "limit":
        out({"type": "error", "message": "You've hit your usage limit. Try again later."})
        return 1
    if mode == "transient" and n <= int(os.environ.get("FAKE_FAILS", "99")):
        out({"type": "error", "message": "stream disconnected: connection reset (503)"})
        return 1
    out({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message",
                                            "text": f"Fake Codex says: {last_user_line(prompt)}"}})
    out({"type": "turn.completed", "usage": {"input_tokens": 50, "cached_input_tokens": 10, "output_tokens": 8}})
    return 0


if __name__ == "__main__":
    tool, rest = sys.argv[1], sys.argv[2:]
    if tool == "claude":
        sys.exit(claude(rest, Path(os.environ["CLAUDE_CONFIG_DIR"])))
    sys.exit(codex(rest, Path(os.environ["CODEX_HOME"])))
