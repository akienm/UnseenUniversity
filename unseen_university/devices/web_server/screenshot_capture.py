"""
screenshot_capture.py — Headless screenshot of device fascia pages.

Captures a PNG of each known device's /feeds/<id> page and stores it at:
  <runtime>/datacenter_logs/web_server/screenshots/<device_id>.png

Requires google-chrome (or chromium) installed. Gracefully skips on failure.
Called by Nanny Ogg's periodic screenshot sweep, or run standalone:

    python3 -m unseen_university.devices.web_server.screenshot_capture [--device DEVICE_ID]

D-comms-fascia-ux-2026-06-09
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


_DEFAULT_WEB_PORT = int(os.environ.get("ADC_WEB_PORT", "8080"))
_DEFAULT_BASE_URL = f"http://127.0.0.1:{_DEFAULT_WEB_PORT}"

_SCREENSHOT_DIR: Path = (
    Path(
        os.environ.get("ADC_RUNTIME_ROOT")
        or os.environ.get("IGOR_RUNTIME_ROOT")
        or Path.home() / ".unseen_university"
    )
    / "datacenter_logs"
    / "web_server"
    / "screenshots"
)


def _chrome_bin() -> str | None:
    """Return path to Chrome/Chromium binary, or None if not found."""
    for candidate in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "/usr/bin/google-chrome",
    ):
        result = subprocess.run(
            ["which", candidate], capture_output=True, text=True
        )
        if result.returncode == 0:
            return result.stdout.strip()
    return None


def capture_device(
    device_id: str,
    base_url: str = _DEFAULT_BASE_URL,
    out_dir: Path | None = None,
    timeout: int = 15,
) -> Path | None:
    """Take a screenshot of /feeds/<device_id> and save as PNG.

    Returns the saved path on success, None on failure.
    """
    chrome = _chrome_bin()
    if chrome is None:
        return None

    out_dir = out_dir or _SCREENSHOT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / f"{device_id}.png"
    url = f"{base_url}/feeds/{device_id}"

    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        f"--screenshot={out_path}",
        "--window-size=1280,800",
        "--hide-scrollbars",
        url,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0 and out_path.exists():
            return out_path
    except (subprocess.TimeoutExpired, Exception):
        pass

    return None


def capture_all(
    base_url: str = _DEFAULT_BASE_URL,
    out_dir: Path | None = None,
) -> dict[str, bool]:
    """Capture screenshots for all registered devices. Returns {device_id: success}."""
    results: dict[str, bool] = {}

    try:
        import urllib.request
        import json as _json

        with urllib.request.urlopen(f"{base_url}/api/device/list", timeout=5) as resp:
            data = _json.loads(resp.read())
        device_ids = [d["id"] for d in data.get("devices", []) if d.get("id")]
    except Exception:
        return results

    for device_id in device_ids:
        path = capture_device(device_id, base_url=base_url, out_dir=out_dir)
        results[device_id] = path is not None

    return results


def screenshot_path(device_id: str, out_dir: Path | None = None) -> Path:
    """Return the expected path for a device's cached screenshot."""
    return (out_dir or _SCREENSHOT_DIR) / f"{device_id}.png"


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Capture device fascia screenshots")
    parser.add_argument("--device", default=None, help="Single device ID (default: all)")
    parser.add_argument("--base-url", default=_DEFAULT_BASE_URL)
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args()

    out = Path(args.out_dir) if args.out_dir else None

    if args.device:
        path = capture_device(args.device, base_url=args.base_url, out_dir=out)
        print(f"{args.device}: {'ok → ' + str(path) if path else 'failed'}")
    else:
        results = capture_all(base_url=args.base_url, out_dir=out)
        for dev_id, ok in results.items():
            print(f"{dev_id}: {'ok' if ok else 'failed'}")
