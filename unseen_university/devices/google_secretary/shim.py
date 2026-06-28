"""
GoogleSecretaryShim — lifecycle + OAuth management for the Google Secretary device.

Manages the OAuth 2.0 token file. On start():
  - Verifies credentials.json exists at the configured home path
  - Loads or refreshes the stored OAuth token (file-backed, no Postgres dependency)
  - Marks availability

Token storage: flat file at <home>/token.json (gitignored, owner-only 0o600).
Credentials file: <home>/credentials.json — must be provided by Akien (Google
OAuth app credentials from Google Cloud Console, NOT committed to git).

Auth scopes required:
  - https://www.googleapis.com/auth/calendar
  - https://www.googleapis.com/auth/gmail.modify
  - https://www.googleapis.com/auth/tasks
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from unseen_university.shim import BaseShim

log = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/tasks",
]
_DEFAULT_HOME = Path.home() / ".unseen_university" / "google_secretary"


class GoogleSecretaryShim(BaseShim):
    """
    Shim for GoogleSecretaryDevice. Owns OAuth credential lifecycle.

    Credentials file: <home>/credentials.json (provided by Akien — never in git)
    Token file:       <home>/token.json       (auto-generated, gitignored)
    """

    def __init__(
        self,
        home: Path | str = _DEFAULT_HOME,
        token_storage: str = "file",
    ) -> None:
        self._home = Path(home)
        self._token_storage = token_storage
        self._creds = None
        self._started = False

    @property
    def device_id(self) -> str:
        return "google_secretary"

    # ── OAuth helpers ──────────────────────────────────────────────────────────

    def _creds_path(self) -> Path:
        return self._home / "credentials.json"

    def _token_path(self) -> Path:
        return self._home / "token.json"

    def _load_or_refresh_token(self):
        """Load token from file; refresh if expired; run OAuth flow if missing."""
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
        except ImportError:
            log.warning(
                "GoogleSecretaryShim: google-auth not installed — "
                "run: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client"
            )
            return None

        token_path = self._token_path()
        creds = None

        if token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)
            except Exception as exc:
                log.warning("GoogleSecretaryShim: token load failed: %s", exc)
                creds = None

        if creds and creds.valid:
            return creds

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                self._save_token(creds)
                log.info("GoogleSecretaryShim: token refreshed")
                return creds
            except Exception as exc:
                log.warning("GoogleSecretaryShim: token refresh failed: %s", exc)
                creds = None

        # Interactive OAuth flow — requires user to authorize in browser
        creds_path = self._creds_path()
        if not creds_path.exists():
            log.warning(
                "GoogleSecretaryShim: credentials.json not found at %s — "
                "download from Google Cloud Console and place there",
                creds_path,
            )
            return None

        try:
            from google_auth_oauthlib.flow import InstalledAppFlow

            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), _SCOPES)
            creds = flow.run_local_server(port=0)
            self._save_token(creds)
            log.info("GoogleSecretaryShim: OAuth flow completed, token saved")
            return creds
        except Exception as exc:
            log.error("GoogleSecretaryShim: OAuth flow failed: %s", exc)
            return None

    def _save_token(self, creds) -> None:
        token_path = self._token_path()
        token_path.write_text(creds.to_json())
        token_path.chmod(0o600)  # owner-only — contains refresh token

    def get_credentials(self):
        """Return cached credentials, refreshing if needed."""
        if self._creds is None or not self._creds.valid:
            self._creds = self._load_or_refresh_token()
        return self._creds

    # ── BaseShim contract ──────────────────────────────────────────────────────

    def start(self) -> bool:
        self._home.mkdir(parents=True, exist_ok=True)

        creds = self._load_or_refresh_token()
        if creds is None:
            log.warning(
                "GoogleSecretaryShim: started without valid credentials — "
                "device will return errors until OAuth is configured"
            )
            # Don't fail start() — device can still boot; it just won't work
            # until credentials are provided. This matches KnightlyBuilder principle:
            # device starts and reports degraded health rather than failing to boot.
        else:
            self._creds = creds
            log.info("GoogleSecretaryShim: credentials loaded")

        self._started = True
        return True

    def stop(self) -> bool:
        self._creds = None
        self._started = False
        log.info("GoogleSecretaryShim: stopped")
        return True

    def restart(self) -> bool:
        return self.stop() and self.start()

    def self_test(self) -> dict:
        creds_path = self._creds_path()
        if not creds_path.exists():
            return {
                "passed": False,
                "details": f"credentials.json missing at {creds_path} — provide OAuth app credentials",
            }
        if self._creds is None:
            return {
                "passed": False,
                "details": "no valid OAuth token — run start() or configure credentials.json",
            }
        if not self._creds.valid:
            return {"passed": False, "details": "OAuth token expired and refresh failed"}
        return {"passed": True, "details": "OAuth credentials valid and ready"}

    def rollback(self) -> None:
        self._creds = None
        self._started = False
        log.info("GoogleSecretaryShim: rollback complete")
