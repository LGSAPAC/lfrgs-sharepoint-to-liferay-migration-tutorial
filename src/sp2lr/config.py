"""Settings loaded from the environment (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines, no interpolation. Existing env wins."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class SharePointSettings:
    tenant_id: str
    client_id: str
    hostname: str
    site_url: str
    auth_mode: str = "device_code"
    cert_path: str = ""
    cert_thumbprint: str = ""

    @property
    def site_path(self) -> str:
        """Server-relative path of the site, e.g. /sites/marketing."""
        return urlparse(self.site_url).path.rstrip("/") or "/"


@dataclass(frozen=True)
class LiferaySettings:
    url: str
    user: str
    password: str
    locale: str = "en_US"


def _req(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required setting {name}. See .env.example.")
    return value


def sharepoint_settings() -> SharePointSettings:
    load_dotenv()
    site_url = _req("SP_SITE_URL")
    return SharePointSettings(
        tenant_id=_req("SP_TENANT_ID"),
        client_id=_req("SP_CLIENT_ID"),
        hostname=os.environ.get("SP_HOSTNAME") or urlparse(site_url).hostname or "",
        site_url=site_url,
        auth_mode=os.environ.get("SP_AUTH_MODE", "device_code"),
        cert_path=os.environ.get("SP_CERT_PATH", ""),
        cert_thumbprint=os.environ.get("SP_CERT_THUMBPRINT", ""),
    )


def liferay_settings() -> LiferaySettings:
    load_dotenv()
    return LiferaySettings(
        url=_req("LIFERAY_URL").rstrip("/"),
        user=_req("LIFERAY_USER"),
        password=_req("LIFERAY_PASSWORD"),
        locale=os.environ.get("LIFERAY_LOCALE", "en_US"),
    )


def out_dir() -> Path:
    load_dotenv()
    p = Path(os.environ.get("SP2LR_OUT", "./out"))
    p.mkdir(parents=True, exist_ok=True)
    return p
