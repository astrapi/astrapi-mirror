# astrapi-mirror – Projektkontext für GitHub Copilot

Wird im Repo versioniert und von VS Code Copilot automatisch geladen.

---

## Was ist astrapi-mirror?

Web-UI zur Verwaltung und Spiegelung von Linux-Paketrepositorys, aufgebaut auf **astrapi-core**.
Synchronisiert Repos via **refrapt** (Subprocess), prüft Vollständigkeit nach dem Sync
und stellt den Mirror unter `/repo/<os>/` bereit. Caddy terminiert TLS extern.

Erster OS-Typ: **Debian** (`/repo/debian/`). Architektur erlaubt spätere Erweiterung
um RPM, Alpine o.ä. als eigene Module.

---

## Stack

| Komponente | Details |
|---|---|
| Basis | astrapi-core (FastAPI + HTMX + Jinja2) |
| Sync-Tool | refrapt ≥ 0.4.10 (Subprocess: `refrapt --conf <tempfile>`) |
| Persistenz | SQLite via `YamlStorage` (astrapi-core) |
| Serving | Custom FastAPI-Router `/repo/debian/{path:path}` mit Directory-Listing |
| Port | 5002 (Standard) |
| Python | ≥ 3.11 |

---

## Verzeichnisstruktur

```
astrapi_mirror/
├── _app.py                  # ASGI-App-Factory (analog astrapi-packages)
├── _cli.py                  # Console-Script: astrapi-mirror --work-dir … --port 5002
├── _paths.py                # mirror_path(), skel_path(), var_path() – aus Settings-Registry
├── app.yaml                 # version, name, display_name
├── config.yaml              # {"app": {"name": "astrapi-mirror", "lang": "de"}}
├── navigation.yaml          # Modulreihenfolge im Menü
├── api/
│   ├── fastapi_app.py       # FastAPI-Factory (registriert Module + repo-Router)
│   └── repo.py              # /repo/ + /repo/debian/{path:path} – File-Serving + Listing
└── modules/
    └── debian/
        ├── __init__.py      # load_modul() + auto_seed() + scheduler register_action
        ├── _seed.py         # 30 initiale Repos aus refrapt-Config (wird bei leerem Store eingespielt)
        ├── api.py           # CRUD + /sync-all, /{id}/sync, /{id}/validate, /{id}/sources-list, /refrapt-config
        ├── ui.py            # CRUD-UI + Sync/Validate/sources.list/Log-Modals
        ├── engine.py        # Config-Generierung, Validierung (InRelease + Dateigrößen), sources.list
        ├── jobs.py          # sync_all(), sync_repo() – blockierend; *_async()-Wrapper
        ├── storage.py       # store = YamlStorage("debian")
        ├── schema.yaml      # Formularfelder (id, label, url, suites, components, architectures, …)
        ├── modul.yaml       # label, nav_group, card_actions (kein settings_button)
        ├── settings.yaml    # mirror_path, skel_path, var_path, threads, limit_rate, language, …
        ├── icon.svg         # Debian-Swirl-Logo (Simple Icons, currentColor)
        ├── icon-outline.svg # Debian-Swirl-Logo (inactive)
        └── templates/
            ├── partials/
            │   ├── card_body.html   # meta-grid: URL, Anbieter, Suites, Arch, Probleme
            │   ├── list_header.html # URL | Anbieter | Suites
            │   └── list_row.html
            └── modals/
                ├── sources_list.html  # apt sources.list-Snippet mit Copy-Button
                ├── validate.html      # Validierungsbericht (InRelease + Dateigrößen)
                └── log.html           # Letzter Sync-Status + Validierungsprobleme
```

---

## Datenmodell: Debian-Repo-Eintrag

Ein Eintrag = ein Provider/URL mit optionalen mehreren Suites.
Erzeugt beim Config-Export N `deb`-Zeilen in der refrapt.conf.

