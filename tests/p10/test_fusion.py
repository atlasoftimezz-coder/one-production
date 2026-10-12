"""Fusion episodes (F3): the service's checks and shot timing, and the fixed runner's fusion_build /
fusion_stills against an in-process stand-in Resolve that behaves like Resolve Studio 21.1 did when
measured on 2026-10-06: InsertFusionCompositionIntoTimeline is a 300-frame RIPPLE insert at the
playhead (splitting an item it lands in), DeleteClips(ripple=False) leaves gaps, ExportCurrentFrameAsStill
writes the viewer frame, comps keep SetData values."""
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import resolve_runner as R
from app.fusion import Fusion, FusionError, check_art, check_shots, frames_of
from app.storage import Store
from tests.p10.media_fixtures import master, needs_ffmpeg, srt, video, voice

GEN = 300


class Comp:
    def __init__(self):
        self.data, self.tools = {}, 1                 # MediaOut only, like an empty Fusion composition

    def SetData(self, k, v): self.data[k] = v
    def GetData(self, k): return self.data.get(k)
    def GetToolList(self, selected=False): return {n: object() for n in range(self.tools)}


class Item:
    def __init__(self, start, dur, name="Fusion Composition", path=None):
        self.start, self.dur, self.name, self.path, self.comp = start, dur, name, path, Comp()

    def GetStart(self): return self.start
    def GetEnd(self): return self.start + self.dur
    def GetDuration(self): return self.dur
    def GetName(self): return self.name
    def SetName(self, n): self.name = n; return True
    def GetFusionCompByIndex(self, i): return self.comp if i == 1 else None
    def GetClipProperty(self, k): return "30" if k == "FPS" else None      # a 30 fps Pexels clip in the media pool
    def AddFusionComp(self): return self.comp


class Timeline:
    def __init__(self, name, fps):
        self.name, self.fps, self.video, self.audio, self.playhead = name, fps, [], {}, 0
        self.audio_tracks = 1                            # a new Resolve timeline has one audio track

    def GetName(self): return self.name
    def GetStartFrame(self): return 0
    def GetTrackCount(self, kind): return 1 if kind == "video" else self.audio_tracks

    def AddTrack(self, kind, sub=None):
        assert kind == "audio"
        self.audio_tracks += 1
        return True

    def GetItemListInTrack(self, kind, n):
        return sorted(self.video, key=lambda i: i.start) if kind == "video" else list(self.audio.get(n, []))

    def SetCurrentTimecode(self, tc):
        h, m, s, f = map(int, tc.split(":"))
        self.playhead = ((h * 60 + m) * 60 + s) * round(self.fps) + f
        return True

    def InsertFusionCompositionIntoTimeline(self):
        p = self.playhead
        for it in list(self.video):
            if it.start < p < it.GetEnd():            # split, and push the right part after the new item
                right = Item(p + GEN, it.GetEnd() - p)
                it.dur = p - it.start
                self.video.append(right)
            elif it.start >= p:
                it.start += GEN
        new = Item(p, GEN)
        self.video.append(new)
        return new

    def DeleteClips(self, items, ripple):
        assert ripple is False
        for it in items:
            self.video.remove(it)
        return True


class Project:
    def __init__(self, name):
        self.name, self.settings, self.timelines, self.current, self.stills = name, {}, [], None, []

    def GetName(self): return self.name
    def SetSetting(self, k, v): self.settings[k] = v; return True
    def GetSetting(self, k): return self.settings.get(k)
    def GetMediaPool(self): return Pool(self)
    def SetCurrentTimeline(self, t): self.current = t; return True
    def GetCurrentTimeline(self): return self.current
    def GetTimelineCount(self): return len(self.timelines)
    def GetTimelineByIndex(self, n): return self.timelines[n - 1]

    def ExportCurrentFrameAsStill(self, path):
        Path(path).write_bytes(b"png")
        self.stills.append((self.current.playhead, path))
        return True


class Pool:
    def __init__(self, project): self.project = project

    def ImportMedia(self, paths):
        return [Item(0, 0, Path(p).name, p) for p in paths if Path(p).is_file()]

    def CreateEmptyTimeline(self, name):
        t = Timeline(name, float(self.project.settings.get("timelineFrameRate", 24)))
        self.project.timelines.append(t)
        return t

    def AppendToTimeline(self, infos):
        out = []
        for info in infos:
            tl = self.project.current
            if info["mediaType"] == 1:                 # video: the source range conformed to the timeline's rate
                src = float(info["mediaPoolItem"].GetClipProperty("FPS"))
                # Resolve 21.1: endFrame is one past the last source frame (real check, 2026-10-12)
                dur = round((info["endFrame"] - info["startFrame"]) * tl.fps / src)
                it = Item(info["recordFrame"], dur, Path(info["mediaPoolItem"].path).name, info["mediaPoolItem"].path)
                it.source = (info["startFrame"], info["endFrame"])
                tl.video.append(it)
            else:
                assert info["mediaType"] == 2 and info["trackIndex"] <= tl.audio_tracks, "no such audio track"
                it = Item(info["recordFrame"], 1440, Path(info["mediaPoolItem"].path).name)
                tl.audio.setdefault(info["trackIndex"], []).append(it)
            out.append(it)
        return out


