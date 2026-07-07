"""Guard for BuilderCloseMixin — the ONE shared branch-builder close.

Both AiderDevice and DickSimnelDevice inherit this instead of hand-rolling close
(homogeneity). The load-bearing invariant: a branch-builder close goes through the
proof-on-close gate the honest way — shipped-unproven WITH the missing-lever reason —
never a bare `close` (which the gate refuses; DickSimnel's old plain close bounced
every success to CC). A hollow mixin that plain-closes fails
`test_close_passes_shipped_unproven_with_lever`.
"""

from unseen_university.devices._builder_close import BuilderCloseMixin


class _FakeBuilder(BuilderCloseMixin):
    """Minimal host: records cc_queue calls, returns canned close/show results."""

    def __init__(self, close_result, show_status=None):
        self.calls = []
        self._close_result = close_result
        self._show_status = show_status

    def _run_queue_cmd(self, *args):
        self.calls.append(args)
        if args[0] == "close":
            return self._close_result
        if args[0] == "show":
            return {"status": self._show_status} if self._show_status else None
        return None


def _close_call(dev):
    return next(c for c in dev.calls if c[0] == "close")


def test_close_passes_shipped_unproven_with_lever():
    dev = _FakeBuilder(close_result={"status": "closed"})
    ok = dev._builder_close("T-x", note="did the thing", missing_lever="proof emits at merge")
    assert ok is True
    call = _close_call(dev)
    assert "--shipped-unproven" in call, "builder close must go through the gate honestly"
    assert any("proof emits at merge" in str(a) for a in call), "must name the missing lever"


def test_double_close_is_success():
    # close() returns None when already closed; show confirms -> success, not failure.
    dev = _FakeBuilder(close_result=None, show_status="closed")
    assert dev._builder_close("T-x", note="n", missing_lever="l") is True


def test_genuine_close_failure_returns_false():
    # close None AND show shows not-closed -> real failure, caller must escalate.
    dev = _FakeBuilder(close_result=None, show_status=None)
    assert dev._builder_close("T-x", note="n", missing_lever="l") is False


def test_lever_and_note_are_truncated_not_dropped():
    dev = _FakeBuilder(close_result={"status": "closed"})
    dev._builder_close("T-x", note="N" * 5000, missing_lever="L" * 5000)
    call = _close_call(dev)
    # note capped at 1500, lever at 400 — present but bounded
    assert any(a == "N" * 1500 for a in call)
    assert any(a == "L" * 400 for a in call)
