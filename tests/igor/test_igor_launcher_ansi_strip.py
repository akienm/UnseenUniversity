"""
Regression test for T-igor-log-strip-ansi.

Verifies that the sed pipeline used in TheIgors/igor (the bash launcher)
strips ANSI CSI + OSC escape sequences from Rich-style colored output
without touching the underlying text.
"""

from __future__ import annotations

import re
import subprocess
import unittest

# Same regex pair as the launcher's `sed -E 's/...//g; s/...//g'`.
SED_EXPR = r"s/\x1b\[[0-9;?]*[a-zA-Z]//g; s/\x1b\][^\x07]*\x07//g"


def _strip(input_bytes: bytes) -> bytes:
    """Run the same sed pipeline the launcher uses and return its output."""
    result = subprocess.run(
        ["sed", "-u", "-E", SED_EXPR],
        input=input_bytes,
        capture_output=True,
        check=True,
    )
    return result.stdout


class TestAnsiStripPipeline(unittest.TestCase):
    def test_strips_color_csi(self):
        # Typical Rich red→reset sequence.
        raw = b"\x1b[31mERROR\x1b[0m: failure\n"
        out = _strip(raw)
        self.assertEqual(out, b"ERROR: failure\n")
        self.assertNotIn(b"\x1b", out)

    def test_strips_compound_csi(self):
        # Bold + 256-color foreground.
        raw = b"\x1b[1;38;5;208mWARN\x1b[0m\n"
        out = _strip(raw)
        self.assertEqual(out, b"WARN\n")

    def test_strips_dim_and_reset(self):
        # Rich's [dim]...[/dim] expands to \x1b[2m...\x1b[22m.
        raw = b"\x1b[2mdim text\x1b[22m\n"
        out = _strip(raw)
        self.assertEqual(out, b"dim text\n")

    def test_strips_osc_title_sequence(self):
        # Many terminals emit OSC 0;title BEL when Rich sets the window title.
        raw = b"\x1b]0;Igor wild-0001\x07prompt$ \n"
        out = _strip(raw)
        self.assertEqual(out, b"prompt$ \n")

    def test_preserves_normal_text_unchanged(self):
        raw = b"plain text with no escapes\n12345 special chars: & % $\n"
        out = _strip(raw)
        self.assertEqual(out, raw)

    def test_strips_mixed_realistic_payload(self):
        raw = (
            b"\x1b[31mERROR\x1b[0m: ticket \x1b[1;33mT-foo\x1b[0m queued\n"
            b"\x1b[2mdim trace line\x1b[22m\n"
            b"\x1b]0;Igor\x07standard prompt\n"
            b"unstyled message\n"
        )
        out = _strip(raw)
        self.assertEqual(
            out,
            (
                b"ERROR: ticket T-foo queued\n"
                b"dim trace line\n"
                b"standard prompt\n"
                b"unstyled message\n"
            ),
        )
        # Total absence of escape characters.
        self.assertNotIn(b"\x1b", out)

    def test_launcher_script_uses_expected_sed_expression(self):
        """Catch silent drift between this test's regex and the actual
        launcher's regex — both should be the same string."""
        from pathlib import Path

        launcher = Path("/home/akien/TheIgors/igor").read_text()
        # The launcher embeds the sed expression inside the pipe-pane command.
        expected_fragment = (
            r"sed -u -E 's/\x1b\[[0-9;?]*[a-zA-Z]//g; s/\x1b\][^\x07]*\x07//g'"
        )
        self.assertIn(expected_fragment, launcher, "launcher sed regex drift")


if __name__ == "__main__":
    unittest.main()
