"""
test_blame_with_model.py — T-blame-with-model

Unit tests for the Co-Authored-By extraction + porcelain parser. Live
git interaction is exercised by one integration test against this repo.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devlab.claudecode.blame_with_model import (  # noqa: E402
    BlameLine,
    blame_file,
    extract_coauthor_model,
    parse_blame_porcelain,
)

# ── extract_coauthor_model ───────────────────────────────────────────────────


class TestExtractCoauthor:
    def test_opus_token_recognized(self):
        msg = "feat: x\n\nbody\n\nCo-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>\n"
        name, model = extract_coauthor_model(msg)
        assert model == "opus"
        assert "Opus" in name

    def test_sonnet_token_recognized(self):
        msg = "fix: y\n\nCo-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>\n"
        _, model = extract_coauthor_model(msg)
        assert model == "sonnet"

    def test_haiku_token_recognized(self):
        msg = (
            "refactor: z\n\nCo-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>\n"
        )
        _, model = extract_coauthor_model(msg)
        assert model == "haiku"

    def test_no_trailer_returns_dash(self):
        msg = "chore: bump\n\nNo coauthor here.\n"
        name, model = extract_coauthor_model(msg)
        assert model == "-"
        assert name == ""

    def test_unknown_coauthor_returns_full_name(self):
        msg = "fix: x\n\nCo-Authored-By: Some Human <human@example.com>\n"
        name, model = extract_coauthor_model(msg)
        assert model == "Some Human"

    def test_case_insensitive_trailer(self):
        msg = "feat: x\n\nco-authored-by: Claude Opus <x@y.com>\n"
        _, model = extract_coauthor_model(msg)
        assert model == "opus"

    def test_first_trailer_wins_when_multiple(self):
        msg = "feat: x\n\nCo-Authored-By: Claude Opus 4.7 <a@b.c>\nCo-Authored-By: Claude Sonnet 4.6 <c@d.e>\n"
        _, model = extract_coauthor_model(msg)
        assert model == "opus"

    def test_extra_whitespace_tolerated(self):
        msg = "feat: x\n\n   Co-Authored-By:   Claude Opus 4.7 (1M context)   <a@b.c>\n"
        _, model = extract_coauthor_model(msg)
        assert model == "opus"


# ── parse_blame_porcelain ────────────────────────────────────────────────────


class TestParseBlamePorcelain:
    # Each SHA must be exactly 40 hex chars to match the porcelain format.
    SHA_A = "a" * 40
    SHA_D = "d" * 40
    SAMPLE = (
        f"{SHA_A} 1 1 2\n"
        "author Akien MacIain\n"
        "author-mail <akien@example.com>\n"
        "author-time 1714400000\n"
        "summary feat: x\n"
        "filename foo.py\n"
        "\thello = 1\n"
        f"{SHA_A} 2 2 2\n"
        "\tworld = 2\n"
        f"{SHA_D} 3 3 1\n"
        "author Igor\n"
        "author-mail <igor@theigors>\n"
        "summary fix: y\n"
        "filename foo.py\n"
        "\tprint(hello)\n"
    )

    def test_extracts_three_lines(self):
        lines = parse_blame_porcelain(self.SAMPLE)
        assert len(lines) == 3

    def test_first_line_metadata(self):
        lines = parse_blame_porcelain(self.SAMPLE)
        line, sha, author, source = lines[0]
        assert line == 1
        assert sha == self.SHA_A
        assert author == "Akien MacIain"
        assert source == "hello = 1"

    def test_second_line_inherits_sha(self):
        lines = parse_blame_porcelain(self.SAMPLE)
        line, sha, _, source = lines[1]
        assert line == 2
        assert sha == self.SHA_A
        assert source == "world = 2"

    def test_third_line_different_commit(self):
        lines = parse_blame_porcelain(self.SAMPLE)
        line, sha, author, source = lines[2]
        assert line == 3
        assert sha == self.SHA_D
        assert author == "Igor"
        assert source == "print(hello)"

    def test_empty_input(self):
        assert parse_blame_porcelain("") == []


# ── blame_file (with mocked subprocess) ─────────────────────────────────────


class TestBlameFile:
    SHA_X = "a" * 40  # exactly 40 hex chars, matches porcelain format

    def test_attaches_model_from_message(self):
        mock_blame = (
            f"{self.SHA_X} 1 1 1\n"
            "author Akien MacIain\n"
            "summary feat: x\n"
            "\tfoo = 1\n"
        )
        mock_msg = "feat: x\n\nbody\n\nCo-Authored-By: Claude Opus 4.7 <a@b.c>\n"

        def fake_run(args, **kwargs):
            class _R:
                returncode = 0
                stdout = mock_blame if "blame" in args else mock_msg

            return _R()

        with patch(
            "devlab.claudecode.blame_with_model.subprocess.run", side_effect=fake_run
        ):
            blames = blame_file(Path("foo.py"))
        assert len(blames) == 1
        assert blames[0].model == "opus"
        assert blames[0].code == "foo = 1"
        assert blames[0].author == "Akien MacIain"

    def test_caches_message_per_commit(self):
        mock_blame = (
            f"{self.SHA_X} 1 1 2\n"
            "author A\n"
            "\tline1\n"
            f"{self.SHA_X} 2 2 2\n"
            "\tline2\n"
        )
        msg = "feat: x\n\nCo-Authored-By: Claude Sonnet <a@b.c>\n"
        call_count = {"log": 0}

        def fake_run(args, **kwargs):
            class _R:
                returncode = 0
                stdout = ""

            if "blame" in args:
                _R.stdout = mock_blame
            elif "log" in args:
                call_count["log"] += 1
                _R.stdout = msg
            return _R()

        with patch(
            "devlab.claudecode.blame_with_model.subprocess.run", side_effect=fake_run
        ):
            blames = blame_file(Path("foo.py"))
        assert len(blames) == 2
        # Both lines from the same commit → only 1 git log call
        assert call_count["log"] == 1
        assert blames[0].model == "sonnet"
        assert blames[1].model == "sonnet"


# ── live integration ────────────────────────────────────────────────────────


class TestIntegrationLive:
    def test_blame_real_file_with_known_coauthor(self):
        """consult.py has recent commits with Co-Authored-By trailers."""
        import subprocess as _sp

        repo_root = Path(__file__).resolve().parent.parent.parent
        target = repo_root / "devices" / "igor" / "cognition" / "consult.py"
        if not target.exists():
            return  # integration only runs in a checkout
        # Skip if file isn't tracked (fresh worktree)
        check = _sp.run(
            ["git", "ls-files", "--error-unmatch", str(target)],
            cwd=str(repo_root),
            capture_output=True,
        )
        if check.returncode != 0:
            return
        # Blame the dataclass region — recent edits have trailers
        blames = blame_file(target, start_line=145, end_line=160, cwd=repo_root)
        assert blames, "expected at least one line of blame"
        # At least one recent line should attribute to opus/sonnet/haiku
        assert any(b.model in ("opus", "sonnet", "haiku") for b in blames)
