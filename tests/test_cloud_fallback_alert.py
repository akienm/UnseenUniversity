"""
Regression test for T-cloud-fallback-alert — pe_chain pushes a high-urgency
cc_inbox alert when _call_tier2 falls back from local Ollama to paid cloud.

Akien noticed a budget surprise once because the only signal was a forensic
log line. This test asserts the inbox push fires (and does not fire when
the OPENROUTER_API_KEY is absent — no fallback engaged).
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch


class TestCloudFallbackAlert(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        # Force the cloud-programming branch off so the function reaches
        # the local-Ollama path (where the failure + fallback alert lives).
        os.environ.pop("IGOR_CLOUD_PROGRAMMING", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_inbox_alert_fires_on_ollama_failure_with_or_key(self):
        os.environ["OPENROUTER_API_KEY"] = "test-key"

        from wild_igor.igor.tools import pe_chain

        with (
            patch.object(
                pe_chain, "_call_cloud_programming", return_value="cloud-response"
            ),
            patch(
                "wild_igor.igor.cognition.cc_inbox_bridge.post_to_cc_inbox"
            ) as mock_post,
            patch("urllib.request.urlopen", side_effect=ConnectionError("ollama down")),
        ):
            result = pe_chain._call_tier2("hello", timeout=1, temperature=0.1)

        self.assertEqual(result, "cloud-response")
        mock_post.assert_called_once()
        kwargs = mock_post.call_args.kwargs
        self.assertEqual(kwargs["kind"], "cloud_fallback_engaged")
        self.assertEqual(kwargs["urgency"], "high")
        self.assertIn("Ollama", kwargs["summary"])
        self.assertIn("OR cloud", kwargs["summary"])

    def test_no_inbox_alert_when_no_or_key(self):
        os.environ.pop("OPENROUTER_API_KEY", None)

        from wild_igor.igor.tools import pe_chain

        with (
            patch(
                "wild_igor.igor.cognition.cc_inbox_bridge.post_to_cc_inbox"
            ) as mock_post,
            patch("urllib.request.urlopen", side_effect=ConnectionError("ollama down")),
        ):
            result = pe_chain._call_tier2("hello", timeout=1, temperature=0.1)

        # No fallback path, no alert.
        self.assertIsNone(result)
        mock_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