class Manager:
    def __init__(self):
        self.folders, self.folder, self.current, self.saved = {"": ["Owner project"]}, "", Project("Owner project"), 0

    def GetCurrentProject(self): return self.current
    def GotoRootFolder(self): self.folder = ""; return True
    def GetFolderListInCurrentFolder(self): return [f for f in self.folders if f]
    def CreateFolder(self, n): self.folders.setdefault(n, []); return True

    def OpenFolder(self, n):
        if n not in self.folders:
            return False
        self.folder = n
        return True

    def GetProjectListInCurrentFolder(self): return [p if isinstance(p, str) else p.name for p in self.folders[self.folder]]

    def CreateProject(self, n):
        p = Project(n)
        self.folders[self.folder].append(p)
        self.current = p
        return p

    def LoadProject(self, n):
        p = next((p for p in self.folders[self.folder] if not isinstance(p, str) and p.name == n), None)
        if p:
            self.current = p
        return p

    def SaveProject(self): self.saved += 1; return True


class Resolve:
    def __init__(self, version="21.1.0.17"):
        self.version, self.manager, self.page = version, Manager(), "media"

    def GetProjectManager(self): return self.manager
    def GetProductName(self): return "DaVinci Resolve Studio"
    def GetVersionString(self): return self.version
    def OpenPage(self, p): self.page = p; return True


def build_spec(tmp_path, **over):
    vo = tmp_path / "vo.wav"; vo.write_bytes(b"x")
    music = tmp_path / "bed.mp3"; music.write_bytes(b"x")
    spec = {"expected_version": "21.1.0.17", "project_name": "Ep 1 - One Production test", "fps": 60, "width": 1920, "height": 1080,
            "voiceover": str(vo), "music": str(music), "style": {"palette": {"bg": "#000000"}},
            "shots": [{"name": "shot-001", "start": 0, "frames": 90, "idea": "a"}, {"name": "shot-002", "start": 90, "frames": 127, "idea": "b"},
                      {"name": "shot-003", "start": 217, "frames": 113, "idea": "c", "anchor": "x y z"}, {"name": "shot-004", "start": 330, "frames": 70, "idea": "d"}]}
    spec.update(over)
    return spec


def test_timecode_counts_whole_frames():
    assert R.timecode(0, 60) == "00:00:00:00" and R.timecode(217, 60) == "00:00:03:37" and R.timecode(60 * 3661 + 5, 60) == "01:01:01:05"


def test_fusion_build_cuts_every_shot_exactly_and_stores_the_briefs(tmp_path):
    res = Resolve()
    out = R.fusion_build(res, build_spec(tmp_path))
    assert out["passed"], out
    assert out["shots"] == [{"name": "shot-001", "start": 0, "frames": 90}, {"name": "shot-002", "start": 90, "frames": 127},
                            {"name": "shot-003", "start": 217, "frames": 113}, {"name": "shot-004", "start": 330, "frames": 70}]
    pm = res.manager
    project = pm.current
    assert pm.folder == R.EPISODE_FOLDER and project.settings["timelineFrameRate"] == "60" and pm.saved == 1
    tl = project.current
    items = tl.GetItemListInTrack("video", 1)
    assert len(items) == 4 and items[-1].GetEnd() == 400               # the leftover pieces were deleted
    brief = items[2].comp.data["OneProduction.shot"]
    assert brief == {"name": "shot-003", "start": 217, "frames": 113, "idea": "c", "anchor": "x y z", "text": None, "fps": 60.0}
    assert items[0].comp.data["OneProduction.style"] == {"palette": {"bg": "#000000"}}
    assert out["audio"] == [(0, 1440), (0, 1440)] and set(tl.audio) == {1, 2}


