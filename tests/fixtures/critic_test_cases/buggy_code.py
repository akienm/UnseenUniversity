"""buggy_code.py — intentionally broken module for critic harness testing.

Planted defects (see fixture_manifest.json for authoritative list):
  B1: read_config() — bare ``except: pass`` swallows all errors silently
  B2: module-level DEFAULT_OUTPUT — hardcoded absolute path
  B3: upload_file() — ``while True`` with no max-retries cap
  B4: parse_tag() — ``raw_tag.strip()`` raises AttributeError when raw_tag is None
  B5: upload_file() — ``return True`` is unreachable; caller gets None on loop exit
"""
import json
import time

DEFAULT_OUTPUT = "/home/user/data/output.csv"  # B2: machine-specific hardcoded path


def read_config(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except:  # B1: bare except swallows ALL errors; caller never knows config failed
        pass
    return {}


def parse_tag(raw_tag) -> str:
    return raw_tag.strip().lower()  # B4: AttributeError if raw_tag is None


def upload_file(url: str, data: bytes) -> bool:
    import urllib.request
    while True:  # B3: no retry cap — infinite loop on persistent network failure
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            urllib.request.urlopen(req)
            return True
        except Exception:
            time.sleep(1)
    # B5: unreachable — function returns None if loop ever exits (it won't)


def process_batch(items: list) -> list:
    results = []
    for item in items:
        tag = parse_tag(item.get("tag"))  # propagates B4: None if "tag" absent
        results.append({"tag": tag, "status": "ok"})
    return results
