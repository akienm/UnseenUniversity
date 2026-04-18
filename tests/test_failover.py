"""
test_failover.py — T-postgres-home-db-failover

Tests for failover appointment tool.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestAppointHomeDb:
    def test_unknown_hostname_returns_error(self):
        from lab.utility_closet.failover import appoint_home_db

        with patch("lab.utility_closet.machine_manager.get_machine", return_value=None):
            result = appoint_home_db("nonexistent-box")
        assert "error" in result

    def test_valid_hostname_updates_env(self):
        from lab.utility_closet.failover import appoint_home_db

        mock_machine = MagicMock()
        mock_machine.ip = "10.0.0.99"
        old = os.environ.get("IGOR_HOME_DB_URL")
        try:
            with patch(
                "lab.utility_closet.machine_manager.get_machine",
                return_value=mock_machine,
            ):
                result = appoint_home_db("akiendell")
            assert result["env_updated"] is True
            assert "10.0.0.99" in result["new_host"]
            assert "manual_steps" in result
        finally:
            if old is not None:
                os.environ["IGOR_HOME_DB_URL"] = old
            else:
                os.environ.pop("IGOR_HOME_DB_URL", None)

    def test_returns_manual_steps(self):
        from lab.utility_closet.failover import appoint_home_db

        mock_machine = MagicMock()
        mock_machine.ip = "10.0.0.90"
        old = os.environ.get("IGOR_HOME_DB_URL")
        try:
            with patch(
                "lab.utility_closet.machine_manager.get_machine",
                return_value=mock_machine,
            ):
                result = appoint_home_db("yoga9i")
            assert len(result["manual_steps"]) >= 3
        finally:
            if old is not None:
                os.environ["IGOR_HOME_DB_URL"] = old
            else:
                os.environ.pop("IGOR_HOME_DB_URL", None)


class TestCheckHomeDbHealth:
    def test_healthy_when_connected(self):
        from lab.utility_closet.failover import check_home_db_health

        if not os.environ.get("IGOR_HOME_DB_URL"):
            pytest.skip("No DB URL")
        result = check_home_db_health()
        assert result["healthy"] is True

    def test_unhealthy_without_url(self):
        from lab.utility_closet.failover import check_home_db_health

        saved = os.environ.pop("IGOR_HOME_DB_URL", None)
        try:
            result = check_home_db_health()
            assert result["healthy"] is False
        finally:
            if saved:
                os.environ["IGOR_HOME_DB_URL"] = saved