def test_fusion_build_refuses_what_it_must_not_do(tmp_path):
    out = R.fusion_build(Resolve("21.0.0.47"), build_spec(tmp_path))
    assert not out["passed"] and "Resolve is now 21.0.0.47" in out["error"] and not out["changed"]
    res = Resolve()
    R.fusion_build(res, build_spec(tmp_path))
    again = R.fusion_build(res, build_spec(tmp_path))
    assert not again["passed"] and "already exists" in again["error"]
    bad = R.fusion_build(Resolve(), build_spec(tmp_path, shots=[{"name": "shot-001", "start": 5, "frames": 90}]))
    assert not bad["passed"] and "frame 0" in bad["error"]


def test_fusion_stills_exports_built_shots_and_names_the_empty_ones(tmp_path):
    res = Resolve()
    R.fusion_build(res, build_spec(tmp_path))
    items = res.manager.current.current.GetItemListInTrack("video", 1)
    items[1].comp.tools = 40                                            # the AI built shot-002 and shot-004
    items[3].comp.tools = 25
    res.manager.current = Project("Owner project")                     # the owner opened something else meanwhile
    out_dir = tmp_path / "stills"; out_dir.mkdir()
    out = R.fusion_stills(res, {"project_name": "Ep 1 - One Production test", "fps": 60, "out_dir": str(out_dir),
                               "shots": [{"name": n, "at": [10, 999]} for n in ("shot-001", "shot-002", "shot-003", "shot-004")]})
    assert out["passed"], out
    assert out["empty"] == ["shot-001", "shot-003"] and len(out["stills"]) == 4
    frames = [f for f, _ in res.manager.current.stills]
    assert frames == [90 + 10, 90 + 126, 330 + 10, 330 + 69]          # clamped inside each shot
    assert all(Path(p).name.startswith(("shot-002_f", "shot-004_f")) for p in out["stills"])


def test_a_stock_shot_puts_the_clip_at_its_frames_with_a_comp_on_top(tmp_path):
    clip = tmp_path / "pexels-video-7.mp4"; clip.write_bytes(b"x")
    spec = build_spec(tmp_path)
    spec["shots"][1].update(frames=128, stock="stock/pexels-video-7.mp4", stock_path=str(clip), **{"from": 2.0})
    spec["shots"][2].update(start=218, frames=112)                         # (the plan made the stock shot even)
    res = Resolve()
    out = R.fusion_build(res, spec)
    assert out["passed"], out
    assert out["shots"][1] == {"name": "shot-002", "start": 90, "frames": 128}
    items = res.manager.current.current.GetItemListInTrack("video", 1)
    assert len(items) == 4 and items[1].path == str(clip) and items[1].name == "shot-002"
    assert items[1].source == (60, 60 + 64)                                # 2 s into a 30 fps clip, 128 frames at 60 fps
    assert items[1].comp.data["OneProduction.shot"]["stock"] == "stock/pexels-video-7.mp4"
    # stills: a stock shot with nothing on top still shows its footage, so it is not "empty"
    out_dir = tmp_path / "st"; out_dir.mkdir()
    st = R.fusion_stills(res, {"project_name": spec["project_name"], "fps": 60, "out_dir": str(out_dir),
                               "shots": [{"name": "shot-002", "at": [5], "stock": True}, {"name": "shot-003", "at": [5]}]})
    assert st["empty"] == ["shot-003"]


def test_sound_effects_land_on_a3_at_their_frames(tmp_path):
    a = tmp_path / "001.wav"; a.write_bytes(b"x")
    b = tmp_path / "002.wav"; b.write_bytes(b"x")
    res = Resolve()
    out = R.fusion_build(res, build_spec(tmp_path, sfx=[{"path": str(a), "frame": 90}, {"path": str(b), "frame": 217},
                                                       {"path": str(a), "frame": 330}]))
    assert out["passed"], out
    tl = res.manager.current.current
    assert tl.audio_tracks == 3 and [i.start for i in tl.audio[3]] == [90, 217, 330]
    assert [i.start for i in tl.audio[1]] == [0] and [i.start for i in tl.audio[2]] == [0]        # voice A1, music A2


def test_stock_shots_get_whole_source_frames_without_gaps():
    from app.fusion import whole_source_frames
    timed = [(0, 90), (90, 127), (217, 113), (330, 70)]
    out = whole_source_frames(timed, [0.0, 30.0, 25.0, 30.0])
    assert out == [(0, 90), (90, 128), (218, 108), (326, 74)]              # 30 fps: even; 25 fps: x12; the last cut stays
    assert sum(f for _, f in out) == 400 and all(a + f == b for (a, f), (b, _) in zip(out, out[1:]))
    assert whole_source_frames(timed, [0.0, 29.97, 0.0, 0.0]) == timed     # non-integer rates conform as they are


