"""
vault/client.py — Public credential accessor for rack devices.

Usage:
    from unseen_university.devices.vault.client import get_credential
    api_key = get_credential("inference", "akien", "OLLAMA_API_KEY")

Returns '' when credential not found, device not authorized, or vault unavailable.
Callers should fall back to env vars / flat files when '' is returned.
"""

from __future__ import annotations


def get_credential(device_id: str, owner: str, key: str) -> str:
    """Fetch a credential from vault, scoped by device_id.

    Args:
        device_id: The calling device's canonical ID (e.g. "inference", "dicksimnel").
        owner: Credential owner (e.g. "akien", "igor").
        key: Credential key name (e.g. "OLLAMA_API_KEY").

    Returns:
        Decrypted credential value, or '' if not found / not authorized / vault down.
    """
    try:
        from unseen_university.devices.vault.store import get_credential as _get
        return _get(device_id=device_id, owner=owner, key=key)
    except Exception:
        return ""
