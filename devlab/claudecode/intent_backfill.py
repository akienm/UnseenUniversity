#!/usr/bin/env python3
"""
intent_backfill.py — One-time backfill of ticket intentions via IntentExtractorDevice.

For tickets missing intention field: call intent_predict() from description.
For closed tickets with result: call intent_validate() with result as outcome.

Gracefully degrades when device is unavailable.
Idempotent: multiple runs are safe (skips tickets already decorated).
"""

import json
import sys
from pathlib import Path

# Add devlab/claudecode to path for cc_queue access
sys.path.insert(0, str(Path(__file__).parent))
import cc_queue


def main():
    """Backfill existing tickets with intent predictions and validations."""
    try:
        from unseen_university.devices.intent.tools import intent_predict, intent_validate
    except ImportError:
        print("❌ Intent extractor device not available — backfill cannot proceed.")
        return 1

    tasks = cc_queue._load()
    if not tasks:
        print("No tickets found.")
        return 0

    print(f"🔄 Backfill: {len(tasks)} tickets total\n")

    predicted = 0
    validated = 0
    skipped = 0
    errors = 0

    for task in tasks:
        ticket_id = task.get("id")
        if not ticket_id:
            continue

        # Skip if intention or inferred_intention already present
        if task.get("intention") or task.get("inferred_intention"):
            skipped += 1
            continue

        try:
            description = task.get("description", "")[:500]
            if not description:
                skipped += 1
                continue

            # Predict intention from description
            result = intent_predict(context=description, domain="coding")
            if not result:
                print(f"  ⚠️  {ticket_id}: prediction failed")
                errors += 1
                continue

            predicted_intent = result.get("intent")
            prediction_id = result.get("prediction_id")
            confidence = result.get("confidence", 0.0)

            task["inferred_intention"] = predicted_intent
            task["inferred_intention_id"] = prediction_id
            predicted += 1
            print(f"  ✓ {ticket_id}: predicted '{predicted_intent}' (confidence: {confidence:.2f})")

            # If ticket is closed with a result, validate the prediction
            if task.get("status") in ("closed", "done") and task.get("result"):
                result_text = str(task.get("result"))[:200]
                try:
                    intent_validate(actual_outcome=result_text, prediction_id=prediction_id)
                    validated += 1
                    print(f"      → validated against result")
                except Exception as exc:
                    print(f"      ⚠️  validation error: {exc}")

        except Exception as exc:
            print(f"  ❌ {ticket_id}: {exc}")
            errors += 1

    # Save updated tasks
    cc_queue._save(tasks)

    print(f"\n📊 Backfill complete:")
    print(f"  • Predicted: {predicted}")
    print(f"  • Validated: {validated}")
    print(f"  • Skipped (already decorated): {skipped}")
    print(f"  • Errors: {errors}")

    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
