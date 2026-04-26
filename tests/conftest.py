"""
tests/conftest.py

Session-Fixtures für alle astrapi-mirror-Tests:
- Zeigt auf das echte Projektverzeichnis (nicht tmpdir), damit Fehler in der
  vorhandenen DB erkannt werden.
- Stellt einen session-scoped FastAPI-TestClient bereit
"""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Echtes Projektverzeichnis: tests/ → Projektroot
_PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def work_dir():
    """Echtes Projektverzeichnis – damit der Test die vorhandene DB nutzt."""
    d = _PROJECT_ROOT
    (d / "data").mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture(scope="session", autouse=True)
def configure_env(work_dir):
    """Setzt alle nötigen Env-Variablen bevor die App importiert wird."""
    os.environ["ASTRAPI-MIRROR_WORK_DIR"] = str(work_dir)
    yield


@pytest.fixture(scope="session")
def client(configure_env):
    """TestClient der vollständig initialisierten App (einmal je Session)."""
    from astrapi_mirror._app import create_app

    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
