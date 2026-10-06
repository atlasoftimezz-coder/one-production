"""P12 terms check (plan R8, D7). AUTO: subscription chaining stays disabled while the recorded terms
mark a provider "not_allowed" - even when the owner switches it on."""
import json

from app import terms
from app.accounts import switch_allowed


def a(id_, provider):
    return {"id": id_, "provider": provider}


def test_recorded_verdicts_match_the_terms_check_document():
    data = terms.load()
    assert data["checked"] == "2026-10-06"
    assert terms.verdict("claude") == "not_allowed" and terms.verdict("codex") == "not_allowed"
    assert terms.verdict("anthropic_api") == "allowed" and terms.verdict("nobody") == "unclear"
    doc = (terms.FILE.parents[1] / "docs" / "terms-check.md").read_text(encoding="utf-8")
    assert "2026-10-06" in doc and "not allowed" in doc.lower()


def test_chaining_stays_off_while_the_terms_say_no_even_if_the_owner_wants_it():
    for owner in (False, True):
        assert not switch_allowed(a("c1", "claude"), a("c2", "claude"), owner)
        assert not switch_allowed(a("x1", "codex"), a("x2", "codex"), owner)
        assert switch_allowed(a("c1", "claude"), a("x1", "codex"), owner)            # another service: fine
        assert switch_allowed(a("c1", "claude"), a("k1", "anthropic_api"), owner)    # D7 default order: fine
    assert not switch_allowed(a("c1", "claude"), a("c1", "claude"), True)            # never "to itself"


def test_both_the_owner_and_the_terms_must_allow_it():
    yes = {"providers": {"claude": {"chaining": "allowed"}}}
    unclear = {"providers": {"claude": {"chaining": "unclear"}}}
    assert terms.chaining_allowed("claude", True, yes) and not terms.chaining_allowed("claude", False, yes)
    assert not terms.chaining_allowed("claude", True, unclear)
    assert not terms.chaining_allowed("claude", True, {"providers": {}})            # no record = no


def test_api_shows_the_verdicts_and_the_owner_switch_cannot_override_them(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ONEPROD_SETTINGS_FILE", str(tmp_path / "settings.json"))
    s = client.get("/api/terms").json()
    assert s["checked"] == "2026-10-06" and s["owner_allows_chaining"] is False
    assert s["providers"]["claude"]["chaining"] == "not_allowed" and s["providers"]["claude"]["chaining_effective"] is False
    s = client.patch("/api/terms", json={"allow_chaining": True}).json()
    assert s["owner_allows_chaining"] is True and s["providers"]["claude"]["chaining_effective"] is False
    assert s["providers"]["anthropic_api"]["chaining_effective"] is True
    assert json.loads((tmp_path / "settings.json").read_text())["allow_chaining"] is True
    usage = client.get("/api/usage?range=today").json()
    assert usage["same_service_switching"] is False and usage["terms"]["checked"] == "2026-10-06"
