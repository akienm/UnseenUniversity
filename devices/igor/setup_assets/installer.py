"""
installer.py — D321 entry point for Igor (UnseenUniversity edition).

Called by the ~/TheIgors/igor bash launcher after venv is ensured.
Runs the Igor restart loop: launches devices.igor.main, handles exit codes,
detects crash loops.

Exit codes from igor.main:
  42  → clean restart (re-read cfg)
  0, 130, 143  → clean exit (no restart)
  other  → crash; restart unless crash loop detected

Crash loop: >= 4 restarts in 60s → halt and require manual intervention.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# UU repo root = parent of devices/igor/setup_assets/
_UU_DIR = Path(__file__).resolve().parent.parent.parent.parent

_CRASH_WINDOW_SECS = 60
_CRASH_LOOP_THRESHOLD = 4


def _load_cfg() -> None:
    """Hydrate os.environ from igor.switches.cfg + igor.cfg files (best effort)."""
    try:
        runtime_root = Path(
            os.environ.get("IGOR_RUNTIME_ROOT", Path.home() / ".TheIgors")
        )
        instance_id = os.environ.get("IGOR_INSTANCE_ID", "Igor-wild-0001")
        instance_dir = runtime_root / instance_id
        cfg_order = [
            runtime_root / "swarm" / "swarm.cfg",
            instance_dir / "igor.cfg",
            instance_dir / "igor.models.cfg",
            instance_dir / "igor.switches.cfg",
            instance_dir / "igor.credentials.cfg",
        ]
        for p in cfg_order:
            if not p.exists():
                continue
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.split("#")[0].strip()
                if "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception as e:
        print(f"[installer] cfg load warning: {e}", file=sys.stderr)


def restart_loop(igor_args: list[str]) -> None:
    """Main Igor restart loop."""
    runtime_root = Path(os.environ.get("IGOR_RUNTIME_ROOT", Path.home() / ".TheIgors"))
    instance_id = os.environ.get("IGOR_INSTANCE_ID", "Igor-wild-0001")
    instance_dir = runtime_root / instance_id
    instance_dir.mkdir(parents=True, exist_ok=True)
    restart_ts_file = instance_dir / "restart_timestamps.txt"

    launch_env = os.environ.copy()
    existing_pp = launch_env.get("PYTHONPATH", "")
    launch_env["PYTHONPATH"] = str(_UU_DIR) + (":" + existing_pp if existing_pp else "")

    while True:
        _load_cfg()

        # Crash loop detection
        now = time.time()
        timestamps: list[float] = []
        if restart_ts_file.exists():
            for line in restart_ts_file.read_text().splitlines():
                try:
                    t = float(line.strip())
                    if now - t < _CRASH_WINDOW_SECS:
                        timestamps.append(t)
                except ValueError:
                    pass
        timestamps.append(now)
        restart_ts_file.write_text("\n".join(str(t) for t in timestamps) + "\n")

        if len(timestamps) >= _CRASH_LOOP_THRESHOLD:
            msg = (
                f"[installer] CRITICAL: Igor crash loop — "
                f"{len(timestamps)} restarts in {_CRASH_WINDOW_SECS}s. "
                "Halting. Manual intervention required.\n"
            )
            print(msg, file=sys.stderr)
            sys.exit(1)

        cmd = [sys.executable, "-m", "devices.igor.main"] + igor_args
        print(f"[installer] Launching: {' '.join(cmd)}", file=sys.stderr)

        proc = subprocess.Popen(
            cmd,
            cwd=str(_UU_DIR),
            env=launch_env,
        )
        proc.wait()
        exit_code = proc.returncode

        if exit_code == 42:
            print("[installer] Restarting (re-reading cfg)...", file=sys.stderr)
            continue

        if exit_code in (0, 130, 143):
            print(
                f"[installer] Igor exited cleanly (code {exit_code}).", file=sys.stderr
            )
            sys.exit(0)

        print(
            f"[installer] Igor exited with code {exit_code} — restarting...",
            file=sys.stderr,
        )
        time.sleep(1)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Igor launcher (UnseenUniversity)")
    parser.add_argument("--id", help="Instance ID")
    parser.add_argument("--host", help="Host label for auto-generated ID")
    args, extra = parser.parse_known_args(argv)

    igor_args: list[str] = []
    if args.id:
        os.environ["IGOR_INSTANCE_ID"] = args.id
        igor_args += ["--id", args.id]
    if args.host:
        igor_args += ["--host", args.host]
    igor_args += extra

    restart_loop(igor_args)


if __name__ == "__main__":
    main()
