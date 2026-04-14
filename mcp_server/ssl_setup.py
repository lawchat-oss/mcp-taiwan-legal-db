"""Shared SSL trust-store bootstrap."""

import truststore

_INJECTED = False


def inject_os_trust_store() -> None:
    """Patch Python SSL once so http clients use the OS trust store."""
    global _INJECTED
    if _INJECTED:
        return
    truststore.inject_into_ssl()
    _INJECTED = True


inject_os_trust_store()
