#!/usr/bin/env python3
"""uu_device — `uu device <dev> <verb> [args]` dispatch (D-skills-two-products).

The two-products split scoped to the DEVICE boundary. Each device carries two
folders so its surface travels with it (devices/ is one-subdir-per-device,
independently deployable):

  devices/<dev>/bin/    — zero-inference executor scripts (the view layer)
  devices/<dev>/skills/ — reasoning-bearing skills CC executes (workflow layer)

The bare CLI (`uu device`) serves bin/ ONLY: a terminal can run an executor but
not a reasoning skill (that needs CC). A skills/-only verb errors with a pointer
to `/device <dev> <verb>` — the CC-facing shim that resolves skills/ then bin/.
The caller gives ONE verb; the dispatcher resolves it. The caller never has to
know "skill" vs "command".

UNIQUE-NAME RULE: a verb may live in bin/ OR skills/, never both. A name present
in both is a LOAD-time error (no silent precedence) — caught here before any
verb runs, so an ambiguous device surface fails loudly instead of guessing.

Devices root resolves from UU_DEVICES_ROOT (test override) else $UU_ROOT/devices.
Zero inference; runs in a bare shell.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _devices_root() -> Path:
    env = os.environ.get("UU_DEVICES_ROOT")
    if env:
        return Path(env)
    uu_root = os.environ.get("UU_ROOT") or str(Path(__file__).resolve().parents[2])
    return Path(uu_root) / "devices"


def _err(msg: str) -> int:
    print(f"uu device: {msg}", file=sys.stderr)
    return 1


def _bin_verbs(dev_dir: Path) -> dict:
    """Executable scripts in bin/ — name -> path."""
    d = dev_dir / "bin"
    if not d.is_dir():
        return {}
    return {p.name: p for p in d.iterdir() if p.is_file() and os.access(p, os.X_OK)}


def _skill_verbs(dev_dir: Path) -> set:
    """Skill dirs in skills/ (a <verb>/SKILL.md) — names only."""
    d = dev_dir / "skills"
    if not d.is_dir():
        return set()
    return {p.name for p in d.iterdir() if p.is_dir() and (p / "SKILL.md").exists()}


def main(argv: list[str]) -> int:
    args = argv[1:]
    if not args:
        return _err("usage: uu device <dev> <verb> [args]")

    dev = args[0]
    dev_dir = _devices_root() / dev
    if not dev_dir.is_dir():
        return _err(f"unknown device '{dev}' (no {dev_dir})")

    bins = _bin_verbs(dev_dir)
    skills = _skill_verbs(dev_dir)

    # Unique-name load check — runs before any dispatch, so a device whose bin/
    # and skills/ both define a verb fails loudly rather than picking one.
    clash = sorted(set(bins) & skills)
    if clash:
        return _err(
            f"verb name(s) defined in BOTH bin/ and skills/ for '{dev}': "
            f"{', '.join(clash)} — names must be unique per device (no silent precedence)"
        )

    avail = sorted(set(bins) | skills)
    if len(args) < 2:
        return _err(f"usage: uu device {dev} <verb> [args] — verbs: {', '.join(avail) or '(none)'}")

    verb, rest = args[1], args[2:]
    if verb in bins:
        script = bins[verb]
        os.execv(str(script), [str(script), *rest])  # zero-inference executor; replaces process

    if verb in skills:
        return _err(
            f"'{verb}' is a reasoning skill — run `/device {dev} {verb}` (it needs CC), "
            f"not the bare `uu device` CLI"
        )

    return _err(f"unknown verb '{verb}' for device '{dev}' — verbs: {', '.join(avail) or '(none)'}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
