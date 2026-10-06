"""P12 reuses the P2 stand-ins (fake CLIs, fake Anthropic API, memory keyring) and P5's scripted agent."""
from tests.p2.conftest import client, fake_cmds, make_client, memory_keyring  # noqa: F401 - fixtures
from tests.p5.conftest import agent  # noqa: F401 - fixture
