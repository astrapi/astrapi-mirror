"""astrapi_mirror._app – ASGI-App-Factory.

Start:
    uvicorn astrapi_mirror._app:app
    astrapi-mirror --work-dir /opt/astrapi-mirror --port 5002
"""
import time

from astrapi_core.system.paths import configure as _configure_paths
_configure_paths("astrapi-mirror")

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from astrapi_core.ui import create as create_ui
from astrapi_core.ui.module_registry import load_modules
from astrapi_core.ui.settings_registry import init as settings_init
from astrapi_core.system.health import register_health
from astrapi_core.system.systemd import sd_notify, start_watchdog
from astrapi_core.system.version import get_display_name
from astrapi_core.modules.settings.engine import configure as configure_settings
from astrapi_core.modules.system.updater import configure as configure_updater

from astrapi_mirror._paths import package_dir, work_dir, db_path
from astrapi_mirror.api.fastapi_app import create as create_api

_START_TIME = time.time()


def _db_check() -> tuple[bool, dict]:
    from astrapi_core.system.db import _conn
    try:
        _conn().execute("SELECT 1").fetchone()
        return True, {"db": True}
    except Exception:
        return False, {"db": False}


def create_app() -> FastAPI:
    _pkg = package_dir()
    configure_settings(health_fn=_db_check, app_name=get_display_name(_pkg))
    configure_updater(_pkg)

    from astrapi_core.system.db import configure as _configure_db, create_all_registered_tables
    _configure_db(db_path())
    create_all_registered_tables()

    settings_init(work_dir())

    modules, _ = load_modules(_pkg)
    api = create_api(modules=modules)

    import astrapi_core.ui
    from pathlib import Path
    core_static = Path(astrapi_core.ui.__file__).parent / "static"
    api.mount("/static", StaticFiles(directory=str(core_static)), name="static")

    create_ui(api, app_root=_pkg, modules=modules)

    register_health(api, check_fn=_db_check, start_time=_START_TIME)
    start_watchdog(check_fn=lambda: _db_check()[0])
    sd_notify("READY=1")
    return api


app = create_app()
