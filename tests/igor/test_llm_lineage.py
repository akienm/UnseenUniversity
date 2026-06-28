"""
Tests for T-universal-llm-lineage:
  - infra.llm_calls migration applied (table exists)
  - _write_llm_call_db writes a row and it's queryable
  - log_inference_io dual-write: file still written, DB row added
  - IgorBase.log_llm_io dual-write: file still written, DB row added
"""

import hashlib
import os
import time
import unittest
from unittest.mock import MagicMock, patch

_DB_URL = os.environ.get(
    "UU_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)


def _skip_if_no_db():
    try:
        import psycopg2

        psycopg2.connect(_DB_URL).close()
        return False
    except Exception:
        return True


@unittest.skipIf(_skip_if_no_db(), "DB unavailable")
class TestLLMLineageMigration(unittest.TestCase):
    """Migration applied — table and index exist."""

    def test_table_exists(self):
        import psycopg2

        conn = psycopg2.connect(_DB_URL)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'infra' AND table_name = 'llm_calls'"
            )
            row = cur.fetchone()
        conn.close()
        self.assertIsNotNone(row, "infra.llm_calls table does not exist")

    def test_required_columns_exist(self):
        import psycopg2

        conn = psycopg2.connect(_DB_URL)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'infra' AND table_name = 'llm_calls'"
            )
            cols = {r[0] for r in cur.fetchall()}
        conn.close()
        required = {
            "id",
            "ts",
            "prompt_hash",
            "model",
            "tokens_in",
            "tokens_out",
            "outcome",
            "source_fn",
            "elapsed_ms",
            "instance_id",
        }
        missing = required - cols
        self.assertFalse(missing, f"Missing columns: {missing}")


@unittest.skipIf(_skip_if_no_db(), "DB unavailable")
class TestWriteLLMCallDB(unittest.TestCase):
    """_write_llm_call_db inserts a queryable row."""

    def setUp(self):
        os.environ["UU_HOME_DB_URL"] = _DB_URL
        # Use a unique hash so we can find our row
        self._hash = f"test_{int(time.time() * 1000)}"

    def tearDown(self):
        # Clean up test row
        try:
            import psycopg2

            conn = psycopg2.connect(_DB_URL)
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM infra.llm_calls WHERE prompt_hash LIKE 'test_%'"
                    )
            conn.close()
        except Exception:
            pass

    def test_row_inserted_and_queryable(self):
        from unseen_university.devices.igor.cognition.forensic_logger import _write_llm_call_db
        import psycopg2

        _write_llm_call_db(
            prompt_hash=self._hash,
            model="test-model",
            tokens_in=100,
            tokens_out=50,
            outcome="pass",
            source_fn="test_fn",
            elapsed_ms=123,
        )

        conn = psycopg2.connect(_DB_URL)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT model, tokens_in, tokens_out, outcome, source_fn, elapsed_ms "
                "FROM infra.llm_calls WHERE prompt_hash = %s",
                (self._hash,),
            )
            row = cur.fetchone()
        conn.close()

        self.assertIsNotNone(row, "No row found in infra.llm_calls")
        model, tokens_in, tokens_out, outcome, source_fn, elapsed_ms = row
        self.assertEqual(model, "test-model")
        self.assertEqual(tokens_in, 100)
        self.assertEqual(tokens_out, 50)
        self.assertEqual(outcome, "pass")
        self.assertEqual(source_fn, "test_fn")
        self.assertEqual(elapsed_ms, 123)


class TestLogInferenceIODualWrite(unittest.TestCase):
    """log_inference_io: file still written, DB write attempted."""

    def test_file_write_preserved(self, tmp_path=None):
        """File log write must still succeed after our changes."""
        from unseen_university.devices.igor.cognition import forensic_logger as fl

        written = []

        class _FakePath:
            def open(self, *a, **kw):
                class _FakeFile:
                    def __enter__(self):
                        return self

                    def __exit__(self, *a):
                        pass

                    def write(self, s):
                        written.append(s)

                return _FakeFile()

            def mkdir(self, **_):
                pass

            def __truediv__(self, other):
                return self

        with (
            patch.object(fl, "LOG_DIR", _FakePath()),
            patch(
                "unseen_university.devices.igor.cognition.forensic_logger._write_llm_call_db"
            ) as mock_db,
            patch("unseen_university.devices.igor.cognition.forensic_logger._purge_old_inference_io"),
        ):
            fl.log_inference_io(
                provider="openrouter",
                model="claude-3",
                prompt="hello world",
                response="hi",
                elapsed_ms=50,
                call_type="reason",
            )

        self.assertTrue(len(written) > 0, "Nothing written to file")
        self.assertIn("openrouter/claude-3", written[0])
        mock_db.assert_called_once()

    def test_db_write_called_with_correct_hash(self):
        from unseen_university.devices.igor.cognition import forensic_logger as fl

        prompt = "test prompt for hashing"
        expected_hash = hashlib.md5(prompt.encode("utf-8")).hexdigest()

        class _FakePath:
            def open(self, *a, **kw):
                class _F:
                    def __enter__(self):
                        return self

                    def __exit__(self, *a):
                        pass

                    def write(self, s):
                        pass

                return _F()

            def mkdir(self, **_):
                pass

            def __truediv__(self, other):
                return self

        with (
            patch.object(fl, "LOG_DIR", _FakePath()),
            patch(
                "unseen_university.devices.igor.cognition.forensic_logger._write_llm_call_db"
            ) as mock_db,
            patch("unseen_university.devices.igor.cognition.forensic_logger._purge_old_inference_io"),
        ):
            fl.log_inference_io(
                provider="ollama",
                model="llama3",
                prompt=prompt,
                response="response",
                elapsed_ms=100,
            )

        call_kwargs = mock_db.call_args[1]
        self.assertEqual(call_kwargs["prompt_hash"], expected_hash)
        self.assertEqual(call_kwargs["model"], "llama3")
        self.assertEqual(call_kwargs["elapsed_ms"], 100)
