"""P11 gate: dispatch.py picks the coder by rules and headroom, and the OTHER model reviews.

AUTO (plan): claude floor mocked low -> codex picked; Codex review file appears for a
Claude-written change. Everything runs on the stand-in CLIs; no real account is touched."""
import asyncio
import os
import sqlite3
import time

import pytest

from tests.p11.conftest import account, argvs, codex_used, dispatch, git, prompts

REVIEW = "## Findings\nNo problems found.\n## Verdict\nmerge - small and tested."


def make(settings, repo, **cfg):
    config = dispatch.load_config()
    for k, v in cfg.items():
        if isinstance(v, dict):
            config[k].update(v)
        else:
            config[k] = v
    lines = []
    d = dispatch.Dispatcher(settings, repo=repo, config=config, say=lines.append)
    d.lines = lines
    return d


def pick(d, text="fix the usage page"):
    order, _ = d.order_for(text)
    return asyncio.run(d.pick(order, d.config["floor_pct"]))


# ---------------------------------------------------------------- choosing
def test_claude_below_floor_picks_codex(env, repo, monkeypatch):
    account(env, "claude")
    account(env, "codex")
    monkeypatch.setenv("FAKE_UTIL", "0.90")           # Claude's /usage: 90 % used, 10 % left < 20 %
    choice, report = pick(make(env, repo))
    assert choice.provider == "codex"
    assert any("below the 20% floor" in r and r.startswith("Claude") for r in report), report
    assert any("resets" in r for r in report)


def test_claude_with_headroom_is_picked(env, repo, monkeypatch):
    account(env, "claude")
    account(env, "codex")
    monkeypatch.setenv("FAKE_UTIL", "0.30")
    choice, report = pick(make(env, repo))
    assert choice.provider == "claude" and choice.kind == "exact" and choice.pct == 30


def test_rules_choose_the_order(env, repo):
    d = make(env, repo)
    assert d.order_for("please add tests for the editor")[0] == ["codex", "claude"]
    assert d.order_for("anything", kind="tests") == (["codex", "claude"], "tests")
    assert d.order_for("make the usage page faster")[0] == ["claude", "codex"]
    with pytest.raises(dispatch.DispatchError):
        d.order_for("x", kind="nope")


def test_turned_off_and_signed_out_accounts_are_skipped(env, repo, monkeypatch):
    monkeypatch.setenv("FAKE_UTIL", "0.10")
    off = account(env, "claude", label="off")
    from app.accounts import Accounts
    Accounts(env.data_dir).set_enabled(off["id"], False)
    account(env, "codex")
    choice, report = pick(make(env, repo))
    assert choice.provider == "codex"
    assert any("turned off" in r for r in report)


def test_signed_out_claude_falls_to_codex(env, repo):
    account(env, "claude", signed_in=False)
    account(env, "codex")
    choice, report = pick(make(env, repo))
    assert choice.provider == "codex"
    assert any("not logged in" in r for r in report), report


def test_second_claude_login_is_not_used_when_the_first_runs_low(env, repo, monkeypatch):
    """D7: hopping between two logins of the same service stays off until the terms check."""
    account(env, "claude", label="first")
    account(env, "claude", label="second")
    codex = account(env, "codex")
    codex_used(codex, 95)
    monkeypatch.setenv("FAKE_UTIL", "0.90")
    choice, report = pick(make(env, repo))
    assert choice is None
    assert any("second" in r and "terms don't allow it" in r for r in report), report


def test_unknown_usage_does_not_block(env, repo):
    account(env, "codex")                              # no session file yet: usage unknown
    choice, report = pick(make(env, repo), "add tests for x")
    assert choice.provider == "codex" and choice.pct is None
    assert any("usage unknown" in r for r in report)


def test_cli_pick_exits_2_when_every_account_is_low(env, repo, monkeypatch, capsys):
    account(env, "claude")
    codex_used(account(env, "codex"), 99)
    monkeypatch.setenv("FAKE_UTIL", "0.95")
    monkeypatch.setenv("ONEPROD_SETTINGS_FILE", str(env.data_dir / "none.json"))
    monkeypatch.setenv("ONEPROD_DATA_DIR", str(env.data_dir))
    monkeypatch.setenv("ONEPROD_CLAUDE_CMD", env.claude_cmd)
    monkeypatch.setenv("ONEPROD_CODEX_CMD", env.codex_cmd)
    assert dispatch.main(["pick", "fix something"]) == dispatch.EXIT_NO_ACCOUNT
    out = capsys.readouterr().out
    assert "Claude" in out and "Codex" in out and "below the 20% floor" in out


