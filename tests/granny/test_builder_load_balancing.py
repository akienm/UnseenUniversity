"""Red->green proof for builder load-balancing (T-granny-builder-load-balancing).

Two builders (DickSimnel.0, Aider.0) now share the builder/creator role. `_select_builder`
spreads work across AVAILABLE builders instead of the old hard-route to a single one, and
the `Aider` tag opt-in — whose tag collided with aider's own topic tag, causing Granny to
vacuum aider-ABOUT tickets to the builder — is retired from both rule write-sites.

Load-bearing invariant (the ticket's completion criterion): a builder ticket reaches
Aider.0 when DickSimnel.0 is unavailable, and vice versa. A hollow selector that hard-
routes to one builder fails `test_fails_over_to_aider_when_ds_unavailable`.
"""

from unseen_university.devices.granny.daemon import (
    _select_builder,
    _default_config,
    _load_config,
)

_WORKERS = {
    "DickSimnel.0": {"worker_name": "dicksimnel", "one_at_a_time": True},
    "Aider.0": {"worker_name": "aider", "one_at_a_time": True},
    "CC.0": {"worker_name": "claude"},  # master — never a builder
}


def _avail(available):
    return lambda wid: wid in available


def test_fails_over_to_aider_when_ds_unavailable():
    # THE completion criterion: DS down -> the builder ticket reaches Aider.0.
    assert _select_builder(_WORKERS, set(), avail_fn=_avail({"Aider.0"})) == "Aider.0"


def test_fails_over_to_ds_when_aider_unavailable():
    assert _select_builder(_WORKERS, set(), avail_fn=_avail({"DickSimnel.0"})) == "DickSimnel.0"


def test_spreads_batch_across_both_builders_one_per_cycle():
    both = _avail({"Aider.0", "DickSimnel.0"})
    dispatched = set()
    first = _select_builder(_WORKERS, dispatched, avail_fn=both)
    dispatched.add(first)  # one_at_a_time: mark it taken this cycle
    second = _select_builder(_WORKERS, dispatched, avail_fn=both)
    assert {first, second} == {"Aider.0", "DickSimnel.0"}


def test_none_when_no_builder_available():
    # Both down -> None -> caller defers (same as an unavailable single builder).
    assert _select_builder(_WORKERS, set(), avail_fn=_avail(set())) is None


def test_never_selects_a_non_builder():
    # CC.0 is master; it must never be picked as a builder even when it's the only
    # available worker.
    assert _select_builder(_WORKERS, set(), avail_fn=_avail({"CC.0"})) is None


def _has_aider_tag_rule(cfg):
    return any(
        "Aider" in ((rule.get("when") or {}).get("tags_any") or [])
        for rule in cfg.get("rules", [])
    )


def test_aider_tag_opt_in_retired_from_daemon_defaults():
    assert not _has_aider_tag_rule(_default_config()), \
        "the Aider tag opt-in (topic-tag collision) must be retired from daemon defaults"


def test_aider_tag_opt_in_retired_from_granny_yaml():
    assert not _has_aider_tag_rule(_load_config()), \
        "the Aider tag opt-in must be retired from config/granny.yaml (kept in sync)"
