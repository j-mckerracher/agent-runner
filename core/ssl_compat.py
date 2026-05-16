"""TLS compatibility helpers for networked SDKs on local macOS/Python setups."""

from __future__ import annotations


def configure_system_ssl() -> None:
    """Prefer the platform trust store when the optional dependency is present."""
    try:
        import truststore
    except ImportError:
        return
    truststore.inject_into_ssl()


__all__ = ["configure_system_ssl"]
