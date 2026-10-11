"""Competitor style probe: a video with known cuts, colours and sound measured exactly (no AI)."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

from app import style_probe
from app.competitors import FetchError, YtDlp

from tests.p10.media_fixtures import FF, cut_video, needs_ffmpeg

FAKE = Path(__file__).parent / "fakes" / "fake_ytdlp.py"
LENGTHS = [2.0, 1.5, 3.0, 1.0, 2.5, 2.0]          # cuts at 2.0, 3.5, 6.5, 7.5, 10.0


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    if FF is None:
        pytest.skip("ffmpeg is not installed")
    return cut_video(tmp_path_factory.mktemp("style") / "competitor.mp4", LENGTHS)


@needs_ffmpeg
def test_cuts_and_shot_lengths_are_measured(clip):
    cuts = style_probe.cut_times(FF, clip)
    assert cuts == pytest.approx([2.0, 3.5, 6.5, 7.5, 10.0], abs=0.07)
    p = style_probe.pace(style_probe.shots_of(cuts, sum(LENGTHS)), sum(LENGTHS))
    assert p["shots"] == 6 and p["cuts"] == 5
    assert p["cuts_per_min"] == pytest.approx(5 / (12 / 60), abs=0.5)
    assert p["shot_median"] == pytest.approx(2.0, abs=0.1)


@needs_ffmpeg
def test_full_analysis_writes_numbers_frames_and_sheets(clip, tmp_path):
    text = "Why did the city vanish? Nobody knows. " * 20
    m = style_probe.analyse(FF, clip, tmp_path / "probe", text, {"id": "abc", "title": "T"})
    assert m["pace"]["shots"] == 6
    assert len(m["frames"]) == 6 and all((tmp_path / "probe" / f["file"]).is_file() for f in m["frames"])
    assert m["sheets"] and (tmp_path / "probe" / m["sheets"][0]).is_file()
    pic = m["picture"]
    assert 0.2 <= pic["flat_frame_share"] <= 0.75          # 3 of 6 shots are plain colour (5.5 of 12 s)
    assert pic["main_colours"] and pic["main_colours"][0]["hex"].startswith("#")
    s = m["sound"]
    assert s["has_audio"] and s["cuts_with_a_hit"] >= 0.8   # a click on every cut
    assert s["music_bed_hint"] is True                      # the tone never stops
    assert m["words"]["wpm"] and json.loads((tmp_path / "probe" / "measure.json").read_text(encoding="utf-8"))["seconds"]


@needs_ffmpeg
def test_no_bed_and_no_hits_are_told_apart(tmp_path):
    quiet = cut_video(tmp_path / "quiet.mp4", [2.0, 2.0, 2.0], hits=False, bed=False)
    s = style_probe.sound(FF, quiet, style_probe.shots_of([2.0, 4.0], 6.0), 6.0)
    assert not s.get("cuts_with_a_hit")


def test_combine_takes_medians_by_code():
    a = {"pace": {"cuts_per_min": 10, "shot_median": 3}, "sound": {"has_audio": True, "music_bed_hint": True}}
    b = {"pace": {"cuts_per_min": 20, "shot_median": 5}, "sound": {"has_audio": True, "music_bed_hint": False}}
    c = {"pace": {"cuts_per_min": 30, "shot_median": 4}, "sound": {"has_audio": True, "music_bed_hint": True}}
    out = style_probe.combine([a, b, c])
    assert out["pace"]["cuts_per_min"] == 20 and out["pace"]["shot_median"] == 4
    assert out["sound"]["music_bed_hint_share"] == pytest.approx(0.67, abs=0.01) and out["videos"] == 3


@needs_ffmpeg
def test_download_through_yt_dlp_gets_video_meta_and_captions(clip, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_YTDLP_VIDEO", str(clip))
    yt = YtDlp([sys.executable, str(FAKE)])
    got = asyncio.run(style_probe.download(yt, "FACTSVID002", tmp_path / "dl", FF))
    assert got["video"].name == "FACTSVID002.mp4" and got["meta"]["title"] == "The city under the sea"
    assert "vanish" in got["text"]
    with pytest.raises(FetchError, match="could not be downloaded"):
        asyncio.run(style_probe.download(yt, "TOOLONG0001", tmp_path / "dl2", FF))
