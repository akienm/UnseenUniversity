"""Distributional monitoring for the intent extractor's output.

WHY THIS EXISTS — and why a record-level check could not have replaced it.

The extractor was ~97% broken for a month (2,435 of 2,504 predictions were the
`except` block's fallback) and NOTHING caught it. Not because the monitoring was
weak, but because every individual record was WELL-FORMED. `intent='unknown'` is a
perfectly legal, perfectly shaped value — it is what a well-behaved CP1 device
returns when it honestly doesn't know. Shape-injectivity (the `provenance_class`
column) fixes that GOING FORWARD, but it only catches the failures you can
ENUMERATE, and it would not have caught this one in real time.

What screamed was the DISTRIBUTION: 2,435 identical outputs, and ZERO genuine
refusals, ever.

  A WORKING CP1 DEVICE REFUSES *SOMETIMES*.
  A VIRTUE FIRING 97% OF THE TIME IS AS IMPOSSIBLE AS ONE FIRING NEVER.
  BOTH EXTREMES MUST PAGE SOMEONE.

A degenerate distribution is the only signal a perfectly-shaped lie still emits.
So: any device whose output is a VIRTUE (a refusal, a pass, an "all clear") gets
its output distribution monitored. That is the rule this module implements.

See R-feedback-is-unconditional-silence-is-never-success (amended 2026-07-13) and
T-intent-extractor-crash-masquerades-as-refusal.
"""

from __future__ import annotations

import logging

from unseen_university.system_alarms import raise_alarm

log = logging.getLogger(__name__)

# A single output value holding more than this share of a window is degenerate.
# 0.90 is deliberately loose: the failure it exists to catch sat at 0.97, and a
# tight threshold on a noisy signal trains people to ignore the alarm.
DEGENERATE_SHARE = 0.90

# Below this many samples the share is meaningless (3 identical answers out of 3 is
# a Tuesday, not an outage). The monitor stays SILENT rather than guessing — and
# reports `fired=False, reason='insufficient-samples'`, which is NOT the same as
# 'healthy'. An absent check must never read as a passing one.
MIN_SAMPLES = 20


def check_output_distribution(
    store,
    domain: str,
    window: int = 100,
    threshold: float = DEGENERATE_SHARE,
    min_samples: int = MIN_SAMPLES,
) -> dict:
    """Is this device's recent output degenerate? Raise an alarm if so.

    Returns the verdict — ``{fired, reason, samples, distinct, top_value, top_share}``
    — AND raises a system alarm when it fires. The return value is what makes the
    check testable; the alarm is what makes it MATTER. A check that only returns is
    a check nobody reads, which is the same as no check at all.
    """
    dist = store.output_distribution(domain, window=window)
    samples = dist.get("samples", 0)

    if samples < min_samples:
        # Not healthy — UNDETERMINED. The distinction is the whole point of this file.
        return {**dist, "fired": False, "reason": "insufficient-samples"}

    top_share = dist.get("top_share", 0.0)
    if top_share < threshold:
        return {**dist, "fired": False, "reason": "ok"}

    top_value = dist.get("top_value")
    message = (
        f"intent extractor output is DEGENERATE for domain={domain!r}: "
        f"{top_share:.0%} of the last {samples} predictions are the identical value "
        f"{top_value!r} ({dist.get('distinct')} distinct values in the window). "
        f"A device that always says the same thing is not answering — it is stuck, "
        f"and if that value is a virtue (a refusal, a pass) it is a failure wearing "
        f"the virtue's clothes."
    )
    raise_alarm(
        signature=f"degenerate-output:intent:{domain}",
        caller="unseen_university.devices.intent.distribution",
        message=message,
        level="ERROR",
    )
    return {**dist, "fired": True, "reason": "degenerate-output"}
