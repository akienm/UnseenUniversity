"""DS.0's cc_queue.py path must resolve to the repo root, not the package dir.

Reorg fallout: the single-package collapse (a5f4dab0) moved device.py from
devices/dicksimnel/ to unseen_university/devices/dicksimnel/ — one level deeper — so
Path(__file__).parents[2] regressed from the repo root to the `unseen_university/`
package dir, making _CC_QUEUE point at a non-existent unseen_university/devlab/... and
DS.0 unable to fetch ANY ticket. Surfaced by the first live DS.0-on-Hex build. Fix uses
the canonical uu_root(), immune to file depth.
"""
from __future__ import annotations

from pathlib import Path

from unseen_university._uu_root import uu_root
from unseen_university.devices.dicksimnel import device


def test_cc_queue_path_resolves_to_repo_root_and_exists():
    assert device._CC_QUEUE == Path(uu_root()) / "devlab" / "claudecode" / "cc_queue.py"
    assert device._CC_QUEUE.exists()


def test_cc_queue_path_is_not_under_the_package_dir():
    # The regressed path was <root>/unseen_university/devlab/... — must never be that.
    assert "unseen_university/devlab" not in str(device._CC_QUEUE)
