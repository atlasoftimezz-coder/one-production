"""Draft episodes: a kit script for one of THIS episode's shots runs without a card (owner, 2026-10-11),
completed with the kit, the episode's own motion.py and a guard that its Resolve project is open.
Anything else still goes to the card; full-PC scripts stay blocked; nothing is automatic outside drafts."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import fusion_kit
from app.agent import policy
from app.agent.claude_agent import ClaudeAgent
from app.fusion import ShotScripts

PROJECT = "Ep 1 - One Production 20261011-120000"
MOTION = "def slam_in(S, text, y=0.5):\n    t = S.text(text, 0.5, y, 0.08, S.c('ink'))\n    return S.pop(t, 0.5, y, 0)\n"
SHOT = 'from op_kit import *\nfrom ep_motion import *\nS = begin("shot-002")\nresult = finish(S, slam_in(S, "HELIUM"))\n'


def scripts(tmp_path, auto=True, motion=MOTION):
    (tmp_path / "motion.py").write_text(motion, encoding="utf-8")
    return ShotScripts(PROJECT, {"shot-001", "shot-002"}, tmp_path / "motion.py", tmp_path / "scripts", auto=auto)


def test_own_shot_runs_with_kit_motion_and_project_guard(tmp_path):
    out = scripts(tmp_path).auto(SHOT)
    assert out is not None and "class Shot" in out and "def slam_in" in out
    assert out.index("class Shot") < out.index("def slam_in") < out.index('begin("shot-002")')     # kit, then motion, then the shot
    assert f"if project.GetName() != {PROJECT!r}:" in out and "from op_kit" not in out and "from ep_motion" not in out
    assert (tmp_path / "scripts" / "shot-002.py").read_text(encoding="utf-8") == SHOT              # the AI's own code is kept


@pytest.mark.parametrize("script", [
    'from op_kit import *\nS = begin("shot-009")\n',                       # not this episode's shot
    'from op_kit import *\nS = begin("shot-001")\nT = begin("shot-777")\n',  # one foreign shot is enough
    'S = begin("shot-001")\n',                                              # no kit: not a shot script
    'from op_kit import *\nresolve.GetProjectManager().DeleteProject("x")\n',  # kit but no shot at all
])
def test_anything_else_goes_to_the_card(tmp_path, script):
    assert scripts(tmp_path).auto(script) is None


def test_nothing_is_automatic_outside_a_draft_but_approved_scripts_still_get_motion(tmp_path):
    s = scripts(tmp_path, auto=False)
    assert s.auto(SHOT) is None
    out, used = s.expand(SHOT)
    assert used and "def slam_in" in out and "if project.GetName()" in out


def test_a_broken_motion_library_is_not_run_automatically(tmp_path):
    assert scripts(tmp_path, motion="import os\nos.remove('x')\n").auto(SHOT) is None
    assert scripts(tmp_path, motion="def x(:\n").auto(SHOT) is None
    for bad, why in (("import subprocess", "not allowed"), ("open('a')", "not allowed"), ("from op_kit import *", "must not import"),
                     ("", "empty"), ("x = 1\n" * 20000, "kB")):
        with pytest.raises(ValueError, match=why):
            fusion_kit.check_motion(bad)
    assert fusion_kit.check_motion(MOTION) == MOTION


def test_full_pc_scripts_stay_denied():
    assert policy.decide("mcp__davinci__run_script_unsafe", {"script": SHOT}, Path("."))[0] == "deny"


def can_use(tmp_path, run):
    """The agent's own permission callback, as the Agent SDK calls it."""
    sdk = pytest.importorskip("claude_agent_sdk")
    permits = []

    async def permit(r, tool, inp, gate=None):
        permits.append(tool)
        return False, "the owner said no"
    agent = ClaudeAgent(None, tmp_path)
    opts = agent.options(run, SimpleNamespace(permit=permit), {"id": "a", "provider": "claude", "profile_dir": str(tmp_path)},
                         SimpleNamespace(tools=lambda: []), None, "", None, None)
    return opts.can_use_tool, permits, sdk


def test_the_agent_runs_own_shots_without_asking_and_asks_for_the_rest(tmp_path):
    run = SimpleNamespace(root=tmp_path, stopped=False, lock_check=None, shot_scripts=scripts(tmp_path))
    cb, permits, sdk = can_use(tmp_path, run)
    res = asyncio.run(cb("mcp__davinci__run_script", {"script": SHOT}, None))
    assert isinstance(res, sdk.PermissionResultAllow) and "def slam_in" in res.updated_input["script"] and not permits
    res = asyncio.run(cb("mcp__davinci__run_script", {"script": 'from op_kit import *\nS = begin("shot-999")\n'}, None))
    assert isinstance(res, sdk.PermissionResultDeny) and permits == ["mcp__davinci__run_script"]     # it went to the card
    res = asyncio.run(cb("mcp__davinci__generate_lut", {"name": "x"}, None))
    assert isinstance(res, sdk.PermissionResultDeny) and permits[-1] == "mcp__davinci__generate_lut"
    plain = SimpleNamespace(root=tmp_path, stopped=False, lock_check=None)                            # a normal chat run
    cb2, permits2, _ = can_use(tmp_path, plain)
    asyncio.run(cb2("mcp__davinci__run_script", {"script": SHOT}, None))
    assert permits2 == ["mcp__davinci__run_script"]