# ---------------------------------------------------------------- run + cross review
def test_claude_writes_codex_reviews(env, repo, tmp_path, monkeypatch):
    account(env, "claude")
    account(env, "codex")
    monkeypatch.setenv("FAKE_UTIL", "0.30")
    monkeypatch.setenv("FAKE_WRITE", "hello.txt=hi from the coder")
    monkeypatch.setenv("FAKE_REPLY", "Added hello.txt; ran no tests.")
    monkeypatch.setenv("FAKE_CODEX_REPLY", REVIEW)
    d = make(env, repo)
    assert asyncio.run(d.run("t1", "Add a hello file")) == dispatch.EXIT_OK, d.lines

    # the change lives on its own branch / worktree; main and the main folder are untouched
    assert not (repo / "hello.txt").exists()
    assert git(repo, "log", "--format=%s", "main") == "start\n"
    body = git(repo, "log", "-1", "--format=%B", "dispatch/t1")
    assert "Co-Authored-By: Claude" in body and "dispatch t1: Add a hello file" in body
    wt = repo / "build" / "dispatch" / "t1"
    assert (wt / "hello.txt").read_text(encoding="utf-8") == "hi from the coder\n"   # reviewer wrote nothing

    review = (repo / "dev" / "reviews" / "t1.md").read_text(encoding="utf-8")
    assert "| Written by | Claude |" in review
    assert "| Reviewed by | Codex (" in review
    assert "No problems found." in review and "Added hello.txt" in review and "Add a hello file" in review

    coder_prompt, review_prompt = [p["prompt"] for p in prompts(tmp_path)]
    assert "Add a hello file" in coder_prompt and "Do not commit" in coder_prompt
    assert "+hi from the coder" in review_prompt and "written by Claude" in review_prompt
    runs = [a for a in argvs(tmp_path) if "-p" in a and "/usage" not in a or a[:1] == ["exec"]]
    claude_run = next(a for a in runs if "-p" in a)
    codex_run = next(a for a in runs if a[:1] == ["exec"])
    assert "acceptEdits" in claude_run and "Bash(python -m pytest:*)" in claude_run
    assert codex_run[codex_run.index("--sandbox") + 1] == "read-only"

    with sqlite3.connect(env.data_dir / "usage.sqlite") as c:
        roles = {r[0] for r in c.execute("SELECT role FROM calls")}
    assert roles == {"dispatch-coder", "dispatch-reviewer"}


def test_codex_writes_claude_reviews(env, repo, tmp_path, monkeypatch):
    account(env, "claude")
    account(env, "codex")
    monkeypatch.setenv("FAKE_UTIL", "0.85")            # too low to code (15 % < 20), enough to review (>= 10)
    monkeypatch.setenv("FAKE_WRITE", "lib/util.py=VALUE = 1")
    monkeypatch.setenv("FAKE_REPLY", REVIEW)
    d = make(env, repo)
    assert asyncio.run(d.run("t2", "Add a util module")) == dispatch.EXIT_OK, d.lines
    assert "Co-Authored-By: Codex" in git(repo, "log", "-1", "--format=%B", "dispatch/t2")
    review = (repo / "dev" / "reviews" / "t2.md").read_text(encoding="utf-8")
    assert "| Written by | Codex |" in review and "| Reviewed by | Claude (" in review
    claude_review = next(a for a in argvs(tmp_path) if "-p" in a and "/usage" not in a)
    assert claude_review[claude_review.index("--tools") + 1] == "Read,Glob,Grep"
    assert "acceptEdits" not in claude_review
    codex_code = next(a for a in argvs(tmp_path) if a[:1] == ["exec"])
    assert codex_code[codex_code.index("--sandbox") + 1] == "workspace-write"


def test_limit_mid_task_hands_over_to_the_other_model(env, repo, tmp_path, monkeypatch):
    account(env, "claude")
    account(env, "codex")
    monkeypatch.setenv("FAKE_UTIL", "0.30")
    monkeypatch.setenv("FAKE_MODE_CLAUDE", "limit")
    monkeypatch.setenv("FAKE_WRITE", "notes.txt=step")
    d = make(env, repo)
    code = asyncio.run(d.run("t3", "Write the notes"))
    log = git(repo, "log", "--format=%B%x00", "main..dispatch/t3").split("\x00")
    assert any("partial - stopped at its limit" in b and "Claude" in b for b in log)
    assert any("Co-Authored-By: Codex" in b for b in log)
    codex_prompt = [p["prompt"] for p in prompts(tmp_path)][1]
    assert "stopped at its usage limit" in codex_prompt
    # Claude wrote first, Codex finished: Claude would review, but it is at its limit -> noted, exit 3
    assert code == dispatch.EXIT_NOT_REVIEWED
    review = (repo / "dev" / "reviews" / "t3.md").read_text(encoding="utf-8")
    assert "Claude then Codex" in review and "**Not reviewed.**" in review


