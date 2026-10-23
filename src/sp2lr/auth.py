"""Microsoft Entra ID tokens for Microsoft Graph and the SharePoint REST API.

Two modes:
  * device_code  - delegated; the signed-in user must be a Site Collection Admin
                   of the source site (needed to enumerate role assignments).
  * certificate  - app-only; requires Sites.FullControl.All (or Sites.Selected
                   with "fullcontrol" granted on the site). SharePoint REST does
                   not accept app-only tokens obtained with a client secret.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import msal

from .config import SharePointSettings

# Shared by the CLI and the MCP server, wherever their working directory is
CACHE_FILE = Path(os.environ.get("SP2LR_TOKEN_CACHE", Path.home() / ".sp2lr" / "token_cache.json"))

GRAPH_SCOPES_DELEGATED = [
    "https://graph.microsoft.com/Sites.Read.All",
    "https://graph.microsoft.com/User.Read.All",
    "https://graph.microsoft.com/GroupMember.Read.All",
]


def sharepoint_scopes_delegated(hostname: str) -> list[str]:
    # Reading role assignments requires "Enumerate Permissions", which in the
    # delegated model is only granted through AllSites.FullControl. The tools in
    # this repo only ever issue GET requests against SharePoint.
    return [f"https://{hostname}/AllSites.FullControl"]


class TokenProvider:
    def __init__(self, settings: SharePointSettings, interactive: bool = True):
        self.settings = settings
        # The MCP server runs non-interactively: nobody can see a device-code
        # prompt on a stdio server, so it relies on `sp2lr login` having filled the cache.
        self.interactive = interactive
        self._cache = msal.SerializableTokenCache()
        if CACHE_FILE.exists():
            self._cache.deserialize(CACHE_FILE.read_text())
        authority = f"https://login.microsoftonline.com/{settings.tenant_id}"
        if settings.auth_mode == "certificate":
            self._app = msal.ConfidentialClientApplication(
                settings.client_id,
                authority=authority,
                client_credential={
                    "private_key": Path(settings.cert_path).read_text(),
                    "thumbprint": settings.cert_thumbprint,
                },
                token_cache=self._cache,
            )
        else:
            self._app = msal.PublicClientApplication(
                settings.client_id, authority=authority, token_cache=self._cache
            )

    def _persist(self) -> None:
        if self._cache.has_state_changed:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_text(self._cache.serialize())
            CACHE_FILE.chmod(0o600)

    def graph_token(self) -> str:
        if self.settings.auth_mode == "certificate":
            return self._app_only("https://graph.microsoft.com/.default")
        return self._delegated(GRAPH_SCOPES_DELEGATED)

    def sharepoint_token(self) -> str:
        if self.settings.auth_mode == "certificate":
            return self._app_only(f"https://{self.settings.hostname}/.default")
        return self._delegated(sharepoint_scopes_delegated(self.settings.hostname))

    def _app_only(self, scope: str) -> str:
        result = self._app.acquire_token_for_client(scopes=[scope])
        self._persist()
        return self._check(result)

    def _delegated(self, scopes: list[str]) -> str:
        accounts = self._app.get_accounts()
        result = None
        if accounts:
            result = self._app.acquire_token_silent(scopes, account=accounts[0])
        if not result and not self.interactive:
            raise RuntimeError("No cached SharePoint token. Run `sp2lr login` in a terminal first.")
        if not result:
            flow = self._app.initiate_device_flow(scopes=scopes)
            if "user_code" not in flow:
                raise SystemExit(f"Device flow failed: {flow}")
            # stderr, so the MCP stdio channel stays clean
            print(flow["message"], file=sys.stderr, flush=True)
            result = self._app.acquire_token_by_device_flow(flow)
        self._persist()
        return self._check(result)

    @staticmethod
    def _check(result: dict) -> str:
        if "access_token" not in result:
            raise SystemExit(
                f"Token request failed: {result.get('error')}: {result.get('error_description')}"
            )
        return result["access_token"]
