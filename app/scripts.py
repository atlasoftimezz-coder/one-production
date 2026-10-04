"""Reading a script out of whatever file the user uploaded.

Scripts arrive as .docx (Word), .txt (often UTF-16 when exported from Word or PowerShell)
or .md. The voice engine wants plain text with optional "## Heading" / "[m:ss] Heading"
lines, which it treats as on-screen titles and does not speak.
"""
from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path

SUPPORTED = {".docx", ".txt", ".md"}


def read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        # UTF-16 without a BOM shows up as every other byte being NUL
        if raw[1:200:2].count(0) > 50:
            return raw.decode("utf-16-le")
        return raw.decode("cp1252", errors="replace")


def read_docx(path: Path) -> str:
    """Paragraphs in order; Word heading styles become markdown headings."""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    lines = []
    for para in xml.split("</w:p>"):
        style = re.search(r'w:pStyle w:val="([^"]+)"', para)
        text = "".join(html.unescape(t) for t in re.findall(r"<w:t[^>]*>([^<]*)</w:t>", para)).strip()
        if not text:
            lines.append("")
            continue
        s = style.group(1).lower() if style else ""
        if s == "title":
            lines.append("# " + text)                     # the document title sits above chapters
        elif s.startswith("heading"):
            level = int(re.sub(r"\D", "", s) or 1) + 1    # Heading 1 -> "##" (a chapter)
            lines.append("#" * min(level, 6) + " " + text)
        else:
            lines.append(text)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def read_script(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".docx":
        return read_docx(path)
    if ext in (".txt", ".md"):
        return read_text_file(path)
    raise ValueError("scripts must be .docx, .txt or .md")


# Same rules as the voice app's pipeline.parse_script, so the dialog shows exactly what
# will be spoken (that module can't be imported here: it pulls in numpy/librosa).
_STAMP = re.compile(r"^\s*\[(\d+):(\d{2})\]\s*(.*)$")
_MDHDR = re.compile(r"^\s*#{1,6}\s+(.*)$")
_HDRSTAMP = re.compile(r"^(\d+):(\d{2})\s*[-–—:]*\s*(.*)$")
_NOT_NARRATED = re.compile(r"not narrated|production note|b.?roll|shot list|fact.?check|"
                           r"thumbnail|title option|sponsor|references?$|sources?$", re.I)
_METALINE = re.compile(r"^\s*\*\*[^*]{1,40}:\*\*")


def sections(text: str) -> list[dict]:
    raw, cur = [], None
    for line in text.splitlines():
        m, h = _STAMP.match(line), _MDHDR.match(line)
        if m or h:
            if cur:
                raw.append(cur)
            title = (m.group(3) if m else h.group(1)).strip()
            ts = None if m else _HDRSTAMP.match(title)
            if ts and ts.group(3):
                title = ts.group(3).strip()
            cur = {"title": title, "body": []}
        else:
            cur = cur or {"title": "", "body": []}
            cur["body"].append(line)
    if cur:
        raw.append(cur)
    out = []
    for s in raw:
        if s["title"] and _NOT_NARRATED.search(s["title"]):
            continue
        body = "\n".join(l for l in "\n".join(s["body"]).strip().splitlines()
                         if not l.strip().startswith((">", "|")) and not _METALINE.match(l))
        body = re.sub(r"\*\[[^\]]*\]\*", "", body).replace("—", " - ").strip()
        if body:
            out.append({"title": s["title"], "words": len(body.split())})
    return out


def stats(text: str) -> dict:
    """Numbers for the dialog: only what the voice app will actually speak."""
    secs = sections(text)
    words = sum(s["words"] for s in secs)
    return {"words": words, "sections": len(secs), "minutes_at_155": round(words / 155, 1)}