def test_stock_shot_rules():
    shots = check_shots({"shots": [{"idea": "a", "stock": "stock/pexels-video-1.mp4", "from": "1.5"}]})
    assert shots[0]["stock"] == "stock/pexels-video-1.mp4" and shots[0]["from"] == 1.5
    for bad in ("work/x.mp4", "stock/pexels-photo-2.jpg", "../stock/a.mp4"):
        with pytest.raises(FusionError, match="stock"):
            check_shots({"shots": [{"idea": "a", "stock": bad}]})


class RenderProject(Project):
    """Render queue as in the Resolve 21.1 stubs: AddRenderJob -> id, StartRendering([ids]), status dicts."""
    def __init__(self, name):
        super().__init__(name)
        self.format, self.render, self.jobs, self.started = None, None, {}, []

    def SetCurrentRenderFormatAndCodec(self, fmt, codec): self.format = (fmt, codec); return True
    def SetRenderSettings(self, s): self.render = dict(s); return True

    def AddRenderJob(self):
        jid = f"job-{len(self.jobs) + 1}"
        self.jobs[jid] = {"JobStatus": "Ready", "CompletionPercentage": 0}
        return jid

    def StartRendering(self, ids, interactive=False):
        assert isinstance(ids, list)
        self.started += ids
        for j in ids:
            self.jobs[j] = {"JobStatus": "Rendering", "CompletionPercentage": 40}
        return True

    def GetRenderJobStatus(self, jid): return self.jobs[jid]


def test_render_queues_only_the_episode_timeline_and_reports_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "Project", RenderProject)
    res = Resolve()
    R.fusion_build(res, build_spec(tmp_path))
    ep = res.manager.current
    res.manager.current = Project("Owner project")                     # the owner had another project open
    spec = {"project_name": "Ep 1 - One Production test", "timeline": "Episode", "fps": 60, "width": 1920, "height": 1080,
            "target_dir": str(tmp_path / "work"), "name": "draft-1"}
    out = R.render_start(res, spec)
    assert out["passed"] and out["job"] == "job-1" and res.manager.current is ep and res.page == "deliver"
    assert ep.format == ("mp4", "H264") and ep.started == ["job-1"]
    assert ep.render["TargetDir"] == spec["target_dir"] and ep.render["CustomName"] == "draft-1"
    assert ep.render["FrameRate"] == 60.0 and ep.render["SelectAllFrames"] and ep.render["ExportAudio"]
    st = R.render_status(res, {"project_name": spec["project_name"], "job": "job-1"})
    assert st["passed"] and st["progress"] == 40 and not st["done"] and not st["failed"]
    ep.jobs["job-1"] = {"JobStatus": "Failed", "Error": "Disk full"}
    st = R.render_status(res, {"project_name": spec["project_name"], "job": "job-1"})
    assert st["failed"] and st["error"] == "Disk full"
    ep.jobs["job-1"] = {"JobStatus": "Complete", "CompletionPercentage": 100}
    assert R.render_status(res, {"project_name": spec["project_name"], "job": "job-1"})["done"]
    res.manager.current = Project("Owner project")
    gone = R.render_status(res, {"project_name": spec["project_name"], "job": "job-1"})
    assert not gone["passed"] and "no longer open" in gone["error"]


# ---------------------------------------------------------------- the service
ART = {"concept": "c", "palette": {"bg": "#111111", "ink": "#EEEEEE", "accent": "#FF0000"}, "fonts": {"heading": "Inter", "body": "Inter"}}


@pytest.mark.parametrize("art, msg", [
    ({**ART, "palette": {"bg": "#111111", "ink": "#EEEEEE"}}, "missing accent"),
    ({**ART, "palette": {**ART["palette"], "accent": "red"}}, "#1A2B3C"),
    ({**ART, "fonts": {"heading": "Comic Sans MS", "body": "Inter"}}, "installed"),
    ({**ART, "fonts": {"heading": "Inter"}}, '"body"'),
    ({**ART, "concept": " "}, "concept"),
])
def test_art_direction_is_checked(art, msg):
    with pytest.raises(FusionError, match=re.escape(msg)):
        check_art(art)


def test_shot_list_rules_and_frame_math():
    with pytest.raises(FusionError, match="needs \"anchor\""):
        check_shots({"shots": [{"idea": "a"}, {"idea": "b"}]})
    with pytest.raises(FusionError, match="idea"):
        check_shots({"shots": [{"text": "a"}]})
    assert frames_of([0, 1.5, 3.62], 5.0) == [(0, 90), (90, 127), (217, 83)]


