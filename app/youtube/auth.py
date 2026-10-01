"""Google OAuth for YouTube Data and Analytics APIs."""

import base64
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from app.config import get_youtube_callback_uri
from app.youtube.repository import (
    consume_oauth_state,
    create_oauth_state,
    get_encrypted_credentials,
    save_encrypted_credentials,
    set_credential_status,
)

SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
)


class YouTubeAuthError(RuntimeError):
    """Raised when Google OAuth configuration or credentials are unusable."""


class GoogleOAuthService:
    """Manage Google OAuth without returning or logging bearer credentials."""

    def __init__(self, database_path: str | None = None) -> None:
        self.database_path = database_path

    @staticmethod
    def missing_configuration() -> list[str]:
        required = (
            "GOOGLE_CLIENT_ID",
            "GOOGLE_CLIENT_SECRET",
            "GOOGLE_TOKEN_ENCRYPTION_KEY",
            "MOTIVATIONOS_ADMIN_TOKEN",
        )
        return [name for name in required if not os.getenv(name, "").strip()]

    def status(self) -> dict[str, Any]:
        missing = self.missing_configuration()
        saved = get_encrypted_credentials(self.database_path)
        state = saved["status"] if saved else "disconnected"
        if missing and state == "disconnected":
            state = "not_configured"
        return {
            "status": state,
            "configured": not missing,
            "missing_configuration": missing,
            "updated_at": saved["updated_at"] if saved else None,
            "error": saved["error"] if saved else None,
            "scopes": list(SCOPES),
        }

    def _fernet(self) -> Fernet:
        key = os.getenv("GOOGLE_TOKEN_ENCRYPTION_KEY", "").strip()
        if not key:
            raise YouTubeAuthError("Google token encryption is not configured.")
        try:
            return Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as error:
            raise YouTubeAuthError("GOOGLE_TOKEN_ENCRYPTION_KEY must be a valid Fernet key.") from error

    def encrypt_persistent_secret(self, value: str) -> str:
        return self._fernet().encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt_persistent_secret(self, value: str) -> str:
        try:
            return self._fernet().decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError) as error:
            raise YouTubeAuthError("Stored YouTube upload session is unavailable.") from error

    def _client_config(self) -> dict[str, Any]:
        missing = [
            key
            for key in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")
            if not os.getenv(key, "").strip()
        ]
        if missing:
            raise YouTubeAuthError(f"Google OAuth is not configured: {', '.join(missing)}.")
        try:
            redirect_uri = get_youtube_callback_uri()
        except ValueError as error:
            raise YouTubeAuthError(str(error)) from error
        return {
            "web": {
                "client_id": os.environ["GOOGLE_CLIENT_ID"].strip(),
                "client_secret": os.environ["GOOGLE_CLIENT_SECRET"].strip(),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        }

    def start_authorization(self) -> dict[str, str]:
        fernet = self._fernet()
        client_config = self._client_config()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        challenge = challenge.rstrip(b"=").decode("ascii")
        flow = Flow.from_client_config(client_config, scopes=SCOPES, state=state)
        flow.redirect_uri = client_config["web"]["redirect_uris"][0]
        authorization_url, returned_state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            code_challenge=challenge,
            code_challenge_method="S256",
        )
        if returned_state != state:
            raise YouTubeAuthError("Google OAuth state setup failed.")
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        state_hash = hashlib.sha256(state.encode()).hexdigest()
        create_oauth_state(
            state_hash,
            fernet.encrypt(verifier.encode()).decode("ascii"),
            expires_at,
            self.database_path,
        )
        return {"authorization_url": authorization_url, "expires_in_seconds": "600"}

    def complete_authorization(
        self, *, state: str | None, code: str | None, provider_error: str | None = None
    ) -> None:
        if not state:
            raise YouTubeAuthError("OAuth callback is missing its state value.")
        encrypted_verifier = consume_oauth_state(
            hashlib.sha256(state.encode()).hexdigest(), self.database_path
        )
        if encrypted_verifier is None:
            raise YouTubeAuthError("OAuth state is invalid, expired, or already used.")
        if provider_error:
            raise YouTubeAuthError("Google authorization was denied or cancelled.")
        if not code:
            raise YouTubeAuthError("Google OAuth callback is missing its authorization code.")
        try:
            verifier = self._fernet().decrypt(encrypted_verifier.encode()).decode("ascii")
        except (InvalidToken, UnicodeDecodeError) as error:
            raise YouTubeAuthError("The pending OAuth authorization expired; start again.") from error
        client_config = self._client_config()
        flow = Flow.from_client_config(client_config, scopes=SCOPES, state=state)
        flow.redirect_uri = client_config["web"]["redirect_uris"][0]
        try:
            flow.fetch_token(code=code, code_verifier=verifier)
        except Exception as error:
            raise YouTubeAuthError("Google authorization could not be completed; start again.") from error
        credentials = flow.credentials
        if not credentials.refresh_token:
            raise YouTubeAuthError(
                "Google did not issue a refresh token. Revoke this app's access and authorize again."
            )
        self._save_credentials(credentials)

    def authorized_credentials(self) -> Credentials:
        row = get_encrypted_credentials(self.database_path)
        if row is None or row["status"] != "connected":
            raise YouTubeAuthError("YouTube is not connected. Complete Google OAuth first.")
        try:
            decrypted = self._fernet().decrypt(row["encrypted_token"].encode()).decode("utf-8")
            token_data = json.loads(decrypted)
            credentials = Credentials.from_authorized_user_info(token_data, scopes=SCOPES)
        except (InvalidToken, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            set_credential_status(
                "reauth_required", "Stored Google credentials cannot be decrypted.", self.database_path
            )
            raise YouTubeAuthError("Stored YouTube credentials are unavailable; authorize again.") from error
        if credentials.expired:
            if not credentials.refresh_token:
                self._require_reauthorization("Google credentials expired without a refresh token.")
            try:
                credentials.refresh(Request())
            except RefreshError as error:
                self._require_reauthorization("Google credentials were revoked or can no longer refresh.")
                raise YouTubeAuthError("Google authorization expired or was revoked; authorize again.") from error
            except Exception as error:
                raise YouTubeAuthError("Google token refresh could not reach the authorization service; retry later.") from error
            self._save_credentials(credentials)
        if not credentials.valid:
            self._require_reauthorization("Google credentials are not valid.")
        return credentials

    def _save_credentials(self, credentials: Credentials) -> None:
        token_json = credentials.to_json()
        encrypted = self._fernet().encrypt(token_json.encode("utf-8")).decode("ascii")
        save_encrypted_credentials(encrypted, self.database_path)

    def _require_reauthorization(self, error: str) -> None:
        set_credential_status("reauth_required", error, self.database_path)
        raise YouTubeAuthError("Google authorization expired or was revoked; authorize again.")

    def mark_reauthorization_required(self) -> None:
        set_credential_status(
            "reauth_required", "Google rejected the stored authorization.", self.database_path
        )