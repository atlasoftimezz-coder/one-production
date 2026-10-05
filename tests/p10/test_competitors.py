"""Competitor links in any format -> transcripts and numbers (no AI, no cost)."""
import asyncio
import json
import sys
import zipfile
from pathlib import Path

import pytest

from app import competitors as C
from app import faceless as F

FAKE = Path(__file__).parent / "fakes" / "fake_ytdlp.py"


def test_links_from_messy_text():
    text = """My competitors:
    - https://www.youtube.com/@MrFacts  and again https://youtube.com/@mrfacts/videos
    - youtu.be/FACTSVID002?si=abc  ,  https://www.youtube.com/watch?feature=share&v=FACTSVID003&t=10s
    - https://youtube.com/shorts/SHORTVID001 https://m.youtube.com/watch?v=FACTSVID002
    - https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv  https://www.youtube.com/playlist?list=PL1234567890ab
    - https://example.com/not-youtube, https://www.tiktok.com/@x
    """
    r = C.find_links(text)
    assert [(i["kind"], i["key"]) for i in r["items"]] == [
        ("channel", "@MrFacts"), ("video", "FACTSVID002"), ("video", "FACTSVID003"), ("video", "SHORTVID001"),
        ("channel", "channel/UCabcdefghijklmnopqrstuv"), ("playlist", "PL1234567890ab")]
    assert r["skipped"] == ["https://example.com/not-youtube", "https://www.tiktok.com/@x"]
    assert C.find_links("")["items"] == []


def _zip(path: Path, parts: dict) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, text in parts.items():
            z.writestr(name, text)
    return path


def test_links_from_csv_docx_xlsx(tmp_path):
    csv = tmp_path / "list.csv"
    csv.write_text("channel,link\nFacts,https://www.youtube.com/@mrfacts\n", encoding="utf-16")
    docx = _zip(tmp_path / "list.docx", {
        "word/document.xml": "<w:t>Competitors</w:t><w:hyperlink r:id='rId5'><w:t>Their best video</w:t></w:hyperlink>",
        "word/_rels/document.xml.rels": "<Relationship Id='rId5' Target='https://www.youtube.com/watch?v=FACTSVID002&amp;t=3' "
                                        "TargetMode='External'/>"})
    xlsx = _zip(tmp_path / "list.xlsx", {"xl/sharedStrings.xml": "<si><t>https://youtu.be/FACTSVID003</t></si>"})
    for f, key in ((csv, "@mrfacts"), (docx, "FACTSVID002"), (xlsx, "FACTSVID003")):
        assert [i["key"] for i in C.find_links(C.read_list_file(f))["items"]] == [key]


def test_clean_captions():
    manual = "WEBVTT\nKind: captions\nLanguage: en\n\n1\n00:00:00.000 --> 00:00:02.000\n- Hello there &amp; welcome.\n\n" \
             "00:00:02.000 --> 00:00:04.000\n[Music]\n\n00:00:04.000 --> 00:00:06.000\n(Derek laughing) Let's go.\n"
    assert C.clean_vtt(manual) == "Hello there & welcome. Let's go."
    rolling = ("WEBVTT\n\n00:00:00.000 --> 00:00:02.000 align:start position:0%\n\nthe city<00:00:00.5><c> under</c>\n\n"
               "00:00:02.000 --> 00:00:02.010\nthe city under\n\n00:00:02.010 --> 00:00:04.000\nthe city under\nthe sea is\n\n"
               "00:00:04.000 --> 00:00:04.010\nthe sea is\n\n00:00:04.010 --> 00:00:06.000\nthe sea is\nstill there\n")
    assert C.clean_vtt(rolling) == "the city under the sea is still there"


def test_numbers():
    text = ("You won't believe this. Why did it happen? " * 30).strip()
    n = C.measure(text, 60)
    assert n["words"] == 240 and n["wpm"] == 240 and n["questions_per_min"] == 30.0
    assert n["avg_sentence"] == 4.0 and n["you_per_100"] == 12.5 and n["punctuated"]
    assert C.measure("", None)["wpm"] is None


