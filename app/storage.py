"""Projects and their files, stored under data_dir/projects/<id>/.

    project.json   id, name, created
    files/         anything the user uploads, any type
    chat.jsonl     one JSON object per message
"""
from __future__ import annotations

import json
import re
import secrets
import time
from pathlib import Path

_SAFE = re.compile(r"[^A-Za-z0-9._ \-()]+")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}


class NotFound(Exception):
    pass


def safe_filename(name: str) -> str:
    """Keep only the final path component and characters Windows accepts."""
    name = name.replace("\\", "/").split("/")[-1]
    name = _SAFE.sub("_", name).strip(" .")
    if not name or name in {".", ".."}:
        name = "file"
    stem = name.split(".")[0].lower()
    if stem in _RESERVED:
        name = "_" + name
    return name[:180]


class Store:
    def __init__(self, data_dir: Path):
        self.root = data_dir / "projects"
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- projects
    def _dir(self, pid: str) -> Path:
        if not re.fullmatch(r"[a-z0-9\-]{1,64}", pid):
            raise NotFound(pid)
        d = self.root / pid
        if not (d / "project.json").is_file():
            raise NotFound(pid)
        return d

    def list_projects(self) -> list[dict]:
        out = []
        for meta in self.root.glob("*/project.json"):
            try:
                out.append(json.loads(meta.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(out, key=lambda p: p["created"], reverse=True)

    def create_project(self, name: str) -> dict:
        name = (name or "").strip()[:80] or "Untitled"
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "project"
        pid = f"{slug}-{secrets.token_hex(3)}"
        d = self.root / pid
        (d / "files").mkdir(parents=True)
        meta = {"id": pid, "name": name, "created": time.time()}
        (d / "project.json").write_text(json.dumps(meta), encoding="utf-8")
        return meta

    def delete_project(self, pid: str) -> Path:
        """Not erased: moved to data_dir/trash/<id>-<time>, so a mistake can be undone by
        moving the folder back into projects/."""
        src = self._dir(pid)
        trash = self.root.parent / "trash"
        trash.mkdir(exist_ok=True)
        base = f"{pid}-{time.strftime('%Y%m%d-%H%M%S')}"
        dst, n = trash / base, 1
        while dst.exists():                      # two moves within one second
            n += 1
            dst = trash / f"{base}-{n}"
        src.rename(dst)
        return dst

    def project(self, pid: str) -> dict:
        return json.loads((self._dir(pid) / "project.json").read_text(encoding="utf-8"))

    def project_dir(self, pid: str) -> Path:
        return self._dir(pid)

    # ---- files
    def files_dir(self, pid: str) -> Path:
        return self._dir(pid) / "files"

    def unique_path(self, pid: str, filename: str) -> Path:
        folder = self.files_dir(pid)
        name = safe_filename(filename)
        target = folder / name
        stem, dot, ext = name.rpartition(".")
        if not dot:
            stem, ext = name, ""
        n = 1
        while target.exists():
            target = folder / (f"{stem} ({n}).{ext}" if ext else f"{stem} ({n})")
            n += 1
        return target

    def list_files(self, pid: str) -> list[dict]:
        """Every file, also in sub-folders the assistant made (scripts/, competitors/): the name is the
        path inside the project with '/'. Hidden folders and unfinished uploads are left out."""
        folder = self.files_dir(pid)
        out = []
        for f in folder.rglob("*"):
            rel = f.relative_to(folder)
            if not f.is_file() or f.name.endswith(".part") or any(x.startswith(".") for x in rel.parts):
                continue
            out.append({"name": rel.as_posix(), "size": f.stat().st_size, "modified": f.stat().st_mtime})
        return sorted(out, key=lambda x: (x["name"].count("/"), x["name"].lower()))

    def file_path(self, pid: str, name: str) -> Path:
        folder = self.files_dir(pid).resolve()
        parts = [safe_filename(x) for x in name.replace("\\", "/").split("/") if x not in ("", ".")]
        p = folder.joinpath(*parts).resolve() if parts else folder
        if folder not in p.parents or not p.is_file():
            raise NotFound(name)
        return p

    # ---- chat
    def append_message(self, pid: str, role: str, text: str, **meta) -> dict:
        msg = {"role": role, "text": text, "time": time.time(), **meta}
        with (self._dir(pid) / "chat.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(msg) + "\n")
        return msg

    def history(self, pid: str) -> list[dict]:
        f = self._dir(pid) / "chat.jsonl"
        if not f.exists():
            return []
        return [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
