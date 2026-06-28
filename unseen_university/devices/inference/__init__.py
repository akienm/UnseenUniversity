"""
Inference device — intelligent model and provider selection.

Live stack:
- InferenceDevice: rack device owning LLM inference dispatch
- RulesEngine: routing policy mapping task_class → (Source, ModelSpec)
- sources.py: provider abstraction for the inference mini-rack
- shim.py: lifecycle management for the inference backend

Public interface:

    from unseen_university.devices.inference import InferenceDevice, RulesEngine
    from unseen_university.devices.inference.shim import InferenceRequest, InferenceResponse
"""

from .device import InferenceDevice
from .rules_engine import RulesEngine, RoutingRule, RoutingDecision

__all__ = [
    "InferenceDevice",
    "RulesEngine",
    "RoutingRule",
    "RoutingDecision",
]