| Feld | Typ | Beispiel |
|---|---|---|
| `id` | str (Key) | `debian-bookworm` |
| `label` | text | `Debian Bookworm` |
| `provider_group` | text | `Debian`, `Proxmox`, … |
| `url` | text | `http://deb.debian.org/debian` |
| `repo_type` | select | `deb` / `deb-src` |
| `suites` | list | `["bookworm", "bookworm-updates"]` |
| `components` | list | `["main", "contrib", "non-free"]` |
| `architectures` | list | `["amd64", "arm64"]` |
| `is_flat` | boolean | `false` (Flat-Repos: keine Suite/Komponenten) |
| `enabled` | boolean | `true` |
| `last_run` | str | Sync-Zeitstempel (core-Standard) |
| `last_status` | str | `ok` / `error` / `syncing` (core-Standard) |
| `last_sync_issues` | list | Validierungsfehler (fehlende Dateien, Größen) |

---

## Sync-Ablauf

1. `engine.generate_refrapt_config(repos)` → temp. `refrapt-*.conf`
2. `refrapt --conf <tempfile>` als Subprocess (Timeout 12h, stdout → Activity-Log)
3. `engine.validate_repo()` pro Repo: parst `InRelease`, prüft alle referenzierten Dateien auf Existenz + Dateigröße
4. Status + Issues in Store speichern; Notify senden

---

## Serving unter /repo/debian/

```
GET /repo/debian/                    → Directory-Listing (Provider-Ebene)
GET /repo/debian/{host}/{path}/      → Directory-Listing
GET /repo/debian/{host}/{path}/file  → FileResponse
```

Mirror-Pfad: `{mirror_path}/{hostname}/{url-pfad}/` (refrapt-Standard-Layout)

Client-sources.list-Snippet:
```
deb https://mirror.example.com/repo/debian/deb.debian.org/debian bookworm main contrib
```

---

## Settings (Modul debian)

Gespeichert in Settings-Registry unter Modul-Key `debian`.
Kein `settings_button` in der Modul-Karte – Zugriff über globale App-Einstellungen.

| Key | Default | Bedeutung |
|---|---|---|
| `mirror_path` | `{work_dir}/mirror` | Hauptverzeichnis (groß, ext. Laufwerk) |
| `skel_path` | `{work_dir}/skel` | Index-Dateien (SSD empfohlen) |
| `var_path` | `{work_dir}/var` | Logs + Locks (SSD empfohlen) |
| `threads` | `4` | Parallele wget-Prozesse |
| `limit_rate` | `200m` | Bandbreite pro Thread (wget-Syntax) |
| `language` | `de_DE, en` | Translation-Dateien |
| `contents` | `True` | Contents-[arch].* laden |
| `no_check_cert` | `False` | SSL-Zertifikat ignorieren |

---

## Auto-Seed

`_seed.py` enthält 30 vorkonfigurierte Repos aus der produktiven refrapt-Config
(Debian, Proxmox, PostgreSQL, Docker, NodeJS, Grafana, InfluxDB, …).
Wird in `__init__.py` via `auto_seed(store)` beim ersten Start eingespielt (nur wenn Store leer).

---

## Icons

Jedes OS-Modul bringt `icon.svg` + `icon-outline.svg` mit (Brand-Logo).
- `debian/`: Offizielles Debian-Swirl (Simple Icons, `currentColor`)
- Zukünftige Module (RPM, Alpine, …): analog eigene Brand-Icons

---

## Erweiterung um weitere OS-Typen

Neues Modul `astrapi_mirror/modules/rpm/` (oder `alpine/`):
- Eigenes sync-Tool statt refrapt
- Eigene Routen unter `/repo/rpm/`
- Eigenes Brand-Icon
- Kein Framework-Umbau nötig

---

## Starten (Entwicklung)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt  # enthält -e ../astrapi-core
pip install -e .
astrapi-mirror --work-dir ./data --port 5002
```

---

## Versionsschema

CalVer identisch zu astrapi-core: `YY.MM.patch.devN`