def test_collect_with_the_stand_in(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_YTDLP_LOG", str(tmp_path / "calls.jsonl"))
    yt = C.YtDlp([sys.executable, str(FAKE)])
    items = C.find_links("https://www.youtube.com/@mrfacts https://youtu.be/NOCAPTION01 "
                         "https://youtu.be/GONEGONE001 https://youtu.be/RATELIMIT01 https://youtu.be/AUTOCAPS001 "
                         "https://www.youtube.com/@nobody")["items"]
    said = []
    res = asyncio.run(C.collect(items, tmp_path / "competitors", yt, per_channel=2, progress=said.append, pause=0))
    titles = [t["title"] for t in res["transcripts"]]
    assert titles[:2] == ["The city under the sea", "Ten lost cities"]          # top 2 by views
    assert "Video RATELIMIT01" in titles and "Video AUTOCAPS001" in titles        # 429 retried, auto captions ok
    why = {p["link"].rsplit("/", 1)[-1].split("=")[-1]: p["why"] for p in res["problems"]}
    assert why["NOCAPTION01"] == "no English captions" and "unavailable" in why["GONEGONE001"]
    assert "does not exist" in why["@nobody"]
    t = res["transcripts"][0]
    body = (tmp_path / "competitors" / t["file"]).read_text(encoding="utf-8")
    assert "the city under the sea" in body.lower() and "[Music]" not in body and t["wpm"] == t["words"] // 2
    auto = next(x for x in res["transcripts"] if x["id"] == "AUTOCAPS001")
    assert auto["words"] < 600                                                      # rolling repeats removed
    nums = (tmp_path / "competitors" / "_numbers.md").read_text(encoding="utf-8")
    assert "median" in nums and "Not transcribed" in nums and "NOCAPTION01" in nums
    assert not (tmp_path / "competitors" / ".tmp").exists()
    assert any("Transcript" in s for s in said)
    # run again: saved transcripts are reused, nothing fetched twice
    before = len((tmp_path / "calls.jsonl").read_text().splitlines())
    res2 = asyncio.run(C.collect(C.find_links("https://youtu.be/FACTSVID002")["items"], tmp_path / "competitors", yt, pause=0))
    assert res2["transcripts"][0]["title"] == "The city under the sea"
    assert len((tmp_path / "calls.jsonl").read_text().splitlines()) == before
    import statistics
    assert C.summary(res["transcripts"])["median_wpm"] == round(statistics.median(t["wpm"] for t in res["transcripts"]))


# ---------------------------------------------------------------- FacelessOS files

def fake_facelessos(root: Path, marker="SKILLTEXT-7f3a") -> Path:
    """A folder shaped like the user's download: nested version folders and a __MACOSX decoy."""
    old = root / "Course v3"
    new = root / "Course v4" / "skills"
    mac = root / "Course v4" / "__MACOSX" / "skills"
    for d, names in ((old, F.COMMON_WRITER[:2]), (new, sorted({n for r in F.ROLES.values() for n in r} |
                                                                {n for v in F.BY_FORMAT.values() for n in v})),
                     (mac, [F.MASTER])):
        d.mkdir(parents=True, exist_ok=True)
        for n in names:
            (d / n).write_text(f"# {n}\n{marker} {n}\n", encoding="utf-8")
    return new


def test_facelessos_folder_and_skills(tmp_path):
    skills = fake_facelessos(tmp_path / "Faceless os")
    assert F.find(tmp_path / "Faceless os") == skills                     # newest, most files, not __MACOSX
    assert F.find(skills) == skills and F.find(tmp_path / "nothing") is None and F.find(None) is None
    w = [p.name for p in F.skills_for(skills, "writer", "true_crime")]
    assert w[0] == F.MASTER and "true-crime-skill.md" in w and "authenticity-audit-skill.md" not in w
    r = [p.name for p in F.skills_for(skills, "reviewer", "true_crime")]
    assert "authenticity-audit-skill.md" in r and "true-crime-skill.md" not in r
    text = F.load(skills, "reviewer")
    assert "===== FacelessOS: authenticity-audit-skill.md" in text and "SKILLTEXT-7f3a" in text
    assert F.status(str(tmp_path / "Faceless os"))["ok"] and not F.status(None)["ok"]


def test_files_in_sub_folders_are_listed_and_safe(tmp_path):
    from app.storage import NotFound, Store
    st = Store(tmp_path)
    pid = st.create_project("x")["id"]
    root = st.files_dir(pid)
    (root / "scripts").mkdir()
    (root / "scripts" / "a.md").write_text("hi")
    (root / "competitors" / ".tmp").mkdir(parents=True)
    (root / "competitors" / ".tmp" / "x.vtt").write_text("tmp")
    (root / "top.txt").write_text("t")
    (root / "b.mp3.part").write_text("p")
    assert [f["name"] for f in st.list_files(pid)] == ["top.txt", "scripts/a.md"]
    assert st.file_path(pid, "scripts/a.md").read_text() == "hi"
    assert st.file_path(pid, r"scripts\a.md").read_text() == "hi"
    for bad in ("../../outside.txt", "scripts/../../x", "", "scripts"):
        with pytest.raises(NotFound):
            st.file_path(pid, bad)
