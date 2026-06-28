"""
dicksimnel_docstring_chain — DickSimnel docstring + test pass workflow.

Three steps dispatched to DickSimnel in sequence:
  1. toolloop.py docstrings
  2. shim.py docstrings (after step 1)
  3. worker_listener unit tests (after step 2)

Safe validation target: no CC in the dispatch loop.
"""

WORKFLOW_ID = "dicksimnel-docstring-chain"

STEPS = [
    {
        "id": "step-1-toolloop-docstrings",
        "dispatch": "DickSimnel.0",
        "ticket": "T-dicksimnel-toolloop-docstrings",
        "after": [],
    },
    {
        "id": "step-2-shim-docstrings",
        "dispatch": "DickSimnel.0",
        "ticket": "T-shim-docstring-pass",
        "after": ["step-1-toolloop-docstrings"],
    },
    {
        "id": "step-3-worker-listener-tests",
        "dispatch": "DickSimnel.0",
        "ticket": "T-dicksimnel-worker-listener-tests",
        "after": ["step-2-shim-docstrings"],
    },
]
