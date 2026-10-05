"""FacelessOS skills for Script Studio (P10).

FacelessOS is a scriptwriting system (a folder of Markdown "skills"). A copy ships with One Production
in `facelessos/` (the owner's decision, 2026-10-05: "I can handle the licence") and is used by default;
a folder the user picks (e.g. a newer version) wins. The files are handed to the writer / reviewer as
instructions at run time - never copied into project files, backups or diagnostics.
"""
from __future__ import annotations

from pathlib import Path

BUNDLED = Path(__file__).resolve().parent.parent / "facelessos"     # ships with the app
MASTER = "faceless-scripts-os-master.md"
FORMATS = {
    "documentary": "Documentary", "listicle": "Listicle", "expose": "Exposé", "true_crime": "True crime",
    "biography": "Biography / celebrity", "explainer": "Explainer / how-to",
}
COMMON_WRITER = [MASTER, "script-structures-skill.md", "REAL-FACELESS-HOOK-SWIPE.md", "NICHE-SPECIFIC-HOOKS.md",
                 "retention-mechanics-skill.md", "visual-scripting-skill.md", "outro-psychology-skill.md"]
BY_FORMAT = {
    "documentary": ["heros-journey-skill.md"],
    "true_crime": ["true-crime-skill.md"],
    "biography": ["celebrity-documentary-skill.md", "heros-journey-skill.md"],
}
ROLES = {
    "writer": COMMON_WRITER,
    "reviewer": [MASTER, "retention-mechanics-skill.md", "authenticity-audit-skill.md", "REAL-FACELESS-HOOK-SWIPE.md",
                 "title-formulas-skill.md"],
    "analyst": ["REAL-FACELESS-HOOK-SWIPE.md", "script-structures-skill.md"],
}
# the reviewer's score card - every line is checked against the FacelessOS files above
RUBRIC = {
    "hook": "Hook: FacelessOS hook pattern for the format, conversational flow, a reason to stay in the first 30 seconds",
    "structure": "Structure: the FacelessOS template for the format, clear progression, no dead sections",
    "retention": "Retention: dopamine ladder, setup-tension-resolution, but/therefore, none of the 4 deadly mistakes",
    "rehooks": "Rehooks and open loops at the right spacing, transitions that pull forward",
    "anti_slop": "Anti-AI slop: none of the 8 patterns in the master file's checklist; reads naturally aloud",
    "authenticity": "Authenticity audit: original angle and wording, at least 5 of 7 uniqueness signals, zero red flags; "
                    "nothing copied from the competitor transcripts",
    "style_match": "Matches the competitor style guide (pace, sentence rhythm, tone, POV) without copying phrases",
    "length": "Length fits the target (spoken words vs target words)",
    "outro": "Outro / CTA follows the outro psychology skill",
}


def find(folder: str | Path | None) -> Path | None:
    """The skills folder inside what the user picked: the one holding the master file, preferring
    the folder with the most skill files (newest version), never macOS '__MACOSX' copies."""
    if not folder:
        return None
    root = Path(folder)
    if not root.is_dir():
        return None
    if (root / MASTER).is_file():
        cands = [root]
    else:
        cands = [p.parent for p in root.rglob(MASTER) if "__MACOSX" not in p.parts]
    if not cands:
        return None
    return max(cands, key=lambda d: (len(list(d.glob("*.md"))), len(str(d))))


def skills_for(folder: Path, role: str, fmt: str | None = None) -> list[Path]:
    names = list(ROLES[role]) + (BY_FORMAT.get(fmt or "", []) if role == "writer" else [])
    return [folder / n for n in names if (folder / n).is_file()]


def load(folder: Path, role: str, fmt: str | None = None) -> str:
    parts = ["Use the FacelessOS scriptwriting system below (the user's own licensed copy). "
             "Follow it closely; where it offers a choice, pick what fits the brief and the style guide."]
    for f in skills_for(folder, role, fmt):
        parts.append(f"\n\n===== FacelessOS: {f.name} =====\n\n" + f.read_text(encoding="utf-8", errors="replace"))
    return "".join(parts)


def resolve(folder: str | None) -> Path | None:
    """The user's own folder when set and valid, otherwise the copy that ships with the app."""
    return find(folder) or find(BUNDLED)


def status(folder: str | None) -> dict:
    own = find(folder)
    d = own or find(BUNDLED)
    if not d:
        return {"ok": False, "folder": folder or None, "source": None,
                "why": "no FacelessOS files found - pick the folder where they are"}
    out = {"ok": True, "folder": folder, "skills_dir": str(d), "source": "own" if own else "bundled",
           "files": sorted(p.name for p in d.glob("*.md"))}
    if folder and not own:
        out["why"] = "your folder has no FacelessOS files - using the copy that comes with One Production"
    return out
