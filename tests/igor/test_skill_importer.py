"""
tests/test_skill_importer.py — T-skill-to-engram-generalise

Tests cover:
  - _parse_frontmatter: extracts name/description/model
  - _split_steps: splits on ## Step N headers
  - _extract_bash_blocks: finds bash code fences
  - _extract_skill_refs: finds /skill-name references
  - _extract_hard_rules: finds hard rules section
  - _build_payload: assembles run_cell with MCPCALL/FORKIF/ENDIF
  - import_skill: full round-trip with real skills dir
  - import_skill re-import: upsert reflects change (probe criterion)
  - import_all_skills: seeds all skills without error
"""

from __future__ import annotations

import sys
import textwrap
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _add_repo():
    repo = Path(__file__).parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))


_add_repo()

_SAMPLE_SKILL = textwrap.dedent("""\
    ---
    name: sample
    description: A sample skill for testing.
    model: haiku
    ---

    # Sample — Do the Thing

    ## Step 1 — Setup

    Run the setup command:

    ```bash
    echo "setup"
    ```

    ## Step 2 — Execute

    Now execute using /commit to commit the result.

    ```bash
    git status
    git diff --staged
    ```

    ## Step 3 — Verify

    Check output and call /filter on the plan.

    ## Hard rules

    - Never skip Step 1
    - Always verify before closing
    """)


class TestParseFrontmatter(unittest.TestCase):
    def test_extracts_name(self):
        from wild_igor.igor.tools.skill_importer import _parse_frontmatter

        meta, _ = _parse_frontmatter(_SAMPLE_SKILL)
        self.assertEqual(meta["name"], "sample")

    def test_extracts_description(self):
        from wild_igor.igor.tools.skill_importer import _parse_frontmatter

        meta, _ = _parse_frontmatter(_SAMPLE_SKILL)
        self.assertIn("sample skill", meta["description"])

    def test_extracts_model(self):
        from wild_igor.igor.tools.skill_importer import _parse_frontmatter

        meta, _ = _parse_frontmatter(_SAMPLE_SKILL)
        self.assertEqual(meta["model"], "haiku")

    def test_body_excludes_frontmatter(self):
        from wild_igor.igor.tools.skill_importer import _parse_frontmatter

        _, body = _parse_frontmatter(_SAMPLE_SKILL)
        self.assertNotIn("---", body)
        self.assertIn("Step 1", body)

    def test_no_frontmatter_returns_empty_meta(self):
        from wild_igor.igor.tools.skill_importer import _parse_frontmatter

        text = "# No frontmatter here\n\nJust prose."
        meta, body = _parse_frontmatter(text)
        self.assertEqual(meta, {})
        self.assertIn("No frontmatter", body)


class TestSplitSteps(unittest.TestCase):
    def _split(self, text):
        from wild_igor.igor.tools.skill_importer import _split_steps, _parse_frontmatter

        _, body = _parse_frontmatter(text)
        return _split_steps(body)

    def test_finds_three_steps(self):
        steps = self._split(_SAMPLE_SKILL)
        self.assertEqual(len(steps), 3)

    def test_step_titles(self):
        steps = self._split(_SAMPLE_SKILL)
        self.assertEqual(steps[0]["title"], "Setup")
        self.assertEqual(steps[1]["title"], "Execute")
        self.assertEqual(steps[2]["title"], "Verify")

    def test_step_text_contains_content(self):
        steps = self._split(_SAMPLE_SKILL)
        self.assertIn("setup", steps[0]["text"])
        self.assertIn("git status", steps[1]["text"])

    def test_no_steps_returns_empty(self):
        from wild_igor.igor.tools.skill_importer import _split_steps

        steps = _split_steps("Just prose with no step headers.")
        self.assertEqual(steps, [])


class TestExtractBashBlocks(unittest.TestCase):
    def test_finds_bash_blocks(self):
        from wild_igor.igor.tools.skill_importer import _extract_bash_blocks

        text = "Some text\n```bash\necho hello\n```\nMore text"
        blocks = _extract_bash_blocks(text)
        self.assertEqual(len(blocks), 1)
        self.assertIn("echo hello", blocks[0])

    def test_finds_multiple_blocks(self):
        from wild_igor.igor.tools.skill_importer import (
            _extract_bash_blocks,
            _split_steps,
            _parse_frontmatter,
        )

        _, body = _parse_frontmatter(_SAMPLE_SKILL)
        steps = _split_steps(body)
        blocks = _extract_bash_blocks(steps[1]["text"])  # Step 2 has 2 commands
        self.assertEqual(len(blocks), 1)  # one block with 2 lines
        self.assertIn("git status", blocks[0])

    def test_no_bash_blocks_returns_empty(self):
        from wild_igor.igor.tools.skill_importer import _extract_bash_blocks

        blocks = _extract_bash_blocks("Just prose, no code.")
        self.assertEqual(blocks, [])


