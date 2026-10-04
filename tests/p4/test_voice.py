"""R5 + R12: voiceover from the chat through the separate voice app, behind the GPU gate.
Uses a stand-in voice app - no GPU is touched by these tests."""
import os
import socket
import time

from .conftest import make_docx, wait_job

SCRIPT = "## Water\n\nWater can make the brain swell.\n\n## Oxygen\n\nOxygen can damage cells.\n"


def upload(c, pid, name, data: bytes):
    r = c.post(f"/api/projects/{pid}/files", files={"files": (name, data)})
    assert r.status_code == 200
    return r.json()[0]["name"]


def start(c, pid, **body):
    body.setdefault("voice", "human_ref48.wav")
    return c.post(f"/api/projects/{pid}/voice", json=body)


# ---------------------------------------------------------------- gate (R12)
def test_first_voiceover_needs_approval(client, project):
    r = start(client, project, text=SCRIPT)
    assert r.status_code == 409
    need = r.json()["needs_approval"]
    assert need["tool"] == "voiceover" and need["cost_class"] == "heavy" and "GPU" in need["uses"]
    assert client.fake.specs == [], "nothing may start before approval"


def test_allow_once_is_not_remembered(client, project):
    assert start(client, project, text=SCRIPT, approve="once").status_code == 200
    assert start(client, project, text=SCRIPT).status_code == 409


def test_always_allow_is_remembered_and_revocable(client, project):
    assert start(client, project, text=SCRIPT, approve="always").status_code == 200
    assert start(client, project, text=SCRIPT).status_code == 200
    assert client.get("/api/approvals").json() == {"voiceover": True}
    client.delete("/api/approvals/voiceover")
    assert start(client, project, text=SCRIPT).status_code == 409


# ---------------------------------------------------------------- the job
def test_full_job_puts_mp3_wav_srt_in_the_project(client, project):
    job = start(client, project, text=SCRIPT, name="Ep 1", approve="always").json()
    assert job["status"] == "starting"
    done = wait_job(client, job["id"])
    assert done["status"] == "done", done
    assert done["files"] == ["Ep 1.mp3", "Ep 1.wav", "Ep 1.srt"]
    assert done["result"]["clock"] == "1:01" and done["result"]["flagged"] == []
    files = {f["name"] for f in client.get(f"/api/projects/{project}/files").json()}
    assert {"Ep 1.mp3", "Ep 1.wav", "Ep 1.srt"} <= files
    srt = client.get(f"/api/projects/{project}/files/Ep 1.srt").text
    assert "Water can make the brain swell." in srt          # made from the script that was sent
    spec = client.fake.specs[-1]
    assert spec["script"] == SCRIPT and spec["voice"] == "human_ref48.wav"
    assert spec["preset"] == "clone_exact" and spec["auto_fix"] is True


def test_history_records_request_and_result(client, project):
    job = start(client, project, text=SCRIPT, approve="always").json()
    wait_job(client, job["id"])
    hist = client.get(f"/api/projects/{project}/chat").json()
    assert hist[0]["role"] == "user" and hist[0]["tool_request"] == "voiceover"
    tool = [m for m in hist if m["role"] == "tool"]
    assert tool and tool[-1]["job"] == job["id"] and tool[-1]["status"] == "done"


def test_progress_is_visible_while_running(client, project):
    job = start(client, project, text=SCRIPT, approve="always").json()
    seen = set()
    for _ in range(200):
        j = client.get(f"/api/voicejobs/{job['id']}").json()
        seen.add(j["status"])
        if j["sections"]:
            seen.add("had-sections")
        if j["status"] in ("done", "failed"):
            break
        time.sleep(0.01)
    assert "done" in seen and "had-sections" in seen


def test_voice_app_failure_is_reported(client, project):
    client.fake.fail = True
    job = start(client, project, text=SCRIPT, approve="always").json()
    done = wait_job(client, job["id"])
    assert done["status"] == "failed" and "CUDA out of memory" in done["error"]
    assert client.get(f"/api/projects/{project}/chat").json()[-1]["status"] == "failed"


def test_finished_job_survives_restart(make_client, client, project):
    job = start(client, project, text=SCRIPT, approve="always").json()
    wait_job(client, job["id"])
    fresh = make_client()
    assert fresh.get(f"/api/voicejobs/{job['id']}").json()["status"] == "done"


def test_same_name_never_overwrites(client, project):
    for _ in range(2):
        wait_job(client, start(client, project, text=SCRIPT, name="Ep", approve="always").json()["id"])
    files = {f["name"] for f in client.get(f"/api/projects/{project}/files").json()}
    assert {"Ep.mp3", "Ep (1).mp3", "Ep.srt", "Ep (1).srt"} <= files


