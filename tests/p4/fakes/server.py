"""Stand-in for the voiceover app's server. One Production starts it exactly like the real
one ("server.py --port N") - used to test auto-start and to drive the UI without a GPU.

Implements /api/meta, POST /api/jobs and GET /api/jobs/<id>. A job reports two polls of
progress, then "done" with tiny fake wav/mp3 files written next to this script.
"""
import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

OUT = Path(__file__).parent / "out"
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()


class H(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/meta":
            return self._json(200, {"voices": ["human_ref48.wav", "buster_ref48.wav"],
                                    "presets": {"clone_exact": {"label": "Clone the reference exactly"},
                                                "narrator": {"label": "Narrator"}}})
        if self.path.startswith("/api/jobs/"):
            jid = self.path.rsplit("/", 1)[1]
            with LOCK:
                job = JOBS.get(jid)
                if not job:
                    return self._json(404, {"detail": "no such job"})
                job["polls"] += 1
                polls = job["polls"]
            secs = [{"i": 0, "title": "Water", "status": "ok", "wer": 0.0},
                    {"i": 1, "title": "Oxygen", "status": "rendering" if polls < 3 else "ok",
                     "wer": None if polls < 3 else 0.01}]
            if polls < 3:
                return self._json(200, {"status": "running", "message": f"section 2/2: rendering",
                                        "sections": secs, "result": None, "error": None})
            OUT.mkdir(exist_ok=True)
            wav, mp3 = OUT / f"{job['name']}.wav", OUT / f"{job['name']}.mp3"
            wav.write_bytes(b"RIFF....WAVEfake")
            mp3.write_bytes(b"ID3fake-mp3")
            return self._json(200, {"status": "done", "message": "done", "sections": secs, "error": None,
                                    "result": {"wav": str(wav), "mp3": str(mp3), "seconds": 61.2,
                                               "clock": "1:01", "words": 160, "wpm": 157, "flagged": []}})
        self._json(404, {"detail": "not found"})

    def do_POST(self):
        if self.path == "/api/jobs":
            spec = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
            with LOCK:
                jid = f"job{len(JOBS) + 1}"
                JOBS[jid] = {"name": spec["name"], "polls": 0}
            return self._json(200, {"id": jid})
        self._json(404, {"detail": "not found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ThreadingHTTPServer(("127.0.0.1", ap.parse_args().port), H).serve_forever()