class TestExtractSkillRefs(unittest.TestCase):
    def test_finds_skill_refs(self):
        from wild_igor.igor.tools.skill_importer import _extract_skill_refs

        text = "Run /commit then /filter on the plan."
        refs = _extract_skill_refs(text)
        self.assertIn("commit", refs)
        self.assertIn("filter", refs)

    def test_skips_path_components(self):
        from wild_igor.igor.tools.skill_importer import _extract_skill_refs

        text = "File at /home/akien/TheIgors/tools/runner.py"
        refs = _extract_skill_refs(text)
        self.assertNotIn("home", refs)

    def test_no_refs_returns_empty(self):
        from wild_igor.igor.tools.skill_importer import _extract_skill_refs

        refs = _extract_skill_refs("No skill references here.")
        self.assertEqual(refs, [])


class TestExtractHardRules(unittest.TestCase):
    def test_finds_hard_rules(self):
        from wild_igor.igor.tools.skill_importer import (
            _extract_hard_rules,
            _parse_frontmatter,
        )

        _, body = _parse_frontmatter(_SAMPLE_SKILL)
        rules = _extract_hard_rules(body)
        self.assertEqual(len(rules), 2)
        self.assertIn("Never skip Step 1", rules)
        self.assertIn("Always verify before closing", rules)

    def test_no_hard_rules_section(self):
        from wild_igor.igor.tools.skill_importer import _extract_hard_rules

        rules = _extract_hard_rules("## Step 1\nDo this.\n## Step 2\nDo that.")
        self.assertEqual(rules, [])


class TestBuildPayload(unittest.TestCase):
    def _build(self, text=_SAMPLE_SKILL):
        from wild_igor.igor.tools.skill_importer import (
            _build_payload,
            _split_steps,
            _extract_hard_rules,
            _extract_skill_refs,
            _parse_frontmatter,
        )

        _, body = _parse_frontmatter(text)
        steps = _split_steps(body)
        hard_rules = _extract_hard_rules(body)
        skill_refs = _extract_skill_refs(body)
        return _build_payload(steps, hard_rules, skill_refs)

    def test_run_cell_is_list(self):
        payload = self._build()
        self.assertIsInstance(payload["run_cell"], list)

    def test_run_cell_ends_with_endif(self):
        payload = self._build()
        self.assertEqual(payload["run_cell"][-1], "ENDIF")

    def test_bash_blocks_produce_mcpcall(self):
        payload = self._build()
        ops = [i[0] for i in payload["run_cell"] if isinstance(i, list)]
        self.assertIn("MCPCALL", ops)

    def test_mcpcall_targets_run_bash(self):
        payload = self._build()
        mcpcalls = [
            i for i in payload["run_cell"] if isinstance(i, list) and i[0] == "MCPCALL"
        ]
        self.assertTrue(all(i[1] == "run_bash" for i in mcpcalls))

    def test_skill_refs_produce_forkif(self):
        payload = self._build()
        forkifs = [
            i for i in payload["run_cell"] if isinstance(i, list) and i[0] == "FORKIF"
        ]
        self.assertGreater(len(forkifs), 0)
        targets = [i[2] for i in forkifs]
        self.assertTrue(any("COMMIT" in t or "FILTER" in t for t in targets))

    def test_step_text_stored_as_data_fields(self):
        payload = self._build()
        self.assertIn("step_0_text", payload)
        self.assertIn("Setup", payload["step_0_text"])

    def test_hard_rules_stored(self):
        payload = self._build()
        self.assertIn("hard_rules_text", payload)
        self.assertIn("Never skip Step 1", payload["hard_rules_text"])

    def test_no_duplicate_forkif_targets(self):
        # skill refs deduplicated — same skill ref twice → one FORKIF
        payload = self._build()
        forkifs = [
            i for i in payload["run_cell"] if isinstance(i, list) and i[0] == "FORKIF"
        ]
        targets = [i[2] for i in forkifs]
        self.assertEqual(len(targets), len(set(targets)))


