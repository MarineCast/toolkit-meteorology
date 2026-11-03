"""Never read personal run settings or credentials during offline tests."""
import pytest
from meteorology.run_settings import KEYS, TOKEN_KEYS


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in KEYS | TOKEN_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('CDSAPI_RC', str(tmp_path/'absent-credentials'))
