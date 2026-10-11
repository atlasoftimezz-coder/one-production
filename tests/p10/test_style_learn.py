"""Learning a competitor's editing style: watch (real measuring of a synthetic video) -> one Claude turn
writes the profile -> the app adds the measured numbers itself and lists the profile; owner approval
turns it into guidance. Stand-in accounts and agent only."""
import asyncio
import json
import shutil

import pytest

from app import style_probe, styles as styles_mod
from app.agent.runs import Run
from app.agent.style_learn import StyleRun
from app.agent.team import TeamConfig
from app.competitors import FetchError

from tests.p9.test_team import Deps, acc, ok
from tests.p10.media_fixtures import FF, cut_video, needs_ffmpeg

PROFILE = {"summary": "Fast kinetic-type explainer with a click on most cuts.",
           "pacing": {"hook": "a cut every second", "body": "2 s shots"},
           "visual_language": [{"kind": "kinetic type", "share": 60, "how": "words slam in", "frames": ["frames/frame-001.jpg"]}],
           "typography": {"case": "upper"}, "color": {"approach": "dark bg, one accent"},
           "motion": {"eases": "fast out, slow in"}, "sound": {"sfx": "clicks on cuts"},
           "do": ["cut on every new claim"], "dont": ["no slow fades"],
           "measured": {"pace": {"cuts_per_min": 999}}}          # an invented number: the app must replace it


class Store:
    def __init__(self, root):
        self.root = root

    def files_dir(self, pid):
        return self.root


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    if FF is None:
        pytest.skip("ffmpeg is not installed")
    return cut_video(tmp_path_factory.mktemp("learn") / "c.mp4", [2.0, 1.5, 3.0, 1.0, 2.5])


def make(tmp_path, clip, worker, items=None, accounts=None):
    files = tmp_path / "files"
    files.mkdir(exist_ok=True)
    deps = Deps(tmp_path, accounts or [acc("c1", "claude", 0)], [], worker=worker)
    deps.files_dir = lambda: files
    deps.cache_dir = lambda: tmp_path / "cache"

    async def download(vid, work):
        if vid == "GONEGONE001":
            raise FetchError("Video unavailable")
        work.mkdir(parents=True, exist_ok=True)
        dst = work / f"{vid}.mp4"
        shutil.copyfile(clip, dst)
        return {"video": dst, "meta": {"id": vid, "title": f"Video {vid}", "channel": "Rival"},
                "text": "Why did it vanish? Nobody knows. " * 30}

    async def uploads(item, work):
        return [{"id": "CHANVID0001", "views": 9}, {"id": "CHANVID0002", "views": 99}, {"id": "CHANVID0003", "views": 1}]

    deps.download, deps.uploads = download, uploads
    deps.analyse = lambda video, out, text, meta: style_probe.analyse(FF, video, out, text, meta)
    deps.combine = style_probe.combine
    deps.styles = styles_mod.Styles(tmp_path / "data", Store(files))
    items = items or [{"kind": "video", "key": "FACTSVID001", "url": "u"}]
    run = Run("p1", files)
    return StyleRun("p1", "Rival fast", items, run, deps, TeamConfig(poll=0.05), state_file=tmp_path / "style.json",
                    per_channel=2), deps, files


async def writes_profile(d, a, sub, prompt, profile=PROFILE):
    target = d.files_dir() / "style" / "rival-fast" / "profile.json"
    assert sub.lock_check("Write", {"file_path": str(target)}) is None
    assert "LOOK at every contact sheet" in prompt and "sheet-01.jpg" in prompt
    target.write_text(json.dumps(profile), encoding="utf-8")
    return ok("Fast kinetic type.", 1000, 300)


@needs_ffmpeg
def test_watch_then_learn_stores_a_profile_with_measured_numbers(tmp_path, clip):
    sr, deps, files = make(tmp_path, clip, writes_profile)
    snap = asyncio.run(sr.go())
    assert snap["state"] == "done", snap
    base = files / "style" / "rival-fast"
    assert (base / "FACTSVID001" / "measure.json").is_file() and list((base / "FACTSVID001" / "sheets").glob("*.jpg"))
    prof = json.loads((base / "profile.json").read_text(encoding="utf-8"))
    assert prof["measured"]["pace"]["cuts_per_min"] != 999          # numbers come from code
    assert prof["measured"]["pace"]["cuts_per_min"] == pytest.approx(4 / 10 * 60, abs=1)
    assert prof["videos"][0]["id"] == "FACTSVID001"
    listed = deps.styles.list()
    assert listed[0]["name"] == "Rival fast" and listed[0]["available"]
    assert not (tmp_path / "cache" / "FACTSVID001").exists()        # the working copy is deleted
    assert [c[0] for c in deps.calls] == ["worker"]                  # one AI turn, no master


@needs_ffmpeg
def test_channel_links_take_the_most_viewed_and_bad_videos_are_reported(tmp_path, clip):
    items = [{"kind": "channel", "key": "@rival", "url": "https://www.youtube.com/@rival"},
             {"kind": "video", "key": "GONEGONE001", "url": "x"}]
    sr, deps, files = make(tmp_path, clip, writes_profile, items=items)
    snap = asyncio.run(sr.go())
    assert snap["state"] == "done"
    assert [v["id"] for v in snap["videos"]] == ["CHANVID0002", "CHANVID0001"]
    assert snap["problems"] and "unavailable" in snap["problems"][0]["why"]


@needs_ffmpeg
def test_a_bad_profile_goes_back_to_the_ai_then_stops(tmp_path, clip):
    async def lazy(d, a, sub, prompt):
        target = d.files_dir() / "style" / "rival-fast" / "profile.json"
        target.write_text(json.dumps({"summary": "x"}), encoding="utf-8")
        return ok("done", 10, 10)
    sr, deps, files = make(tmp_path, clip, lazy)
    snap = asyncio.run(sr.go())
    assert snap["state"] == "needs_you" and "missing" in snap["result"]["why"]
    assert len(deps.calls) == 3 and "Fix this first" in deps.calls[1][4]
    assert deps.styles.list() == []


@needs_ffmpeg
def test_learning_needs_a_claude_account(tmp_path, clip):
    sr, deps, files = make(tmp_path, clip, writes_profile, accounts=[acc("x1", "codex", 0)])

    async def scenario():
        t = asyncio.create_task(sr.go())
        for _ in range(200):
            if sr.state == "paused":
                break
            await asyncio.sleep(0.02)
        assert sr.state == "paused" and "claude" in sr.paused_why
        sr.run.stopped = True
        sr.resume()
        return await t
    assert asyncio.run(scenario())["state"] == "stopped"


def test_check_profile_and_brief():
    with pytest.raises(styles_mod.StyleError, match="missing"):
        styles_mod.check_profile({"summary": "x"})
    with pytest.raises(styles_mod.StyleError, match="list"):
        styles_mod.check_profile({**PROFILE, "do": "cut a lot"})
    p = {**PROFILE, "measured": {"pace": {"cuts_per_min": 0.2, "beats_per_min": 11, "beat_median": 3.4, "continuous_share": 1.0},
                                 "sound": {"cuts_with_a_hit": 0.8}, "words": {"wpm": 170}}}
    b = styles_mod.brief(p)
    assert "11 visible changes/min (median 3.4 s apart" in b and "0.2 hard cuts/min" in b and "continuous" in b
    assert "80% of cuts" in b and "Do: cut on every new claim" in b
