import pytest
from meteorology.credentials import resolve_credentials, API


def test_precedence_presence_and_secret_redaction(tmp_path, monkeypatch):
    rc = tmp_path / "rc.fixture"
    rc.write_text("url: " + API + "\nkey: RC_SENTINEL\n")
    explicit = tmp_path / "alias.fixture"
    explicit.write_text("COPERNICUS_API_KEY=ALIAS_SENTINEL\n")
    monkeypatch.setenv("CDSAPI_RC", str(rc))
    monkeypatch.delenv("CDSAPI_KEY", raising=False)
    monkeypatch.delenv("CDSAPI_URL", raising=False)
    assert resolve_credentials(required=True).key == "RC_SENTINEL"
    monkeypatch.setenv("CDSAPI_KEY", "ENV_SENTINEL")
    assert resolve_credentials(required=True).key == "ENV_SENTINEL"
    selected = resolve_credentials(env_file=explicit, required=True)
    assert selected.key == "ALIAS_SENTINEL"
    assert "SENTINEL" not in repr(selected) and "SENTINEL" not in str(selected.public())
    assert selected.public()["authenticated_access"] == "not_checked"
    explicit.write_text("malformed secret should not be read while planning")
    preview = resolve_credentials(env_file=explicit, inspect_only=True)
    assert preview.public()["configuration_present"] is True
    assert preview.key is None
    with pytest.raises(ValueError, match="Exactly one"):
        resolve_credentials(env_file=explicit, required=True)


def test_bad_endpoint_and_missing_token_are_actionable(tmp_path, monkeypatch):
    monkeypatch.setenv("CDSAPI_RC", str(tmp_path / "absent"))
    monkeypatch.delenv("CDSAPI_KEY", raising=False)
    monkeypatch.delenv("CDSAPI_URL", raising=False)
    with pytest.raises(ValueError, match="Configure CDSAPI_KEY"):
        resolve_credentials(required=True)
    monkeypatch.setenv("CDSAPI_URL", "https://unqualified.example/SECRET_SENTINEL")
    with pytest.raises(ValueError) as exc:
        resolve_credentials(required=True)
    assert "SECRET_SENTINEL" not in str(exc.value)
