"""Redacted credential resolver shared by planning and execution."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path

import yaml

API = "https://cds.climate.copernicus.eu/api"


@dataclass(frozen=True)
class Credential:
    key: str | None = field(repr=False)
    endpoint: str = API
    mechanism: str = "absent"
    file_present: bool = False

    def public(self):
        return dict(
            configuration_present=bool(self.key) or self.file_present,
            mechanism=self.mechanism,
            credential_file_present=self.file_present,
            token_environment_present=bool(os.environ.get("CDSAPI_KEY")),
            endpoint_environment_present=bool(os.environ.get("CDSAPI_URL")),
            authenticated_access="not_checked",
            dataset_terms="not_checked_manual_acceptance_required",
        )


def resolve_credentials(*, env_file=None, inspect_only=False, required=False):
    """Process environment > selected .env > CDSAPI_RC / ~/.cdsapirc.

    Tokens are never printed or injected into the process environment.
    """
    from .run_settings import read_env, TOKEN_KEYS

    rc = Path(os.environ.get("CDSAPI_RC", str(Path.home() / ".cdsapirc"))).expanduser()
    selected_file = Path(env_file).expanduser() if env_file is not None else Path.cwd()/'.env'
    key = os.environ.get('CDSAPI_KEY')
    endpoint = os.environ.get('CDSAPI_URL', API).rstrip('/')
    mechanism = 'CDSAPI_KEY' if key else 'absent'
    if inspect_only and selected_file.is_file():
        if endpoint != API:
            raise ValueError('Only the official HTTPS CDS API endpoint is supported; address redacted.')
        return Credential(key, endpoint, 'env_file', True)
    _, values = read_env(env_file, keys=TOKEN_KEYS)
    if not key:
        key = values.get('CDSAPI_KEY') or values.get('COPERNICUS_API_KEY')
        if key:
            mechanism = 'env_file'
    endpoint = os.environ.get('CDSAPI_URL', values.get('CDSAPI_URL') or API).rstrip('/')
    if not key:
        mechanism = 'CDSAPI_RC' if 'CDSAPI_RC' in os.environ else 'default_cdsapirc'
        if rc.is_file() and not inspect_only:
            try:
                value = yaml.safe_load(rc.read_text())
                key = value.get('key')
                endpoint = os.environ.get('CDSAPI_URL', values.get('CDSAPI_URL') or value.get('url', API)).rstrip('/')
            except Exception:
                raise ValueError('Malformed ERA5 credential configuration; details redacted.') from None
    if endpoint != API:
        raise ValueError("Only the official HTTPS CDS API endpoint is supported; address redacted.")
    if key is not None and (
        not isinstance(key, str) or not key.strip() or "\n" in key or "\r" in key
    ):
        raise ValueError("Invalid ERA5 token; value redacted.")
    if required and not key:
        raise ValueError(
            "Configure CDSAPI_KEY or CDSAPI_RC / ~/.cdsapirc and accept ERA5 terms manually."
        )
    return Credential(key, endpoint, mechanism, selected_file.is_file() or rc.is_file())
