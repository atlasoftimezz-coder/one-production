"""Modules (plan R14): the app works with Core alone; everything else is a switch.

    core        chat, accounts, usage, handoff, files, backups      always on
    voice       clone + read-check + mastering + subtitles          GPU, processor (slow) or off
    later       subtitles for any audio, animation, video edit, stock, research - shown greyed
                with what they will need, because they are built in Phase 2 (P10), not yet

The voice module is either the user's own voice app (settings voice_app_dir/voice_python,
as on the studio PC) or the one installed by the setup wizard under data_dir/modules/voice,
running the engine that ships with One Production (./voice).
"""
from __future__ import annotations

import json
from pathlib import Path

from .installer import VoiceRecipe

ENGINE_DIR = Path(__file__).resolve().parent.parent / "voice"
VOICE_MODES = ("gpu", "cpu", "off")

LATER = [
    {"id": "subtitles", "label": "Subtitles for any audio",
     "what": "Subtitles for audio you bring (voiceovers already come with subtitles).",
     "needs": "Speech recognition - runs on the processor, no graphics card needed."},
    {"id": "animation", "label": "Animation",
     "what": "Hand-drawn style explainer shots, rendered to video.", "needs": "ffmpeg, the processor."},
    {"id": "video", "label": "Video editing",
     "what": "Puts voiceover, visuals and music together into the finished video.",
     "needs": "ffmpeg (free). DaVinci Resolve control needs the paid Resolve Studio."},
    {"id": "stock", "label": "Stock footage",
     "what": "Finds footage for each section.", "needs": "Free sources; Storyblocks needs its paid key."},
    {"id": "research", "label": "Research",
     "what": "Niche and competitor research from inside the chat.",
     "needs": "Your own NexLev / vidIQ accounts."},
]


class Modules:
    def __init__(self, settings, data_dir: Path):
        self.settings = settings
        self.data_dir = data_dir
        self.file = data_dir / "modules.json"

    def _load(self) -> dict:
        if self.file.exists():
            try:
                return json.loads(self.file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return {}

    def set_voice(self, mode: str) -> None:
        if mode not in VOICE_MODES:
            raise ValueError("voice mode is gpu, cpu or off")
        d = self._load()
        d["voice"] = mode
        self.file.write_text(json.dumps(d), encoding="utf-8")

    @staticmethod
    def suggested_voice_mode(gpu: dict | None) -> str:
        """The scan decides the default: a real NVIDIA card with 8 GB -> graphics card, else off
        (the processor is offered, but at ~20x slower it is never the default)."""
        return "gpu" if gpu and gpu.get("vram_gb", 0) >= 7.5 else "off"

    def voice_mode(self, gpu: dict | None) -> str:
        return self._load().get("voice") or self.suggested_voice_mode(gpu)

    def external(self) -> bool:
        return bool(self.settings.voice_app_dir and self.settings.voice_python)

    def recipe(self, gpu: dict | None, mode: str | None = None) -> VoiceRecipe:
        mode = mode or self.voice_mode(gpu)
        return VoiceRecipe(self.data_dir, ENGINE_DIR, "cpu" if mode == "off" else mode, gpu)

    def voice_config(self, gpu: dict | None) -> tuple:
        """(app_dir, python, env, off_reason) for VoiceService.configure()."""
        s = self.settings
        if self.external():
            return Path(s.voice_app_dir), Path(s.voice_python), {}, None
        mode = self.voice_mode(gpu)
        if mode == "off":
            return None, None, {}, "the voice module is turned off - switch it on in System, Modules"
        r = self.recipe(gpu, mode)
        if not r.ready():
            return None, None, {}, "the voice module isn't installed yet - open System, Modules to install it"
        env = {**r.env(), "VOAPP_VOICES": str(self.data_dir / "voices"),
               "VOAPP_OUTPUTS": str(self.data_dir / "cache" / "voice-outputs"),
               "VOAPP_JOBS": str(self.data_dir / "cache" / "voice-engine")}
        return ENGINE_DIR, r.python, env, None

    def status(self, gpu: dict | None, installing: bool = False) -> list[dict]:
        out = [{"id": "core", "label": "Core", "state": "ready", "always": True,
                "what": "Chat, accounts, usage, handoff, files, backups.", "needs": "Nothing extra."}]
        mode = self.voice_mode(gpu)
        has_gpu = bool(gpu and gpu.get("vram_gb", 0) >= 7.5)
        voice = {"id": "voice", "label": "Voice", "mode": mode, "suggested": self.suggested_voice_mode(gpu),
                 "what": "Clone a voice, check every section was read right, master to -14 LUFS, "
                         "mp3 + wav + subtitles.",
                 "needs": "An NVIDIA graphics card with 8 GB or more. Without one it can run on the "
                          "processor - about 20 times slower.",
                 "gpu_ok": has_gpu, "external": self.external()}
        if self.external():
            voice["state"] = "ready"
            voice["detail"] = f"your own voice app ({self.settings.voice_app_dir})"
        elif installing:
            voice["state"] = "installing"
        elif mode == "off":
            voice["state"] = "off"
        else:
            r = self.recipe(gpu, mode)
            if r.ready():
                voice["state"] = "ready"
                voice["detail"] = "installed in the data folder"
            elif r.marker.exists():
                voice["state"] = "broken"
                voice["detail"] = "something is missing since it was installed - Install again repairs it"
            else:
                voice["state"] = "needs_install"
        if not has_gpu and not self.external():
            voice["warning"] = ("No NVIDIA card with 8 GB found - the voice module can only run on the processor "
                                "(about 20x slower).")
        out.append(voice)
        out += [{**m, "state": "later", "detail": "Built in Phase 2 (P10)."} for m in LATER]
        return out
