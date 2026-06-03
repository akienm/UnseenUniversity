"""
dispatch.py — Thin re-exports for backward compatibility.

Dispatch logic lives in daemon.py. This module re-exports the two functions
so any code that imported from devices.granny.dispatch still works.
"""

from devices.granny.daemon import _dispatch_cc0 as cc0_dispatch_fn
from devices.granny.daemon import _dispatch_dicksimnel as dicksimnel_dispatch_fn

__all__ = ["cc0_dispatch_fn", "dicksimnel_dispatch_fn"]
