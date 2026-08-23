"""astrapi_mirror._app – ASGI-App-Factory.

Start:
    uvicorn astrapi_mirror._app:app
    astrapi-mirror --work-dir /opt/astrapi-mirror --port 5002
"""

import time

from astrapi_core.system.paths import configure as _configure_paths

_configure_paths("astrapi-mirror")

from astrapi_core.modules.settings.engine import configure as configure_settings
from astrapi_core.modules.system.engine import configure_updater
from astrapi_core.system.health import register_health
from astrapi_core.system.systemd import sd_notify, start_watchdog
from astrapi_core.system.version import get_display_name
from astrapi_core.ui import create as create_ui
from astrapi_core.ui.module_registry import load_modules
from astrapi_core.ui.settings_registry import init as settings_init
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from astrapi_mirror._paths import db_path, package_dir, work_dir
from astrapi_mirror.api.fastapi_app import create as create_api

_START_TIME = time.time()


def _db_check() -> tuple[bool, dict]:
    from astrapi_core.system.db import _conn

    try:
        _conn().execute("SELECT 1").fetchone()
        return True, {"db": True}
    except Exception:
        return False, {"db": False}


def _migrate_debian_url_to_mirror_urls() -> None:
    """Uebernimmt das alte `url`-Feld in `mirror_urls` (T-186-MIRROR).

    Debian hatte bisher ein separates "Primaere URL"-Feld zusaetzlich zur
    mirror_urls-Liste -- inkonsistent zu archlinux, das seit einem
    Refactor im Juni nur noch die reine Liste kennt (Architektur ist Teil
    der URL, ein Repo kann mehrere Architekturen ueber mehrere
    Liste-Eintraege abdecken). Debian wird jetzt angeglichen: das `url`-
    Feld wird aus dem Formular/der Sync-Logik entfernt. Damit bestehende
    Repos (die praktisch alle nur `url` gesetzt hatten, `mirror_urls` war
    optionaler Fallback) nicht ohne Quelle dastehen, wird `url` hier
    einmalig als erster Eintrag in `mirror_urls` uebernommen, falls dort
    noch nichts steht. Idempotent -- greift nur wenn mirror_urls leer und
    url gesetzt ist.
    """
    from astrapi_core.system.db import _conn

    con = _conn()
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(debian_repos)")]
        if "url" not in cols:
            return
        rows = con.execute(
            "SELECT id, url, mirror_urls FROM debian_repos WHERE url != '' AND mirror_urls = ''"
        ).fetchall()
        for repo_id, url, _mirror_urls in rows:
            con.execute("UPDATE debian_repos SET mirror_urls = ? WHERE id = ?", (url, repo_id))
        if rows:
            con.commit()
    except Exception:
        pass


def create_app() -> FastAPI:
    _pkg = package_dir()
    configure_settings(health_fn=_db_check, app_name=get_display_name(_pkg))
    configure_updater(_pkg)

    from astrapi_core.system.db import configure as _configure_db
    from astrapi_core.system.db import create_all_registered_tables

    _configure_db(db_path())
    create_all_registered_tables()

    settings_init(work_dir())

    modules, _ = load_modules(_pkg)
    _migrate_debian_url_to_mirror_urls()
    api = create_api(modules=modules)

    from pathlib import Path

    import astrapi_core.ui

    core_static = Path(astrapi_core.ui.__file__).parent / "static"
    api.mount("/static", StaticFiles(directory=str(core_static)), name="static")

    create_ui(api, app_root=_pkg, modules=modules)

    register_health(api, check_fn=_db_check, start_time=_START_TIME)
    start_watchdog(check_fn=lambda: _db_check()[0])
    sd_notify("READY=1")
    return api


app = create_app()
