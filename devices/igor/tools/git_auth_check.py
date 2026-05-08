"""
git_auth_check.py — T-gh-auth-check: gh token expiry detection.

Runs 'gh auth status' and reports whether the token is valid.
Called by PROC_GIT_AUTH_CHECK habit before git push operations.
"""

import logging
import subprocess

from lab.utility_closet.registry import Tool, registry

logger = logging.getLogger(__name__)

_EXPIRY_SIGNALS = (
    "not logged in",
    "token invalid",
    "401",
    "authentication required",
    "bad credentials",
)


def check_gh_auth(**_) -> str:
    """
    Run 'gh auth status' and return a clear pass/fail message.

    Returns "OK: gh token valid" on success, or a warning message with
    remediation instructions if the token is expired or invalid.
    """
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = (result.stdout + result.stderr).lower()
        if result.returncode != 0 or any(sig in output for sig in _EXPIRY_SIGNALS):
            logger.warning("gh auth check failed: %s", output[:200])
            return (
                "⚠️  gh auth token is invalid or expired.\n"
                "Run 'gh auth login' before attempting git push.\n"
                f"gh output: {(result.stdout + result.stderr).strip()[:300]}"
            )
        return "OK: gh token valid — git push should work."
    except FileNotFoundError:
        return "gh CLI not installed — skipping auth check."
    except subprocess.TimeoutExpired:
        return "gh auth status timed out — proceeding with caution."
    except Exception as e:
        logger.error("check_gh_auth error: %s", e)
        return f"gh auth check error: {e}"


registry.register(
    Tool(
        name="check_gh_auth",
        description=(
            "Check whether the gh CLI auth token is valid before git push. "
            "Run this before committing/pushing to avoid silent auth failures. "
            "Returns OK if the token is valid, or a warning with remediation steps."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=check_gh_auth,
    )
)