class TestImportSkill(unittest.TestCase):
    """Integration tests using real skill files."""

    def setUp(self):
        import os

        os.environ.setdefault(
            "IGOR_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
        )
        os.environ.setdefault(
            "IGOR_DB_PATH",
            os.path.expanduser("~/.TheIgors/Igor-wild-0001/wild-0001.db"),
        )

    def test_import_filter_skill(self):
        from pathlib import Path

        skill_path = Path.home() / ".claude" / "skills" / "filter" / "SKILL.md"
        if not skill_path.exists():
            self.skipTest(f"Skill file not found: {skill_path}")

        from wild_igor.igor.tools.skill_importer import import_skill

        result = import_skill(skill_name="filter")
        self.assertIn("SKILL_FILTER_ENTRY", result)
        self.assertIn("seeded", result)

    def test_import_nonexistent_skill(self):
        from wild_igor.igor.tools.skill_importer import import_skill

        result = import_skill(skill_name="nonexistent_xyz")
        self.assertIn("not found", result)

    def test_import_empty_name(self):
        from wild_igor.igor.tools.skill_importer import import_skill

        result = import_skill(skill_name="")
        self.assertIn("skill_name required", result)

    def test_import_sprint_skill(self):
        from pathlib import Path

        skill_path = Path.home() / ".claude" / "skills" / "sprint" / "SKILL.md"
        if not skill_path.exists():
            self.skipTest(f"Skill file not found: {skill_path}")

        from wild_igor.igor.tools.skill_importer import import_skill

        result = import_skill(skill_name="sprint")
        self.assertIn("SKILL_SPRINT_ENTRY", result)
        self.assertIn("Steps:", result)

    def test_reimport_reflects_change(self):
        """Probe criterion: re-import after change reflects updated content."""
        import tempfile, os
        from wild_igor.igor.tools.skill_importer import import_skill
        import psycopg2, json

        db_url = os.environ.get(
            "IGOR_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
        )

        # Create a temp skill dir with a minimal skill
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "testskill"
            skill_dir.mkdir()
            skill_file = skill_dir / "SKILL.md"
            skill_file.write_text(textwrap.dedent("""\
                ---
                name: testskill
                description: Initial description.
                model: haiku
                ---
                ## Step 1 — Init
                ```bash
                echo "v1"
                ```
                ## Hard rules
                - Rule A
            """))

            # First import
            r1 = import_skill(skill_name="testskill", skills_dir=tmp)
            self.assertIn("seeded", r1)

            # Modify skill
            skill_file.write_text(textwrap.dedent("""\
                ---
                name: testskill
                description: Updated description.
                model: haiku
                ---
                ## Step 1 — Init
                ```bash
                echo "v2"
                ```
                ## Step 2 — New Step
                Run /filter on the result.
                ## Hard rules
                - Rule A
                - Rule B (new)
            """))

            # Re-import
            r2 = import_skill(skill_name="testskill", skills_dir=tmp)
            self.assertIn("seeded", r2)

            # Verify DB reflects update
            conn = psycopg2.connect(db_url)
            cur = conn.cursor()
            cur.execute(
                "SELECT metadata, payload FROM memories WHERE id = 'SKILL_TESTSKILL_ENTRY'"
            )
            row = cur.fetchone()
            conn.close()

            self.assertIsNotNone(row)
            meta = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            payload = json.loads(row[1]) if isinstance(row[1], str) else row[1]

            # Updated description and 2 steps
            self.assertEqual(len(meta["step_titles"]), 2)
            self.assertIn("New Step", meta["step_titles"][1])
            # FORKIF for /filter reference
            forkifs = [
                i
                for i in payload["run_cell"]
                if isinstance(i, list) and i[0] == "FORKIF"
            ]
            self.assertGreater(len(forkifs), 0)

            # Cleanup test node
            conn2 = psycopg2.connect(db_url)
            with conn2:
                conn2.cursor().execute(
                    "DELETE FROM memories WHERE id = 'SKILL_TESTSKILL_ENTRY'"
                )
            conn2.close()


class TestImportAllSkills(unittest.TestCase):
    def test_imports_all_returns_summary(self):
        from pathlib import Path

        skills_dir = Path.home() / ".claude" / "skills"
        if not skills_dir.exists():
            self.skipTest(f"Skills directory not found: {skills_dir}")

        from wild_igor.igor.tools.skill_importer import import_all_skills

        result = import_all_skills()
        self.assertIn("skills imported", result)
        # Should have seeded at least filter and sprint
        self.assertIn("filter", result)
        self.assertIn("sprint", result)

    def test_bad_dir_returns_not_found(self):
        from wild_igor.igor.tools.skill_importer import import_all_skills

        result = import_all_skills(skills_dir="/nonexistent/path/xyz")
        self.assertIn("not found", result)


if __name__ == "__main__":
    unittest.main()
