"""System trust store integration.

Python validates TLS against the `certifi` bundle, which contains public
certificate authorities and nothing else. Corporate networks that terminate and
re-sign TLS -- proxies, filtering appliances, endpoint antivirus -- present a
private CA that certifi has never heard of, so every outbound HTTPS call fails
with CERTIFICATE_VERIFY_FAILED even though the machine itself trusts the issuer.

That CA is already installed in the operating system's own store, which is what
browsers and other native software use. `truststore` redirects Python's TLS
verification there, which fixes the interception case without the usual
workaround of disabling verification.
"""

from __future__ import annotations

from app.logging_config import get_logger

logger = get_logger(__name__)


def configure_tls(*, use_system_trust_store: bool) -> bool:
    """Point Python's TLS verification at the OS trust store.

    Must run before any HTTPS client builds its SSL context; once a context
    exists it keeps the verification settings it was created with.

    Returns whether the injection happened, so startup can report it.
    """
    if not use_system_trust_store:
        return False

    try:
        import truststore
    except ImportError:  # pragma: no cover - dependency is declared, not optional
        logger.warning("tls.truststore_unavailable")
        return False

    truststore.inject_into_ssl()
    logger.info("tls.system_trust_store_enabled")
    return True
