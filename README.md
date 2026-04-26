# astrapi-mirror

Web-UI zur Verwaltung und Spiegelung von Linux-Paketrepositorys.
Synchronisiert Repos via **refrapt** (Subprocess), prüft Vollständigkeit nach dem Sync
und stellt den Mirror unter `/repo/<os>/` bereit. Caddy terminiert TLS extern.

Aufgebaut auf **astrapi-core** (FastAPI + HTMX + Jinja2).

## Stack

| Komponente | Details |
|---|---|
| Framework | astrapi-core (FastAPI + HTMX) |
| Sync-Tool | refrapt ≥ 0.4.10 |
| Persistenz | SQLite (YamlStorage via astrapi-core) |
| Port | 5002 (Standard) |
| Python | ≥ 3.11 |

## Voraussetzungen

```bash
pip install refrapt
```

## Setup (Entwicklung)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt   # enthält -e ../astrapi-core
pip install -e .
```

## Starten

```bash
astrapi-mirror --work-dir data --port 5002
```

**Mit Auto-Reload (Entwicklung):**

```bash
astrapi-mirror --work-dir data --port 5002 --reload
```

| Parameter | Standard | Beschreibung |
|---|---|---|
| `--work-dir` | (Pflicht) | Datenpfad für SQLite-DB, Mirror, Skel, Var |
| `--port` | `5002` | HTTP-Port |
| `--host` | `0.0.0.0` | Bind-Adresse |
| `--reload` | – | Auto-Reload bei Dateiänderungen |

Die Web-Oberfläche ist danach erreichbar unter: `http://localhost:5002`

## Modul: Debian

Verwaltet Debian-Repository-Einträge und synchronisiert sie via refrapt.

- **Auto-Seed:** 30 vorkonfigurierte Repos beim ersten Start (Debian, Proxmox, Docker, PostgreSQL, …)
- **Validierung:** prüft InRelease-Dateien + Dateigrößen nach dem Sync
- **sources.list-Snippet:** wird pro Repo generiert und kann kopiert werden

### Client-Konfiguration

```
deb https://mirror.example.com/repo/debian/deb.debian.org/debian bookworm main contrib
```

## Projektstruktur

```
astrapi_mirror/
├── _cli.py                  # Einstiegspunkt (CLI)
├── _app.py                  # ASGI-App-Factory
├── _paths.py                # Pfad-Utilities
├── api/
│   ├── fastapi_app.py       # FastAPI-Factory
│   └── repo.py              # /repo/debian/ – File-Serving + Directory-Listing
└── modules/
    └── debian/              # Debian-Repo-Verwaltung
```

## Erweiterung um weitere OS-Typen

Neues Modul `astrapi_mirror/modules/rpm/` (oder `alpine/`) anlegen –
eigenes Sync-Tool, eigene Routen unter `/repo/rpm/`, eigenes Brand-Icon.
Kein Framework-Umbau nötig.

