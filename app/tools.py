"""Tool registry and the heavy-job gate (plan R12).

Every tool declares a cost class. Anything heavier than "light" must be approved before it
runs - once, or always (remembered per tool in data_dir/approvals.json). Nothing heavy ever
starts silently, so the app never uses a friend's GPU, disk or money without saying so.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

LIGHT, HEAVY, DOWNLOAD, PAID = "light", "heavy", "download", "paid"

TOOLS = {
    "voiceover": {
        "label": "Voiceover",
        "cost_class": HEAVY,
        "uses": "your graphics card (GPU) for the voice clone, roughly 1 minute of work per "
                "minute of audio, plus a few minutes of CPU for the read-check and subtitles",
    },
    "image_assets": {
        "label": "Generated assets",
        "cost_class": HEAVY,
        "uses": "your graphics card (GPU) for about 10 seconds per object (FLUX.1-schnell, local), plus a minute or "
                "two to load the model the first time",
    },
    "video_render": {
        "label": "Video render",
        "cost_class": HEAVY,
        "uses": "your processor (CPU) for roughly 1-3 minutes per minute of video, and some disk space "
                "while it works (no graphics card)",
    },
    "voice_tune": {
        "label": "Finding the best settings for a voice",
        "cost_class": HEAVY,
        "uses": "your graphics card (GPU) for about 3 minutes - it renders the same sentence 7 ways "
                "and keeps the one closest to the voice",
    },
}


class Approvals:
    def __init__(self, data_dir: Path):
        self.file = data_dir / "approvals.json"
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            return json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def always(self, tool: str) -> bool:
        return self._load().get(tool) == "always"

    def set_always(self, tool: str, on: bool) -> None:
        with self._lock:
            d = self._load()
            if on:
                d[tool] = "always"
            else:
                d.pop(tool, None)
            self.file.write_text(json.dumps(d, indent=1), encoding="utf-8")

    def check(self, tool: str, approve: str | None) -> dict | None:
        """None = may run. Otherwise a description of what needs approving.
        approve: None (ask) | "once" | "always"."""
        info = TOOLS[tool]
        if info["cost_class"] == LIGHT or self.always(tool):
            return None
        if approve == "always":
            self.set_always(tool, True)
            return None
        if approve == "once":
            return None
        return {"tool": tool, **info}
