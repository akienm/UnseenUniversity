"""
Prefrontal Cortex — executive judgment functions.

The old reason() delegate (which instantiated a reasoner directly) is gone:
igor reasons through the Inference Proxy via the gateway (reason()/call()), not
a per-call reasoner object (T-inf-reroute-C). What remains here are the judgment
functions (assess_valence, measure_friction, calculate_roi), which live in
judgments.py (#74) and are re-exported here for backward compatibility.
"""

from .judgments import (  # noqa: F401  (re-export)
    assess_valence,
    measure_friction,
    calculate_roi,
    _log_judgment,
    _embed_anchors,
    _POSITIVE_ANCHORS,
    _NEGATIVE_ANCHORS,
)
