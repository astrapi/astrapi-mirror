"""tests/test_button_aktionen.py

Prüft ob alle Button-Aktionen (Speichern, Bearbeiten, Toggle, Löschen,
Synchronisieren) im Debian-Modul die erwarteten HTTP-Statuscodes liefern.

Tests nutzen eine feste Test-ID und bereinigen via try/finally.
"""

_TEST_ID = "__test_button__"

_REPO_DATA = {
    "label": "Test Repo",
    "provider_group": "__test__",
    "url": "http://deb.example.com/__test__",
    "repo_type": "deb",
    "suites": ["test"],
    "components": ["main"],
    "architectures": ["amd64"],
    "enabled": True,
}


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────


def _create(client):
    return client.post(
        "/api/debian/",
        params={"item_id": _TEST_ID},
        json=_REPO_DATA,
    )


def _delete(client):
    client.delete(f"/api/debian/{_TEST_ID}")


# ── Debian ────────────────────────────────────────────────────────────────────


def test_debian_erstellen(client):
    """Speichern-Button im Erstellen-Dialog legt neues Repo an (201)."""
    try:
        resp = _create(client)
        assert resp.status_code == 201
        assert client.get(f"/api/debian/{_TEST_ID}").status_code == 200
    finally:
        _delete(client)


def test_debian_bearbeiten(client):
    """Speichern-Button im Bearbeiten-Dialog aktualisiert das Repo."""
    _create(client)
    try:
        resp = client.put(
            f"/api/debian/{_TEST_ID}",
            json={**_REPO_DATA, "label": "Test Repo Edited"},
        )
        assert resp.status_code == 200
        item = client.get(f"/api/debian/{_TEST_ID}").json()
        assert item["label"] == "Test Repo Edited"
    finally:
        _delete(client)


def test_debian_toggle(client):
    """Toggle-Button schaltet ein deaktiviertes Repo auf aktiv."""
    _create(client)
    # Zuerst deaktivieren
    client.patch(f"/api/debian/{_TEST_ID}/toggle")
    try:
        # Dann wieder aktivieren
        resp = client.patch(f"/api/debian/{_TEST_ID}/toggle")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True
    finally:
        _delete(client)


def test_debian_loeschen(client):
    """Löschen-Button (nach Bestätigung) entfernt das Repo (204)."""
    _create(client)
    resp = client.delete(f"/api/debian/{_TEST_ID}")
    assert resp.status_code == 204
    assert client.get(f"/api/debian/{_TEST_ID}").status_code == 404


def test_debian_synchronisieren(client):
    """Sync-Button startet die Repo-Synchronisation (202 Accepted)."""
    _create(client)
    try:
        resp = client.post(f"/api/debian/{_TEST_ID}/sync")
        assert resp.status_code == 202
        assert resp.json()["status"] == "syncing"
    finally:
        _delete(client)
