"""P9 reuses the stand-ins of earlier phases: P2's fake CLIs and fake Anthropic API (plain chat),
P5's scripted Agent SDK client and fake Codex app-server (agent and team mode)."""
from tests.p2.conftest import client, fake_cmds, make_client, memory_keyring  # noqa: F401 - fixtures
from tests.p5.conftest import agent  # noqa: F401 - fixture
