#!/usr/bin/env python3
"""Self-test all inference sources: ping + hello world."""

import sys
from devices.inference.sources import default_registry

def main():
    """Test all sources in the active registry."""
    registry = default_registry()
    print("=" * 70)
    print("Inference Source Self-Tests")
    print("=" * 70)

    all_ok = True
    for source in registry.all():
        print(f"\n{source.name:20} ", end="")
        success, reason = source.self_test()
        status = "✓" if success else "✗"
        print(f" {status}  {reason}")
        if not success:
            all_ok = False

    print("\n" + "=" * 70)
    if all_ok:
        print("✓ All sources OK")
        return 0
    else:
        print("✗ Some sources failed — see details above")
        return 1

if __name__ == "__main__":
    sys.exit(main())
