from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _default_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WPCLI_API_KEY", "test-api-key")
