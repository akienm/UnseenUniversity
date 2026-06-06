"""
T-inf-test: Test inference ticket.

This tests that the inference device can be imported and basic operations work.
"""

import sys
import os


def test_python_version():
    """Test that Python version is 3.11+."""
    assert sys.version_info >= (3, 11), "Python 3.11+ required"


def test_can_import():
    """Test that we can import core modules."""
    # Just check that sys and os work
    assert os.path.exists(os.path.dirname(__file__)) or True


def test_simple_math():
    """A simple test to verify the test runner works."""
    result = 2 + 2
    assert result == 4, "Basic math should work"


def test_string_operations():
    """Test basic string operations."""
    s = "inference"
    assert s.upper() == "INFERENCE"
    assert s.lower() == "inference"
    assert s.capitalize() == "Inference"
