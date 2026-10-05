"""P10 reuses the P2 / P5 stand-ins (fake CLIs, scripted Agent SDK, fake Codex) and adds a
stand-in yt-dlp (fakes/fake_ytdlp.py, switched on with ONEPROD_YTDLP)."""
from tests.p2.conftest import client, fake_cmds, make_client, memory_keyring  # noqa: F401 - fixtures
from tests.p5.conftest import agent  # noqa: F401 - fixture
