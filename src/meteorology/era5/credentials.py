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
    """Explicit alias file > CDSAPI_KEY/URL > CDSAPI_RC or ~/.cdsapirc.

    Planning reports mechanism/presence without opening secret files. Execution
    resolves that same mechanism; only the official CDS endpoint is supported.
    """
    rc = Path(os.environ.get("CDSAPI_RC", str(Path.home() / ".cdsapirc"))).expanduser()
    key = os.environ.get("CDSAPI_KEY")
    endpoint = os.environ.get("CDSAPI_URL", API).rstrip("/")
    if env_file is not None:
        rc = Path(env_file).expanduser()
        mechanism = "explicit_alias_file"
        key = None
        if not inspect_only:
            try:
                entries = [
                    line.split("=", 1)[1].strip().strip("\"'")
                    for line in rc.read_text().splitlines()
                    if line.strip().startswith("COPERNICUS_API_KEY=")
                ]
            except OSError:
                raise ValueError(
                    "Cannot read explicit ERA5 credential file; path/details redacted."
                ) from None
            if len(entries) != 1:
                raise ValueError("Exactly one COPERNICUS_API_KEY alias required.")
            key = entries[0]
    elif key:
        mechanism = "CDSAPI_KEY"
    else:
        mechanism = "CDSAPI_RC" if "CDSAPI_RC" in os.environ else "default_cdsapirc"
        if rc.is_file() and not inspect_only:
            try:
                value = yaml.safe_load(rc.read_text())
                key = value.get("key")
                endpoint = os.environ.get("CDSAPI_URL", value.get("url", API)).rstrip("/")
            except Exception:
                raise ValueError(
                    "Malformed ERA5 credential configuration; details redacted."
                ) from None
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
    return Credential(key, endpoint, mechanism, rc.is_file())
