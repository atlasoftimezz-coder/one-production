"""Publish a built package where installed copies look for updates.

    .venv\\Scripts\\python tools\\publish_release.py --to "D:\\Releases\\OneProduction" [--version X]

Copies dist\\OneProduction-<v>-win64.zip into the folder and writes latest.json next to it. Point
a friend's copy at that folder (System, Updates, "Update source") - or, if the folder is published
on the web, at its address. Whoever can write to that folder decides what friends install, so use
a place only you control.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def publish(version: str, dist: Path, to: Path) -> Path:
    manifest = json.loads((dist / f"OneProduction-{version}-win64.json").read_text(encoding="utf-8"))
    to.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dist / manifest["file"], to / manifest["file"])
    part = to / "latest.json.part"
    part.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    part.replace(to / "latest.json")              # never a half-written latest.json
    print(f"  published {manifest['file']} ({manifest['size'] / 1e6:.1f} MB) -> {to}")
    return to / "latest.json"


def main() -> None:
    sys.path.insert(0, str(ROOT))
    from app.version import __version__
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", required=True)
    ap.add_argument("--version", default=__version__)
    ap.add_argument("--dist", default=str(ROOT / "dist"))
    a = ap.parse_args()
    publish(a.version, Path(a.dist), Path(a.to))


if __name__ == "__main__":
    main()
