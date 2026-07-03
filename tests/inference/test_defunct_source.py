"""T-inference-mark-anthropic-defunct: the anthropic source is explicitly DEFUNCT.

Akien moved to a 5x Max plan (browser auth) — no paid Anthropic API key, zero balance.
The source must be registered-but-never-selected (VISIBLE in the source info as OFF), and a
`defunct` source must stay off even if a stale key or live connectivity would otherwise make
it available — the policy wins over the ping. This guards the cost-optimizing router from
ever selecting/billing the dead account.
"""
from __future__ import annotations

from unseen_university.devices.inference.sources import (
    AnthropicSource,
    Source,
    SourceRegistry,
    default_registry,
)


class _AlwaysUpSource(Source):
    """A source whose ping always succeeds — isolates the defunct flag from connectivity."""

    def ping(self) -> bool:
        return True


def test_defunct_overrides_ping():
    live = _AlwaysUpSource(name="live")
    dead = _AlwaysUpSource(name="dead", defunct=True, defunct_reason="zero balance")

    # A live source pings up.
    assert live.check_and_update() is True
    assert live.available is True

    # A defunct source starts off and check_and_update() never revives it — even though its
    # ping() returns True. This is the whole point: policy beats connectivity.
    assert dead.available is False
    assert dead.check_and_update() is False
    assert dead.available is False


def test_registry_excludes_defunct_from_available():
    live = _AlwaysUpSource(name="live")
    dead = _AlwaysUpSource(name="dead", defunct=True)
    reg = SourceRegistry()
    reg.register(live)
    reg.register(dead)
    # Visible in the full source info...
    assert {s.name for s in reg.all()} == {"live", "dead"}
    # ...but never offered to the router.
    assert {s.name for s in reg.all_available()} == {"live"}


def test_default_registry_registers_anthropic_defunct():
    reg = default_registry()
    anthropic = reg.get("anthropic")
    # Registered (VISIBLE in source info), NOT silently absent.
    assert anthropic is not None
    assert anthropic.defunct is True
    assert anthropic.available is False
    assert anthropic.defunct_reason  # non-empty reason
    # The router never selects it.
    assert "anthropic" not in {s.name for s in reg.all_available()}


def test_anthropic_stays_defunct_even_with_a_stale_key(monkeypatch):
    # A stale key in the env must NOT revive the defunct source.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-stale-should-be-ignored")
    src = AnthropicSource()
    assert src.defunct is True
    assert src.available is False
    assert src.ping() is False
    assert src.check_and_update() is False
