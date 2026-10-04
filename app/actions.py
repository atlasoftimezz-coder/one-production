"""Lets the chat assistant act on the studio, not just talk about it.

Every reply gets a short description of the project (files, voices, recent voiceovers and
their real pace) plus the one action it may propose. A proposal is a fenced ```action block
at the end of the reply; the server checks it against what actually exists and the user runs
it with one click. The assistant never starts GPU work itself.
"""
from __future__ import annotations

import json
import re

PACES = ("natural", "wpm", "grid", "grid_pad")
WPM_RANGE = (90, 240)
_BLOCK = re.compile(r"```action\s*\n(.*?)\n?```", re.S)

HELP = """You can start one studio tool: a voiceover (clones a reference voice, reads the script, checks
every section, adds mp3 + wav + subtitles to the project). To propose one, write one or two plain
sentences saying what you will do, then end the reply with exactly one block like this:

```action
{"tool": "voiceover", "file": "<script file in this project>", "voice": "<voice file>",
 "preset": "clone_exact", "pace_mode": "wpm", "target_wpm": 170}
```

Rules:
- Only use script files and voices listed above. pace_mode is one of natural, wpm, grid, grid_pad;
  target_wpm (90-240) only with pace_mode "wpm". Optional: "name" for the output file.
- "Faster/slower by N wpm" means: take the measured wpm of the latest voiceover above and add or
  subtract N, then use pace_mode "wpm".
- The user presses Run on a card to start it; never say it has already started or finished.
- If something needed is missing (no script file, unknown voice, voice engine not set up), say what
  is missing instead of writing a block. Do not ask for numbers you can read above.
- Do not claim you lack tools or mention any other modes or skills."""


def context(files: list[dict], voices: list[str] | None, presets: dict, jobs: list[dict]) -> str:
    scripts = [f["name"] for f in files if re.search(r"\.(docx|txt|md)$", f["name"], re.I)]
    lines = ["Studio state for this project:",
             "- Script files: " + (", ".join(scripts) if scripts else "none (the user must upload one)"),
             "- Other files: " + (", ".join(f["name"] for f in files if f["name"] not in scripts) or "none")]
    if voices is None:
        lines.append("- Voices: the voice engine is not running right now; it starts when a voiceover runs. "
                     "Use a voice the user names or one from an earlier voiceover.")
    else:
        lines.append("- Voices: " + (", ".join(voices) or "none"))
    if presets:
        lines.append("- Delivery presets: " + ", ".join(f"{k} ({v.get('label', k)})" for k, v in presets.items()))
    if jobs:
        lines.append("- Recent voiceovers (newest first):")
        for j in jobs[:5]:
            r = j.get("result") or {}
            pace = j.get("pace_mode") or "natural"
            asked = f"{pace}" + (f" {round(j['target_wpm'])} wpm" if pace == "wpm" and j.get("target_wpm") else "")
            got = f"measured {r['wpm']} wpm, {r.get('clock', '')}" if r.get("wpm") else j.get("status", "")
            lines.append(f"  - {j.get('name')}: script {j.get('source')}, voice {j.get('voice')}, "
                         f"preset {j.get('preset')}, pace asked {asked}; {got}")
    else:
        lines.append("- No voiceovers yet in this project.")
    return "\n".join(lines)


def extract(text: str) -> tuple[str, dict | None]:
    """(reply without the block, the proposed action or None). Only the last block counts."""
    blocks = list(_BLOCK.finditer(text))
    if not blocks:
        return text, None
    clean = _BLOCK.sub("", text).strip()
    try:
        action = json.loads(blocks[-1].group(1))
    except ValueError:
        return clean, {"error": "the assistant proposed an action I couldn't read"}
    return clean, action if isinstance(action, dict) else {"error": "the assistant proposed an unreadable action"}


def check(action: dict, files: list[dict], voices: list[str] | None, presets: dict) -> dict:
    """Normalise a proposal, or return {"error": ...} explaining why it can't run."""
    if "error" in action:
        return action
    if action.get("tool") != "voiceover":
        return {"error": f"unknown tool {action.get('tool')!r}"}
    names = {f["name"] for f in files}
    file = action.get("file")
    if file not in names:
        return {"error": f"there is no file called {file!r} in this project"}
    voice = action.get("voice")
    if not voice or (voices is not None and voice not in voices):
        return {"error": f"there is no voice called {voice!r}"}
    pace = action.get("pace_mode") or "natural"
    if pace not in PACES:
        return {"error": f"unknown pace {pace!r}"}
    out = {"tool": "voiceover", "file": file, "voice": voice, "pace_mode": pace,
           "preset": action.get("preset") if action.get("preset") in (presets or {}) or not presets
           else "clone_exact"}
    out["preset"] = out["preset"] or "clone_exact"
    if pace == "wpm":
        try:
            wpm = int(round(float(action.get("target_wpm"))))
        except (TypeError, ValueError):
            return {"error": "pace is 'words per minute' but no number was given"}
        if not WPM_RANGE[0] <= wpm <= WPM_RANGE[1]:
            return {"error": f"{wpm} wpm is outside {WPM_RANGE[0]}-{WPM_RANGE[1]}"}
        out["target_wpm"] = wpm
    if action.get("name"):
        out["name"] = str(action["name"])[:80]
    return out
