"""
Tests for pe_chain_priors and its wiring into scope_guard / pe_hypothesize.
"""

from unittest.mock import MagicMock, call, patch

# ── unit tests for pe_chain_priors module ─────────────────────────────────────


def test_append_prior_upserts_to_db():
    """append_prior calls INSERT ON CONFLICT with correct args."""
    mock_cur = MagicMock()
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value.__enter__ = lambda s: mock_cur
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch(
        "wild_igor.igor.tools.pe_chain_priors._conn", return_value=mock_conn
    ), patch("wild_igor.igor.tools.pe_chain_priors._ensure_table"):
        from wild_igor.igor.tools.pe_chain_priors import append_prior

        append_prior("thalamus.py", "HIGH_INERTIA", "scope_guard")

    mock_cur.execute.assert_called_once()
    sql, params = mock_cur.execute.call_args[0]
    assert "INSERT INTO instance.pe_chain_priors" in sql
    assert "ON CONFLICT" in sql
    assert params == ("thalamus.py", "HIGH_INERTIA", "scope_guard")


def test_append_prior_empty_path_is_noop():
    """append_prior with empty target_path does nothing."""
    with patch("wild_igor.igor.tools.pe_chain_priors._ensure_table") as mock_ensure:
        from wild_igor.igor.tools.pe_chain_priors import append_prior

        append_prior("", "HIGH_INERTIA", "scope_guard")
        mock_ensure.assert_not_called()


def test_append_prior_db_error_is_swallowed():
    """append_prior does not raise on DB failure — logs warning instead."""
    with patch(
        "wild_igor.igor.tools.pe_chain_priors._ensure_table",
        side_effect=Exception("db down"),
    ):
        from wild_igor.igor.tools.pe_chain_priors import append_prior

        # Should not raise
        append_prior("some_file.py", "HIGH_INERTIA", "scope_guard")


def test_get_top_priors_returns_rows():
    """get_top_priors returns list of dicts from DB rows."""
    fake_rows = [
        {
            "target_path": "thalamus.py",
            "symbol": "HIGH_INERTIA",
            "kind": "scope_guard",
            "count": 23,
        },
        {
            "target_path": "cortex.py",
            "symbol": "OLD_STRING_NOT_FOUND",
            "kind": "old_string_mismatch",
            "count": 5,
        },
    ]
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = [
        MagicMock(**{"__iter__": lambda s: iter(r.items()), **r}) for r in fake_rows
    ]

    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value.__enter__ = lambda s: mock_cur
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch(
        "wild_igor.igor.tools.pe_chain_priors._conn", return_value=mock_conn
    ), patch("wild_igor.igor.tools.pe_chain_priors._ensure_table"), patch(
        "psycopg2.extras.RealDictCursor"
    ):
        # Use real dict rows via fetchall returning dicts directly
        mock_cur.fetchall.return_value = fake_rows
        from wild_igor.igor.tools.pe_chain_priors import get_top_priors

        result = get_top_priors(5)
    assert result == fake_rows


def test_get_top_priors_returns_empty_on_error():
    """get_top_priors returns [] on DB error."""
    with patch(
        "wild_igor.igor.tools.pe_chain_priors._ensure_table",
        side_effect=Exception("db down"),
    ):
        from wild_igor.igor.tools.pe_chain_priors import get_top_priors

        result = get_top_priors()
    assert result == []


def test_build_priors_prompt_block_empty_when_no_priors():
    """build_priors_prompt_block returns empty string when get_top_priors is empty."""
    with patch("wild_igor.igor.tools.pe_chain_priors.get_top_priors", return_value=[]):
        from wild_igor.igor.tools.pe_chain_priors import build_priors_prompt_block

        result = build_priors_prompt_block()
    assert result == ""


def test_build_priors_prompt_block_formats_entries():
    """build_priors_prompt_block includes file path and kind×count in output."""
    fake_priors = [
        {
            "target_path": "thalamus.py",
            "symbol": "HIGH_INERTIA",
            "kind": "scope_guard",
            "count": 23,
        },
        {
            "target_path": "thalamus.py",
            "symbol": "OLD_STRING_NOT_FOUND",
            "kind": "old_string_mismatch",
            "count": 7,
        },
    ]
    with patch(
        "wild_igor.igor.tools.pe_chain_priors.get_top_priors", return_value=fake_priors
    ):
        from wild_igor.igor.tools.pe_chain_priors import build_priors_prompt_block

        result = build_priors_prompt_block()
    assert "thalamus.py" in result
    assert "scope_guard ×23" in result
    assert "old_string_mismatch ×7" in result
    assert "KNOWN BAD TARGETS" in result


