"""System trust store injection.

Behind a TLS-intercepting proxy every outbound HTTPS call fails against
certifi's bundle, so this switch is the difference between a working service and
one that cannot reach Gemini at all.
"""

from __future__ import annotations

import ssl

import pytest

from app.config import Settings
from app.tls import configure_tls


def test_disabled_leaves_ssl_untouched() -> None:
    original = ssl.SSLContext

    assert configure_tls(use_system_trust_store=False) is False
    assert ssl.SSLContext is original


def test_enabled_injects_the_system_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """`inject_into_ssl` patches the ssl module globally, so it is stubbed here.

    Letting the real call run would change TLS behaviour for every test that
    follows it in the same process.
    """
    called = False

    def _fake_inject() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("truststore.inject_into_ssl", _fake_inject)

    assert configure_tls(use_system_trust_store=True) is True
    assert called is True


def test_it_is_on_by_default() -> None:
    """Off by default would mean every corporate-network install fails first run."""
    assert Settings().use_system_trust_store is True


def test_it_can_be_disabled_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HRDOC_USE_SYSTEM_TRUST_STORE", "false")

    assert Settings().use_system_trust_store is False
