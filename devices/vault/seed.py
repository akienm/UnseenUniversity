"""
vault/seed.py — One-shot seeder: reads akien.credentials.cfg and inserts rows.

Usage:
    python3 devices/vault/seed.py [--dry-run]

Safe to re-run: upserts so existing rows are updated, not duplicated.
Does not delete rows that already exist in vault but not in cfg.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# Default path for akien's credentials — override with AKIEN_CREDS_FILE env var
_DEFAULT_CREDS = Path("~/.unseen_university/akien/akien.credentials.cfg").expanduser()

# Credentials to seed. Values read from cfg; scoped to listed devices.
# Key name in cfg → (owner, canonical key, allowed_devices)
_SEED_MAP: list[tuple[str, str, str, list[str]]] = [
    # (cfg_key, owner, vault_key, allowed_devices)
    ("OLLAMA_API_KEY",        "akien", "OLLAMA_API_KEY",        ["inference", "dicksimnel", "granny"]),
    ("OLLAMA_PRO_API_KEY",    "akien", "OLLAMA_PRO_API_KEY",    ["inference", "dicksimnel", "granny"]),
    ("OPENROUTER_API_KEY",    "akien", "OPENROUTER_API_KEY",    ["inference", "dicksimnel"]),
    ("ANTHROPIC_API_KEY",     "akien", "ANTHROPIC_API_KEY",     ["inference", "dicksimnel"]),
    ("REAL_ANTHROPIC_API_KEY","akien", "REAL_ANTHROPIC_API_KEY",["inference"]),
    ("GOOGLE_STUDIO_API_KEY", "akien", "GOOGLE_STUDIO_API_KEY", ["inference", "dicksimnel"]),
    ("GOOGLE_AI_STUDIO_API_KEY","akien","GOOGLE_AI_STUDIO_API_KEY",["inference", "dicksimnel"]),
    ("GEMINI_API_KEY",        "akien", "GEMINI_API_KEY",        ["inference", "dicksimnel"]),
]


def _read_cfg(path: Path) -> dict[str, str]:
    """Parse KEY=value lines from a credentials file."""
    result = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                result[k.strip()] = v.strip()
    except OSError as exc:
        log.warning("seed: could not read %s: %s", path, exc)
    return result


def seed(creds_path: Path = _DEFAULT_CREDS, dry_run: bool = False) -> int:
    """Seed vault from credentials file. Returns count of rows upserted."""
    import os
    creds_path = Path(os.environ.get("AKIEN_CREDS_FILE", str(creds_path))).expanduser()
    cfg = _read_cfg(creds_path)

    if not cfg:
        log.warning("seed: no credentials found in %s", creds_path)
        return 0

    from devices.vault.store import upsert_credential
    seeded = 0
    for cfg_key, owner, vault_key, allowed_devices in _SEED_MAP:
        value = cfg.get(cfg_key, "").strip()
        if not value:
            log.debug("seed: %s not in cfg — skipping", cfg_key)
            continue
        if dry_run:
            log.info("seed (dry-run): would upsert owner=%r key=%r devices=%r", owner, vault_key, allowed_devices)
        else:
            upsert_credential(owner=owner, key=vault_key, value=value, allowed_devices=allowed_devices)
            log.info("seed: upserted owner=%r key=%r devices=%r", owner, vault_key, allowed_devices)
        seeded += 1

    return seeded


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    dry_run = "--dry-run" in sys.argv
    count = seed(dry_run=dry_run)
    log.info("seed: %d credential(s) %s", count, "would be upserted (dry-run)" if dry_run else "upserted")
    sys.exit(0 if count >= 0 else 1)


if __name__ == "__main__":
    main()
