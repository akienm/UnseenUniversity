"""python_diagnostic_base_class — merged logging + perf + instance-naming base."""

from .base import DiagnosticBase, prune_json_logs
from .perf import Stopwatch
from .tagged_logger import TaggedLogger

__all__ = ["DiagnosticBase", "Stopwatch", "TaggedLogger", "prune_json_logs"]
__version__ = "0.1.0"
