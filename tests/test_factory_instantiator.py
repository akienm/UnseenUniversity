"""Tests for devices.installer.factory — FactoryInstantiator lifecycle."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from unseen_university.devices.installer.factory import (
    FactoryInstantiator,
    FactorySpec,
    load_spec,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _minimal_spec(**overrides) -> FactorySpec:
    base = dict(
        factory_id="test-factory",
        factory_version="1.0",
        description="test",
        status="pending",
        owner_id="comms://akien/",
        orchestrator="granny-pattern",
        members=[{"agent_type": "librarian"}, {"agent_type": "scraps"}],
        eval_rubric="R-test",
        budget_limits={"daily_limit_usd": 5.00},
        escalation={"timeout_secs": 300},
    )
    base.update(overrides)
    return FactorySpec(**base)


def _make_instantiator(
    posts: list, profiles_dir: Path | None = None
) -> FactoryInstantiator:
    """Return a FactoryInstantiator whose posts are captured in the list."""
    kwargs = {"channel_post_fn": lambda owner, msg: posts.append((owner, msg))}
    if profiles_dir is not None:
        kwargs["profiles_dir"] = profiles_dir
    return FactoryInstantiator(**kwargs)


# ── load_spec ────────────────────────────────────────────────────────────────


class TestLoadSpec:
    def _write_spec(self, tmp_path: Path, content: dict) -> Path:
        p = tmp_path / "spec.yaml"
        p.write_text(yaml.dump(content))
        return p

    def test_valid_spec_parses(self, tmp_path: Path) -> None:
        p = self._write_spec(
            tmp_path,
            {
                "factory_id": "orca",
                "owner_id": "comms://akien/",
                "orchestrator": "granny-pattern",
                "members": [{"agent_type": "librarian"}, {"agent_type": "scraps"}],
                "status": "pending",
            },
        )
        spec = load_spec(p)
        assert spec.factory_id == "orca"
        assert spec.owner_id == "comms://akien/"
        assert len(spec.members) == 2

    def test_missing_required_field_raises(self, tmp_path: Path) -> None:
        p = self._write_spec(
            tmp_path,
            {
                "factory_id": "orca",
                # owner_id missing
                "orchestrator": "granny-pattern",
                "members": [{"agent_type": "librarian"}],
            },
        )
        with pytest.raises(ValueError, match="owner_id"):
            load_spec(p)

    def test_invalid_owner_id_raises(self, tmp_path: Path) -> None:
        p = self._write_spec(
            tmp_path,
            {
                "factory_id": "orca",
                "owner_id": "akien",  # must be comms://
                "orchestrator": "granny-pattern",
                "members": [{"agent_type": "librarian"}],
            },
        )
        with pytest.raises(ValueError, match="comms://"):
            load_spec(p)

    def test_unknown_orchestrator_raises(self, tmp_path: Path) -> None:
        p = self._write_spec(
            tmp_path,
            {
                "factory_id": "orca",
                "owner_id": "comms://akien/",
                "orchestrator": "not-a-real-pattern",
                "members": [{"agent_type": "librarian"}],
            },
        )
        with pytest.raises(ValueError, match="orchestrator"):
            load_spec(p)

    def test_empty_members_raises(self, tmp_path: Path) -> None:
        p = self._write_spec(
            tmp_path,
            {
                "factory_id": "orca",
                "owner_id": "comms://akien/",
                "orchestrator": "granny-pattern",
                "members": [],
            },
        )
        with pytest.raises(ValueError, match="members"):
            load_spec(p)

    def test_member_without_agent_type_raises(self, tmp_path: Path) -> None:
        p = self._write_spec(
            tmp_path,
            {
                "factory_id": "orca",
                "owner_id": "comms://akien/",
                "orchestrator": "granny-pattern",
                "members": [{"role": "librarian"}],  # missing agent_type
            },
        )
        with pytest.raises(ValueError, match="agent_type"):
            load_spec(p)

    def test_example_factory_spec_parses(self) -> None:
        """The committed example-factory.yaml must be valid."""
        spec_path = (
            Path(__file__).parent.parent
            / "unseen_university" / "config"
            / "factories"
            / "example-factory.yaml"
        )
        spec = load_spec(spec_path)
        assert spec.factory_id == "research-orca"
        assert spec.owner_id == "comms://akien/"
        assert len(spec.members) >= 2


# ── FactoryInstantiator.instantiate ─────────────────────────────────────────


class TestInstantiate:
    def test_two_members_produce_two_manifests(self, tmp_path: Path) -> None:
        posts: list = []
        inst = _make_instantiator(posts, profiles_dir=tmp_path)
        spec = _minimal_spec()
        factory_inst = inst.instantiate(spec)
        assert len(factory_inst.member_manifests) == 2

    def test_each_manifest_has_factory_orchestrator_channel(
        self, tmp_path: Path
    ) -> None:
        posts: list = []
        inst = _make_instantiator(posts, profiles_dir=tmp_path)
        spec = _minimal_spec()
        factory_inst = inst.instantiate(spec)
        for manifest in factory_inst.member_manifests:
            channel_names = [s.name for s in manifest.subscriptions]
            assert "test-factory/orchestrator" in channel_names

    def test_instantiate_posts_factory_announce_to_owner(self, tmp_path: Path) -> None:
        posts: list = []
        inst = _make_instantiator(posts, profiles_dir=tmp_path)
        spec = _minimal_spec()
        inst.instantiate(spec)
        assert any("FACTORY_ANNOUNCE" in msg for _, msg in posts)
        assert any(owner == "comms://akien/" for owner, _ in posts)

    def test_factory_announce_includes_member_types(self, tmp_path: Path) -> None:
        posts: list = []
        inst = _make_instantiator(posts, profiles_dir=tmp_path)
        spec = _minimal_spec()
        inst.instantiate(spec)
        announce = next(msg for _, msg in posts if "FACTORY_ANNOUNCE" in msg)
        assert "librarian" in announce
        assert "scraps" in announce

    def test_instance_status_is_active(self, tmp_path: Path) -> None:
        posts: list = []
        inst = _make_instantiator(posts, profiles_dir=tmp_path)
        factory_inst = inst.instantiate(_minimal_spec())
        assert factory_inst.status == "active"

    def test_manifest_issued_by_includes_factory_id(self, tmp_path: Path) -> None:
        posts: list = []
        inst = _make_instantiator(posts, profiles_dir=tmp_path)
        factory_inst = inst.instantiate(_minimal_spec())
        for manifest in factory_inst.member_manifests:
            assert "test-factory" in manifest.issued_by

    def test_profile_yaml_wires_allowed_devices(self, tmp_path: Path) -> None:
        profile = {
            "profile_version": "1.0",
            "agent_type": "myagent",
            "allowed_devices": ["inference"],
            "device_permissions": {"inference": {"mode": "read_write"}},
            "default_channels": ["shared"],
        }
        (tmp_path / "myagent.yaml").write_text(yaml.dump(profile))
        posts: list = []
        inst = _make_instantiator(posts, profiles_dir=tmp_path)
        spec = _minimal_spec(members=[{"agent_type": "myagent"}])
        factory_inst = inst.instantiate(spec)
        manifest = factory_inst.member_manifests[0]
        tool_names = [t.name for t in manifest.tools]
        assert "inference" in tool_names


# ── health_rollup ────────────────────────────────────────────────────────────


class TestHealthRollup:
    def test_posts_factory_health_to_owner(self, tmp_path: Path) -> None:
        posts: list = []
        inst = _make_instantiator(posts, profiles_dir=tmp_path)
        factory_inst = inst.instantiate(_minimal_spec())
        posts.clear()
        inst.health_rollup(factory_inst)
        assert any("FACTORY_HEALTH" in msg for _, msg in posts)
        assert any(owner == "comms://akien/" for owner, _ in posts)

    def test_health_rollup_returns_dict_with_factory_id(self, tmp_path: Path) -> None:
        posts: list = []
        inst = _make_instantiator(posts, profiles_dir=tmp_path)
        factory_inst = inst.instantiate(_minimal_spec())
        rollup = inst.health_rollup(factory_inst)
        assert rollup["factory_id"] == "test-factory"
        assert "overall" in rollup
        assert "members" in rollup

    def test_health_rollup_includes_all_members(self, tmp_path: Path) -> None:
        posts: list = []
        inst = _make_instantiator(posts, profiles_dir=tmp_path)
        factory_inst = inst.instantiate(_minimal_spec())
        rollup = inst.health_rollup(factory_inst)
        assert "librarian" in rollup["members"]
        assert "scraps" in rollup["members"]


# ── report_eval ──────────────────────────────────────────────────────────────


class TestReportEval:
    def test_posts_factory_eval_to_owner(self, tmp_path: Path) -> None:
        posts: list = []
        inst = _make_instantiator(posts, profiles_dir=tmp_path)
        factory_inst = inst.instantiate(_minimal_spec())
        posts.clear()
        inst.report_eval(factory_inst, {"accuracy": 0.9, "latency_p99": 1.2})
        assert any("FACTORY_EVAL" in msg for _, msg in posts)
        assert any(owner == "comms://akien/" for owner, _ in posts)

    def test_eval_message_includes_rubric(self, tmp_path: Path) -> None:
        posts: list = []
        inst = _make_instantiator(posts, profiles_dir=tmp_path)
        factory_inst = inst.instantiate(_minimal_spec())
        posts.clear()
        inst.report_eval(factory_inst, {"score": 0.8})
        eval_msg = next(msg for _, msg in posts if "FACTORY_EVAL" in msg)
        assert "R-test" in eval_msg


# ── escalate ────────────────────────────────────────────────────────────────


class TestEscalate:
    def test_escalate_posts_factory_escalate_to_owner(self, tmp_path: Path) -> None:
        posts: list = []
        inst = _make_instantiator(posts, profiles_dir=tmp_path)
        factory_inst = inst.instantiate(_minimal_spec())
        posts.clear()
        inst.escalate(factory_inst, "orchestrator blocked on ambiguous task")
        assert any("FACTORY_ESCALATE" in msg for _, msg in posts)
        assert any(owner == "comms://akien/" for owner, _ in posts)

    def test_escalate_message_includes_reason(self, tmp_path: Path) -> None:
        posts: list = []
        inst = _make_instantiator(posts, profiles_dir=tmp_path)
        factory_inst = inst.instantiate(_minimal_spec())
        posts.clear()
        inst.escalate(factory_inst, "no members available")
        esc_msg = next(msg for _, msg in posts if "FACTORY_ESCALATE" in msg)
        assert "no members available" in esc_msg


# ── halt ────────────────────────────────────────────────────────────────────


class TestHalt:
    def test_halt_sets_instance_status_to_halted(self, tmp_path: Path) -> None:
        posts: list = []
        inst = _make_instantiator(posts, profiles_dir=tmp_path)
        factory_inst = inst.instantiate(_minimal_spec())
        assert factory_inst.status == "active"
        inst.halt(factory_inst)
        assert factory_inst.status == "halted"

    def test_halt_posts_factory_halt_to_owner(self, tmp_path: Path) -> None:
        posts: list = []
        inst = _make_instantiator(posts, profiles_dir=tmp_path)
        factory_inst = inst.instantiate(_minimal_spec())
        posts.clear()
        inst.halt(factory_inst)
        assert any("FACTORY_HALT" in msg for _, msg in posts)
        assert any(owner == "comms://akien/" for owner, _ in posts)
