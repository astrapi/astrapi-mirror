"""modules/debian/_sync_engine/engine.py::client_sources_file() -- erzeugt
die DEB822 .sources-Datei, die Clients (apt) unter /etc/apt/sources.list.d/
ablegen. Regressionstest für einen Bug, der beim Umbau der Mirror-URL-
Struktur von /files/debian/... auf /debian/... (2026-09) unbemerkt blieb:
die URIs-Zeile trug das alte /files-Präfix noch weiter, wodurch jede
generierte .sources-Datei ins Leere zeigte (404 beim Client-Update)."""
from astrapi_mirror.modules.debian._sync_engine.engine import client_sources_file


def test_client_sources_file_uri_hat_kein_files_praefix():
    repo = {"slug": "caddy", "repo_type": "deb", "suites": [], "components": []}
    content = client_sources_file(repo, "https://mirror.simpsons.lan")
    assert "/files/" not in content


def test_client_sources_file_uri_zeigt_auf_debian_wurzel():
    repo = {"slug": "caddy", "repo_type": "deb", "suites": [], "components": []}
    content = client_sources_file(repo, "https://mirror.simpsons.lan")
    assert "URIs: https://mirror.simpsons.lan/debian/caddy/" in content


def test_client_sources_file_mit_suites_und_components():
    repo = {
        "slug": "myrepo",
        "repo_type": "deb",
        "suites": ["stable"],
        "components": ["main"],
    }
    content = client_sources_file(repo, "https://mirror.simpsons.lan/")
    assert "URIs: https://mirror.simpsons.lan/debian/myrepo" in content
    assert "Suites: stable" in content
    assert "Components: main" in content
