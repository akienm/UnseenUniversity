"""
classifier/cli.py — CLI entry point for classifier device.

Commands:
  classify     — call classify() and output BuilderReport JSON
  freshness    — call freshness_check() on a stored report and output updated JSON

Usage:
  python3 -m unseen_university.devices.classifier.cli classify --title "..." --tags "..." --description "..."
  python3 -m unseen_university.devices.classifier.cli freshness --report-json '{"relevant_files": ...}'

Output: JSON to stdout, errors to stderr. Exit 0 on success, 1 on error.
Non-fatal: failures print a minimal empty report and exit 0 so skill callers never block.

D-classifier-device-architecture-2026-06-12 / T-builder-report-at-filing
"""

from __future__ import annotations

import json
import logging
import sys

log = logging.getLogger(__name__)


def _empty_report(task_shape: str = "general") -> dict:
    from datetime import datetime, timezone
    return {
        "relevant_files": [],
        "context_nodes": [],
        "task_shape": task_shape,
        "confidence": 0.0,
        "classifier": "empty",
        "stale": False,
        "warnings": [],
        "ts": datetime.now(timezone.utc).isoformat(),
    }


def cmd_classify(args) -> int:
    """Run classify() and print BuilderReport JSON."""
    try:
        from unseen_university.devices.classifier.device import ClassifierDevice
        task_description = "\n".join(filter(None, [
            args.title or "",
            " ".join(args.tags or []),
            args.description or "",
        ]))
        if not task_description.strip():
            print(json.dumps(_empty_report()), flush=True)
            return 0

        device = ClassifierDevice(llm_fallback=False)
        report = device.classify(task_description, project_id="unseen_university")
        print(json.dumps(report.to_dict()), flush=True)
        return 0
    except Exception as exc:
        log.warning("classifier cli classify failed: %s", exc)
        print(json.dumps(_empty_report()), flush=True)
        return 0  # non-fatal: always exit 0 so ticket filing continues


def cmd_freshness(args) -> int:
    """Run freshness_check() on a serialized report and print updated JSON."""
    try:
        from unseen_university.devices.classifier.device import ClassifierDevice
        from unseen_university.devices.classifier.report import BuilderReport

        raw = args.report_json or "{}"
        report_dict = json.loads(raw)
        report = BuilderReport.from_dict(report_dict)

        device = ClassifierDevice(llm_fallback=False)
        updated = device.freshness_check(report)
        print(json.dumps(updated.to_dict()), flush=True)
        return 0
    except Exception as exc:
        log.warning("classifier cli freshness failed: %s", exc)
        print(json.dumps(_empty_report()), flush=True)
        return 0


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-level", default="WARNING")
    subs = parser.add_subparsers(dest="command")

    p_classify = subs.add_parser("classify", help="Classify a task and return BuilderReport JSON")
    p_classify.add_argument("--title", default="")
    p_classify.add_argument("--tags", nargs="*", default=[])
    p_classify.add_argument("--description", default="")

    p_fresh = subs.add_parser("freshness", help="Check if a BuilderReport is still fresh")
    p_fresh.add_argument("--report-json", default="{}", help="Serialized BuilderReport JSON")

    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.WARNING),
                        format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)

    if args.command == "classify":
        sys.exit(cmd_classify(args))
    elif args.command == "freshness":
        sys.exit(cmd_freshness(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
