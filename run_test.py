"""Script to run the inference test."""

import sys
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

# Add tests directory to path
sys.path.insert(0, os.path.join(os.getcwd(), "tests"))

from tests.test_inference import test_inference_basic, test_inference_with_list

try:
    test_inference_basic()
    print("PASSED: test_inference_basic")
except AssertionError as e:
    print(f"FAILED: test_inference_basic - {e}")
    sys.exit(1)

try:
    test_inference_with_list()
    print("PASSED: test_inference_with_list")
except AssertionError as e:
    print(f"FAILED: test_inference_with_list - {e}")
    sys.exit(1)

print("\nAll tests passed!")