def test_existing_task_id_is_refused(env, repo, monkeypatch):
    account(env, "codex")
    monkeypatch.setenv("FAKE_WRITE", "a.txt=1")
    monkeypatch.setenv("FAKE_CODEX_REPLY", "done")
    d = make(env, repo)
    asyncio.run(d.run("dup", "first", provider="codex", review=False))
    with pytest.raises(dispatch.DispatchError, match="already exists"):
        asyncio.run(d.run("dup", "second", provider="codex", review=False))
    with pytest.raises(dispatch.DispatchError, match="task id"):
        asyncio.run(d.run("Bad Id!", "x"))


# ---------------------------------------------------------------- review of an existing change
def commit(repo, name, text, trailer):
    (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"Change {name}\n\n{trailer}")


def test_review_of_a_claude_commit_goes_to_codex(env, repo, tmp_path, monkeypatch):
    account(env, "codex")
    monkeypatch.setenv("FAKE_CODEX_REPLY", REVIEW)
    commit(repo, "x.py", "X = 2\n", "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
    sha = git(repo, "rev-parse", "HEAD").strip()
    d = make(env, repo)
    assert asyncio.run(d.review("HEAD")) == dispatch.EXIT_OK, d.lines
    path = repo / "dev" / "reviews" / f"review-{sha[:8]}.md"
    assert "| Reviewed by | Codex (" in path.read_text(encoding="utf-8")
    assert "+X = 2" in prompts(tmp_path)[-1]["prompt"]
    # a second review never overwrites the first
    assert asyncio.run(d.review("HEAD")) == dispatch.EXIT_OK
    assert (repo / "dev" / "reviews" / f"review-{sha[:8]}-2.md").exists()


def test_unknown_author_needs_the_flag(env, repo):
    commit(repo, "y.py", "Y = 1\n", "no trailer here")
    with pytest.raises(dispatch.DispatchError, match="--author"):
        asyncio.run(make(env, repo).review("HEAD"))


def test_reviewer_unavailable_is_written_down(env, repo):
    account(env, "codex", signed_in=False)
    commit(repo, "z.py", "Z = 1\n", "Co-Authored-By: Claude <noreply@anthropic.com>")
    d = make(env, repo)
    assert asyncio.run(d.review("HEAD", task="zz")) == dispatch.EXIT_NOT_REVIEWED
    text = (repo / "dev" / "reviews" / "zz.md").read_text(encoding="utf-8")
    assert "**Not reviewed.**" in text and "Codex not available" in text


def test_large_diff_is_cut_and_says_so(env, repo, tmp_path, monkeypatch):
    account(env, "codex")
    monkeypatch.setenv("FAKE_CODEX_REPLY", REVIEW)
    commit(repo, "big.txt", "line\n" * 2000, "Co-Authored-By: Claude <noreply@anthropic.com>")
    d = make(env, repo, review={"max_diff_chars": 500})
    assert asyncio.run(d.review("HEAD", task="big")) == dispatch.EXIT_OK
    assert "diff cut: 500 of" in prompts(tmp_path)[-1]["prompt"]
    assert "diff cut for the review" in (repo / "dev" / "reviews" / "big.md").read_text(encoding="utf-8")


def test_status_lists_branches_and_reviews(env, repo, monkeypatch):
    account(env, "codex")
    monkeypatch.setenv("FAKE_WRITE", "s.txt=1")
    monkeypatch.setenv("FAKE_CODEX_REPLY", "done")
    d = make(env, repo)
    asyncio.run(d.run("st", "status check", provider="codex", review=False))
    d.status()
    assert any("dispatch/st: 1 commit(s) ahead of main" in line for line in d.lines), d.lines


# ---------------------------------------------------------------- shared pieces
def test_usage_pct_shared_with_the_app():
    from app.usage import usage_pct
    now = time.time()
    acc = {"id": "a", "rate_limit": {"at": now, "windows": {
        "five_hour": {"utilization": 0.9, "resets_at": now - 10},       # already reset -> 0
        "seven_day": {"utilization": 0.4, "resets_at": now + 999}}}}
    assert usage_pct(acc, now=now) == (40.0, "exact")
    assert usage_pct({"id": "b"}) == (None, "none")


def test_dev_folder_is_not_shipped():
    import tools.build_package as bp
    assert "dev" not in bp.SHIP
