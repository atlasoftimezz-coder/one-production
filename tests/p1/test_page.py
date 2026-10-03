"""The page must reach the browser as clean UTF-8.

Regression for a real bug: an editor round-trip saved index.html in the wrong encoding and
the page showed "v0.1.0 Â·" instead of "v0.1.0 ·". Mojibake like that is invisible to
functional tests, so check for it directly.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# UTF-8 text decoded as Windows-1252 produces these pairs.
MOJIBAKE = ("Ã", "Â·", "Â ", "â€", "Ã©", "ï»¿")


def test_text_files_are_valid_utf8_without_bom_or_mojibake():
    # web\ is what the user sees; docs\ and the config files get hand-edited and have
    # been mangled the same way before.
    candidates = [*(ROOT / "web").rglob("*"), *(ROOT / "docs").rglob("*"),
                  ROOT / "pyproject.toml", ROOT / "README.md"]
    for f in candidates:
        if f.is_file() and f.suffix in {".html", ".js", ".css", ".md", ".toml", ".txt"} \
                and not f.name.endswith("-auto.md"):
            raw = f.read_bytes()
            assert not raw.startswith(b"\xef\xbb\xbf"), f"{f.name} starts with a BOM"
            text = raw.decode("utf-8")          # raises if not valid UTF-8
            for bad in MOJIBAKE:
                assert bad not in text, f"{f.name} contains mojibake {bad!r}"


def test_page_is_served_as_utf8(client):
    r = client.get("/")
    assert "charset=utf-8" in r.headers["content-type"].lower()
    assert "·" in r.text and "Â" not in r.text


def test_favicon_is_inline_so_no_404(client):
    assert 'rel="icon"' in client.get("/").text
