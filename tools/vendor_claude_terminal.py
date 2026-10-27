"""Fetch pinned, integrity-checked xterm assets. The app serves these locally, never from a CDN."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile
from pathlib import Path

import httpx

PACKAGES = {
    "@xterm/xterm": ("6.0.0", {"lib/xterm.js": "xterm.js", "css/xterm.css": "xterm.css", "LICENSE": "LICENSE-xterm"}),
    "@xterm/addon-fit": ("0.11.0", {"lib/addon-fit.js": "addon-fit.js", "LICENSE": "LICENSE-addon-fit"}),
}


def main():
    target = Path(__file__).resolve().parents[1] / "web" / "vendor" / "xterm"
    target.mkdir(parents=True, exist_ok=True)
    manifest = {}
    with httpx.Client(timeout=40, follow_redirects=True) as client:
        for name, (version, files) in PACKAGES.items():
            meta = client.get(f"https://registry.npmjs.org/{name}/{version}")
            meta.raise_for_status()
            dist = meta.json()["dist"]
            response = client.get(dist["tarball"])
            response.raise_for_status()
            blob = response.content
            integrity = "sha512-" + base64.b64encode(hashlib.sha512(blob).digest()).decode()
            if integrity != dist["integrity"]:
                raise ValueError(f"Integrity mismatch: {name}")
            hashes = {}
            with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
                for source, dest in files.items():
                    data = archive.extractfile("package/" + source).read()
                    (target / dest).write_bytes(data)
                    hashes[dest] = hashlib.sha256(data).hexdigest()
            manifest[name] = {"version": version, "integrity": integrity, "sha256": hashes}
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("Vendored xterm 6.0.0 and addon-fit 0.11.0 (integrity checked).")


if __name__ == "__main__":
    main()