# ── scope_guard wiring ────────────────────────────────────────────────────────


def test_scope_guard_high_inertia_calls_append_prior():
    """run_scope_guard calls append_prior when HIGH inertia file is targeted."""
    basket = {
        "hypothesis": {
            "file": "wild_igor/igor/brainstem/main_loop.py",
            "old_string": "x",
            "new_string": "y",
        },
        "hypotheses": [
            {
                "file": "wild_igor/igor/brainstem/main_loop.py",
                "old_string": "x",
                "new_string": "y",
            }
        ],
        "op_type": "write",
    }

    with patch(
        "wild_igor.igor.tools.scope_guard._classify_tier", return_value="HIGH"
    ), patch("wild_igor.igor.tools.scope_guard._OP_DELTA", {"write": 1}), patch(
        "wild_igor.igor.tools.pe_chain_priors.append_prior"
    ) as mock_append:
        from wild_igor.igor.tools.scope_guard import run_scope_guard

        run_scope_guard(basket)

    # append_prior should have been called for the HIGH inertia file
    calls = mock_append.call_args_list
    assert any(
        c[0][0] == "wild_igor/igor/brainstem/main_loop.py" and c[0][2] == "scope_guard"
        for c in calls
    ), f"Expected append_prior call for brainstem file, got: {calls}"


# ── pe_hypothesize wiring ─────────────────────────────────────────────────────


def test_hypothesize_prompt_includes_priors_block():
    """pe_hypothesize injects priors block into prompt when priors exist."""
    priors_text = (
        "KNOWN BAD TARGETS — files that caused errors in recent sprints "
        "(avoid as edit targets unless the ticket explicitly names them):\n"
        "- thalamus.py: scope_guard ×23"
    )

    captured_prompts = []

    def fake_call_tier2(prompt, temperature=0.2):
        captured_prompts.append(prompt)
        return None  # simulate tier2 unavailable so we can inspect prompt easily

    fake_self = MagicMock()
    fake_self.basket = {
        "ticket_description": "Add a new helper method",
        "actual": "def foo(): pass",
        "plan_files": ["some_file.py"],
    }

    with patch(
        "wild_igor.igor.tools.pe_chain._call_tier2", side_effect=fake_call_tier2
    ), patch(
        "wild_igor.igor.tools.pe_chain_priors.build_priors_prompt_block",
        return_value=priors_text,
    ), patch(
        "wild_igor.igor.tools.pe_chain._get_coding_standards", return_value=""
    ):
        from wild_igor.igor.tools.pe_chain import PeChain

        PeChain.pe_hypothesize(fake_self)

    assert captured_prompts, "tier.2 was never called"
    prompt = captured_prompts[0]
    assert "KNOWN BAD TARGETS" in prompt, "Priors block not injected into prompt"
    assert "thalamus.py" in prompt


def test_hypothesize_records_prior_on_validation_failure():
    """pe_hypothesize records old_string_mismatch prior when validation fails."""
    bad_edit = {
        "file": "thalamus.py",
        "old_string": "does not exist",
        "new_string": "y",
    }

    with patch(
        "wild_igor.igor.tools.pe_chain._call_tier2",
        return_value='{"edits": [{"file": "thalamus.py", "old_string": "does not exist", "new_string": "y"}]}',
    ), patch(
        "wild_igor.igor.tools.pe_chain._parse_hypothesis", return_value=[bad_edit]
    ), patch(
        "wild_igor.igor.tools.pe_chain._validate_hypotheses",
        return_value=["old_string not found in thalamus.py"],
    ), patch(
        "wild_igor.igor.tools.pe_chain._HYPOTHESIZE_MAX_RETRIES", 0
    ), patch(
        "wild_igor.igor.tools.pe_chain._get_coding_standards", return_value=""
    ), patch(
        "wild_igor.igor.tools.pe_chain_priors.build_priors_prompt_block",
        return_value="",
    ), patch(
        "wild_igor.igor.tools.pe_chain_priors.append_prior"
    ) as mock_append:
        from wild_igor.igor.tools.pe_chain import PeChain

        fake_self = MagicMock()
        fake_self.basket = {
            "ticket_description": "Fix something in thalamus",
            "actual": "def bar(): pass",
            "plan_files": ["thalamus.py"],
        }
        PeChain.pe_hypothesize(fake_self)

    mock_append.assert_called_once_with(
        "thalamus.py", "OLD_STRING_NOT_FOUND", "old_string_mismatch"
    )