# ---------------------------------------------------------------- scripts from files
def test_word_document_headings_become_titles(client, project, tmp_path):
    docx = make_docx(tmp_path / "s.docx", [("Title", "Every Substance"), ("Heading1", "Water"),
                                            (None, "Water can make the brain swell."),
                                            ("Heading1", "Production notes - not narrated"),
                                            (None, "Thumbnail: red and blue.")])
    name = upload(client, project, "Episode script.docx", docx.read_bytes())
    stats = client.post(f"/api/projects/{project}/script-stats", json={"file": name}).json()
    assert stats["words"] == 6 and stats["sections"] == 1       # only spoken sections count
    job = start(client, project, file=name, approve="always").json()
    wait_job(client, job["id"])
    sent = client.fake.specs[-1]
    assert "## Water" in sent["script"] and "# Every Substance" in sent["script"]
    assert sent["name"] == "Episode script"                       # named after the file


def test_dialog_counts_words_exactly_like_the_voice_app():
    # found on the real render: the dialog said 1195 words, the voice spoke 1219, because
    # the voice app splits joined dashes ("door—a") and keeps untitled lead-in text
    from app.scripts import stats
    text = ("Cold open line.\n\n## 0:27 - Water\nWater—the quiet one—shapes you.\n\n"
            "## Sources\nsomeone, 2024\n\n## Empty heading\n\n"
            "**Target runtime:** 8 min\n> editor note\n*[cut to map]*\n")
    assert stats(text) == {"words": 11, "sections": 2, "minutes_at_155": 0.1}


def test_utf16_text_script_is_read_correctly(client, project):
    text = "## Water\n\nWater — about 60% of you.\n"
    name = upload(client, project, "script.txt", b"\xff\xfe" + text.encode("utf-16-le"))
    stats = client.post(f"/api/projects/{project}/script-stats", json={"file": name}).json()
    assert stats["words"] == 6
    wait_job(client, start(client, project, file=name, approve="always").json()["id"])
    assert client.fake.specs[-1]["script"] == text


def test_metadata_lines_not_counted(client, project):
    stats = client.post(f"/api/projects/{project}/script-stats",
                        json={"text": "# T\n**Target runtime:** 8 minutes\n\nOne two three.\n"}).json()
    assert stats["words"] == 3


def test_bad_inputs(client, project):
    assert start(client, project, approve="always").status_code == 400                      # no script
    assert start(client, project, file="nope.docx", approve="always").status_code == 404
    pic = upload(client, project, "thumb.png", b"\x89PNG")
    assert start(client, project, file=pic, approve="always").status_code == 400
    assert start(client, project, text=SCRIPT, pace_mode="warp", approve="always").status_code == 400
    assert start(client, project, text=SCRIPT, pace_mode="wpm", approve="always").status_code == 400
    assert client.get("/api/voicejobs/nope").status_code == 404


# ---------------------------------------------------------------- setup / not set up
def test_status_lists_voices_and_presets(client):
    st = client.get("/api/voice/status").json()
    assert st["configured"] and st["running"]
    assert "human_ref48.wav" in st["voices"] and "clone_exact" in st["presets"]


def test_not_set_up_is_explained_not_crashed(make_client):
    c = make_client(configured=False, transport="real", url="http://127.0.0.1:9")
    st = c.get("/api/voice/status").json()
    assert st["configured"] is False and len(st["missing"]) == 2 and st["running"] is False
    pid = c.post("/api/projects", json={"name": "x"}).json()["id"]
    job = start(c, pid, text=SCRIPT, approve="always").json()
    done = wait_job(c, job["id"])
    assert done["status"] == "failed" and "not set up" in done["error"]


def test_voice_app_is_started_in_the_background(make_client):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    c = make_client(transport="real", url=f"http://127.0.0.1:{port}")
    assert c.get("/api/voice/status").json()["running"] is False
    st = c.post("/api/voice/start").json()
    try:
        assert st["running"] is True and "human_ref48.wav" in st["voices"]
    finally:
        os.system(f'for /f "tokens=5" %a in (\'netstat -ano ^| findstr :{port} ^| findstr LISTENING\') do taskkill /PID %a /F >nul 2>&1')


def test_reference_clip_can_be_heard_before_choosing(client):
    r = client.get("/api/voice/sample/human_ref48.wav")
    assert r.status_code == 200 and r.content == b"RIFF....WAVEhuman_ref48.wav"
    assert r.headers["content-type"] == "audio/wav"
    for bad in ("nobody.wav", "..%2F..%2Fsecret.wav", "%2E%2E%5Cvoices%5Chuman_ref48.wav"):
        assert client.get(f"/api/voice/sample/{bad}").status_code == 404, bad
    assert client.get("/api/voice/sample/human_ref48.wav",
                      headers={"X-OneProd-Token": "wrong"}).status_code == 401