class Service:
    """The app's Resolve service, but the runner runs in-process against the stand-in."""

    def __init__(self, res):
        self.enabled, self.res, self.requests = True, res, []

    def probe(self):
        return {"available": True, "version": self.res.version}

    def call(self, request, timeout):
        self.requests.append(request)
        return R.ACTIONS[request["action"]](self.res, request["spec"])


@needs_ffmpeg
def test_setup_and_stills_through_the_service(tmp_path):
    store = Store(tmp_path / "data")
    pid = store.create_project("F")["id"]
    files = store.files_dir(pid)
    master(voice(tmp_path / "raw.wav", 120, 8), files / "vo.wav")
    srt(files / "vo.srt", 8, [(0, 4, "first the gold standard ended"), (4, 8, "then the dollar floated freely")])
    (files / "work").mkdir()
    (files / "work" / "art.json").write_text(json.dumps(ART), encoding="utf-8")
    (files / "work" / "shots.json").write_text(json.dumps({"shots": [{"idea": "gold bars"}, {"anchor": "the dollar floated", "idea": "a floating dollar"}]}), encoding="utf-8")
    res = Resolve()
    svc = Fusion(store, tmp_path, Service(res), SimpleNamespace(resolve_mcp=False))
    with pytest.raises(FusionError, match="MCP switch is off"):
        svc.setup(pid, "work/shots.json", "work/art.json", "vo.wav", "vo.srt")
    svc.settings.resolve_mcp = True
    out = svc.setup(pid, "work/shots.json", "work/art.json", "vo.wav", "vo.srt", name="Gold")
    assert out["project"].startswith("Gold - One Production") and out["state"] == "work/fusion.json"
    assert [s["name"] for s in out["shots"]] == ["shot-001", "shot-002"] and out["shots"][1]["start_s"] == pytest.approx(5.0, abs=0.3)
    state = json.loads((files / "work" / "fusion.json").read_text(encoding="utf-8"))
    assert state["fps"] == 60 and sum(s["frames"] for s in state["shots"]) == pytest.approx(8 * 60, abs=2)
    tl = res.manager.current.current
    tl.GetItemListInTrack("video", 1)[0].comp.tools = 30
    st = svc.stills(pid, "work/fusion.json")
    assert st["empty"] == ["shot-002"] and st["folder"] == "work/stills"
    assert all(p.startswith("work/stills/shot-001_f") for p in st["stills"]) and len(st["stills"]) == 2
    srt(files / "vo.srt", 8, [(0, 4, "first the gold standard ended"),
                              (4, 8, "then the dollar floated freely and fast one two three four five six seven eight nine ten")])
    (files / "work" / "shots.json").write_text(json.dumps({"shots": [{"idea": "a"}, {"anchor": "freely", "idea": "b"},
                                                                       {"anchor": "and fast", "idea": "c"}]}), encoding="utf-8")
    with pytest.raises(FusionError, match="under 0.4 s"):
        svc.plan(pid, "work/shots.json", "work/art.json", "vo.wav", "vo.srt")
    # a stock clip must be long enough for its shot after "from"
    (files / "stock").mkdir()
    video(files / "stock" / "pexels-video-9.mp4", files / "vo.wav", 4.5)
    (files / "work" / "shots.json").write_text(json.dumps({"shots": [{"idea": "a", "stock": "stock/pexels-video-9.mp4", "from": 1},
                                                                       {"anchor": "the dollar floated", "idea": "b"}]}), encoding="utf-8")
    srt(files / "vo.srt", 8, [(0, 4, "first the gold standard ended"), (4, 8, "then the dollar floated freely")])
    with pytest.raises(FusionError, match="has only 3.5 s"):
        svc.plan(pid, "work/shots.json", "work/art.json", "vo.wav", "vo.srt")
    (files / "work" / "shots.json").write_text(json.dumps({"shots": [{"idea": "a", "stock": "stock/pexels-video-9.mp4"},
                                                                       {"anchor": "then the dollar", "idea": "b"}]}), encoding="utf-8")
    plan = svc.plan(pid, "work/shots.json", "work/art.json", "vo.wav", "vo.srt")
    assert plan["shots"][0]["stock_path"].endswith("pexels-video-9.mp4") and plan["shots"][0]["frames"] % 2 == 0
    # Resolve's Fusion clip is 5 s and cannot be lengthened: a longer shot left extra pieces (v2 re-draft, 2026-10-12)
    (files / "work" / "shots.json").write_text(json.dumps({"shots": [{"idea": "the whole thing"}]}), encoding="utf-8")
    with pytest.raises(FusionError, match=r"at most 5 s.*shot-001 \(8\.0 s\).*Split"):
        svc.plan(pid, "work/shots.json", "work/art.json", "vo.wav", "vo.srt")

