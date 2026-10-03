"""R6: one redaction function, catching every planted secret format."""
import logging

import pytest

from app.security.redact import RedactingFilter, redact, register_secret

PLANTED = {
    "anthropic-key": "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
    "openai-key": "sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
    "google-key": "AIzaSyA1234567890abcdefghijklmnopqrstu",
    "github-token": "ghp_" + "A1b2C3d4E5" * 4,
    "aws-key": "AKIAIOSFODNN7EXAMPLE",
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N",
}


@pytest.mark.parametrize("kind,secret", PLANTED.items())
def test_each_format_is_redacted(kind, secret):
    out = redact(f"before {secret} after")
    assert secret not in out
    assert "[REDACTED:" in out
    assert out.startswith("before ") and out.endswith(" after")


@pytest.mark.parametrize("line", ['password=hunter2hunter2', 'api_key: "abcdef123456"',
                                  "Authorization: Bearer abcdefghijklmnop123456"])
def test_assignments_and_bearer_are_redacted(line):
    out = redact(line)
    assert "hunter2hunter2" not in out and "abcdef123456" not in out and "abcdefghijklmnop123456" not in out


def test_registered_session_token_is_redacted():
    register_secret("my-session-token-XYZ987654321")
    assert "my-session-token-XYZ987654321" not in redact("url #t=my-session-token-XYZ987654321")


def test_ordinary_text_is_untouched():
    text = "Pripyat was evacuated in 1986. File voice.mp3 is 12.5 MB at -16 LUFS."
    assert redact(text) == text


def test_log_filter_scrubs_records(tmp_path):
    log_file = tmp_path / "app.log"
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.addFilter(RedactingFilter())
    log = logging.getLogger("redact-test")
    log.addHandler(handler); log.setLevel(logging.INFO)
    try:
        log.info("key is %s", PLANTED["anthropic-key"])
    finally:
        handler.close(); log.removeHandler(handler)
    text = log_file.read_text(encoding="utf-8")
    assert PLANTED["anthropic-key"] not in text and "[REDACTED:anthropic-key]" in text
