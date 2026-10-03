"""Friends install this on their own PCs, so code must never point at this machine."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCANNED = ("app", "web", "tools")
SUFFIXES = {".py", ".html", ".js", ".css", ".json", ".bat", ".ps1"}
FORBIDDEN = re.compile(r"[A-Za-z]:[\\/]+(Software|Users)[\\/]", re.IGNORECASE)


def test_no_machine_specific_paths_in_code():
    hits = []
    for folder in SCANNED:
        for f in (ROOT / folder).rglob("*"):
            if f.is_file() and f.suffix in SUFFIXES:
                text = f.read_text(encoding="utf-8", errors="ignore")
                for n, line in enumerate(text.splitlines(), 1):
                    if FORBIDDEN.search(line):
                        hits.append(f"{f.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not hits, "hard-coded paths found:\n" + "\n".join(hits)
