"""conftest.py — pytest session fixtures for TheIgors tests.

Prevents tests from creating directories in the live ~/.TheIgors/ instance.
Uses property patching rather than env vars so subprocesses are unaffected.

Test schema lifecycle (T-test-postgres-schema):
  A session-scoped fixture creates a dedicated Postgres test schema
  (test_clan_<epoch>) at session start and drops it at teardown.
  The schema is a thin LIKE-copy of clan + instance tables so Cortex
  and other tools can write freely without touching production data.

  Env overrides used:
    IGOR_HOME_SEARCH_PATH  → test_clan_<ts>,infra,public
    IGOR_LOCAL_SEARCH_PATH → test_instance_<ts>,test_clan_<ts>,infra,public

  Both env vars are cleared at session end. If the DB is unavailable
  the fixture yields without schema creation — tests fall through to
  the live clan schema (guarded by IGOR_TEST_MODE cleanup).
"""

import os
import time

import pytest


@pytest.fixture(autouse=True, scope="session")
def pg_test_schema():
    """Create isolated Postgres schemas for this test session.

    Creates test_clan_<ts> + test_instance_<ts> schemas via LIKE-copy of
    the production tables. Sets IGOR_HOME_SEARCH_PATH and IGOR_LOCAL_SEARCH_PATH
    so Cortex writes there instead of the live clan/instance schemas.
    Drops both schemas on teardown.

    Skipped gracefully when IGOR_HOME_DB_URL is not available.
    """
    db_url = os.environ.get("IGOR_HOME_DB_URL") or os.environ.get("IGOR_DB_URL")
    if not db_url:
        yield None
        return

    ts = int(time.time())
    clan_schema = f"test_clan_{ts}"
    inst_schema = f"test_instance_{ts}"

    try:
        import psycopg2

        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cur = conn.cursor()

        # Create schemas
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {clan_schema}")
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {inst_schema}")

        # Mirror all clan tables (LIKE copies structure + constraints)
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'clan' ORDER BY table_name"
        )
        clan_tables = [r[0] for r in cur.fetchall() if r[0] != "_migrations"]
        for t in clan_tables:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {clan_schema}.{t} "
                f"(LIKE clan.{t} INCLUDING ALL)"
            )

        # Mirror all instance tables
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'instance' ORDER BY table_name"
        )
        inst_tables = [r[0] for r in cur.fetchall()]
        for t in inst_tables:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {inst_schema}.{t} "
                f"(LIKE instance.{t} INCLUDING ALL)"
            )

        cur.close()
        conn.close()

        os.environ["IGOR_HOME_SEARCH_PATH"] = f"{clan_schema},infra,public"
        os.environ["IGOR_LOCAL_SEARCH_PATH"] = (
            f"{inst_schema},{clan_schema},infra,public"
        )

        yield clan_schema

    except Exception as exc:
        print(f"\n[pg_test_schema] setup failed ({exc}) — using live clan schema")
        yield None
        return

    # Teardown
    os.environ.pop("IGOR_HOME_SEARCH_PATH", None)
    os.environ.pop("IGOR_LOCAL_SEARCH_PATH", None)
    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(f"DROP SCHEMA IF EXISTS {clan_schema} CASCADE")
        cur.execute(f"DROP SCHEMA IF EXISTS {inst_schema} CASCADE")
        cur.close()
        conn.close()
    except Exception as exc:
        print(f"\n[pg_test_schema] teardown failed ({exc})")


@pytest.fixture(autouse=True, scope="session")
def _redirect_inbox_to_test_dir(tmp_path_factory):
    """Redirect paths().inbox to a temp dir for the whole test session.

    Without this, tests that call FileInboxChannel().acquire() trigger
    paths().inbox.mkdir(parents=True, exist_ok=True), which creates
    ~/.TheIgors/Igor-wild-0001/inbox/ (the default instance when
    IGOR_INSTANCE_ID is not set in the test environment).
    """
    test_inbox = tmp_path_factory.mktemp("igor_test_inbox")

    from wild_igor.igor.paths import PathManager

    orig_inbox = PathManager.inbox.fget

    PathManager.inbox = property(lambda self: test_inbox)

    yield test_inbox

    PathManager.inbox = property(orig_inbox)


@pytest.fixture(autouse=True, scope="session")
def _test_data_lifecycle():
    """T-test-data-lifecycle: auto-tag + auto-cleanup throwaway test data.

    Sets IGOR_TEST_MODE=1 so cortex.store() stamps metadata.test_data=True
    and metadata.test_expires_at on every memory created during the test
    session. On session teardown, deletes all rows matching the tag.

    The tag-based cleanup is the primary mechanism; the TTL is a
    belt-and-suspenders safeguard for crashed/interrupted test runs.
    Opt-out: tests that want cross-session persistence can set
    metadata.test_data=False explicitly.
    """
    prior_flag = os.environ.get("IGOR_TEST_MODE")
    os.environ["IGOR_TEST_MODE"] = "1"

    yield

    # Restore env var
    if prior_flag is None:
        os.environ.pop("IGOR_TEST_MODE", None)
    else:
        os.environ["IGOR_TEST_MODE"] = prior_flag

    # Best-effort cleanup — never block the test session on failure
    try:
        from wild_igor.igor.memory.cortex import Cortex
        from wild_igor.igor.memory.test_data_lifecycle import cleanup_test_data

        cortex = Cortex()
        removed = cleanup_test_data(cortex)
        if removed:
            print(f"\n[test_data_lifecycle] cleaned up {removed} test memories")
    except Exception as exc:
        print(f"\n[test_data_lifecycle] cleanup skipped: {exc}")


@pytest.fixture(autouse=True, scope="session")
def cc_inbox_test_tag():
    """T-test-inbox-tagging: tag every cc_inbox.append() during this test
    session and sweep matching entries on teardown.

    Sets CC_INBOX_TAG=test:<YYYYMMDD.HHMMSS.ffffff> so cc_inbox.append()
    prepends "[test:<ts>]: " to summaries. On session_finish, calls
    delete_by_prefix("[test:<ts>]") to remove every entry the test session
    produced, leaving production inbox entries untouched.

    Stragglers (tests that bypass this fixture) remain findable via
    delete_by_prefix("[test:") for manual broad sweeps.
    """
    from datetime import datetime, timezone
    from pathlib import Path
    import sys

    # Sweep residue from any prior test session that was killed before teardown.
    # Covers the case where CC_INBOX_TAG was set but the session died mid-run.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from lab.claudecode.cc_inbox import delete_by_prefix as _dbp

        stale = _dbp("[test:")
        if stale:
            print(
                f"\n[cc_inbox_test_tag] swept {stale} stale entry(ies) from prior session(s)"
            )
    except Exception:
        pass

    tag_ts = datetime.now(timezone.utc).strftime("%Y%m%d.%H%M%S.%f")
    tag = f"test:{tag_ts}"
    prior = os.environ.get("CC_INBOX_TAG")
    os.environ["CC_INBOX_TAG"] = tag

    yield tag

    if prior is None:
        os.environ.pop("CC_INBOX_TAG", None)
    else:
        os.environ["CC_INBOX_TAG"] = prior

    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from lab.claudecode.cc_inbox import delete_by_prefix

        removed = delete_by_prefix(f"[{tag}]")
        if removed:
            print(f"\n[cc_inbox_test_tag] swept {removed} tagged entry(ies)")
    except Exception as exc:
        print(f"\n[cc_inbox_test_tag] sweep skipped: {exc}")
