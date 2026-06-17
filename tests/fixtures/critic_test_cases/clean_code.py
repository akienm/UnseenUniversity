"""clean_code.py — well-written module for critic harness testing.

No intentional defects. The critic should produce minimal findings;
the harness uses this to verify it does not hallucinate issues.
"""
import json
import logging
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0


def read_config(path: Path) -> dict:
    """Load JSON config from *path*; log and return {} on any failure."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Config load failed (%s): %s", path, exc)
        return {}


def parse_tag(raw_tag: Optional[str]) -> str:
    """Normalise a tag string; return empty string when raw_tag is None/empty."""
    if not raw_tag:
        return ""
    return raw_tag.strip().lower()


def upload_file(url: str, data: bytes) -> bool:
    """POST *data* to *url* with exponential back-off; returns True on success."""
    import urllib.request
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            urllib.request.urlopen(req, timeout=10)
            return True
        except Exception as exc:
            log.warning("Upload attempt %d/%d failed: %s", attempt + 1, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
    log.error("All %d upload attempts failed for %s", MAX_RETRIES, url)
    return False


def process_batch(items: list) -> list:
    """Process a batch; items missing 'tag' get an empty tag, not an exception."""
    results = []
    for item in items:
        raw = item.get("tag")
        tag = parse_tag(raw)
        results.append({"tag": tag, "status": "ok"})
    return results
