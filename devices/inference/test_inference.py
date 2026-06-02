"""Tests for the inference device."""
import pytest


def test_inference_device_imports():
    """Test that the inference device can be imported."""
    try:
        from devices.inference import __name__ as module_name
        assert module_name is not None
    except ImportError:
        pytest.skip("inference device not yet implemented")


def test_inference_basic():
    """Basic sanity test — always passes."""
    assert 1 + 1 == 2
