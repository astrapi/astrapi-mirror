"""astrapi_mirror.api.fastapi_app – FastAPI-Factory."""
from fastapi import FastAPI
from astrapi_core.system.version import get_app_version

from astrapi_mirror._paths import package_dir

APP_ROOT = package_dir()


def create(modules: list | None = None) -> FastAPI:
    _version = get_app_version(APP_ROOT, default="1.0.0")
    app = FastAPI(
        title="Mirror Control API",
        version=_version,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    from astrapi_core.ui.module_registry import load_modules, register_fastapi_modules
    if modules is None:
        modules, _ = load_modules(APP_ROOT)
    register_fastapi_modules(app, modules)

    # repo_router (Datei-Ausgabe auf der Wurzel, /{os_type}/...) wird
    # bewusst NICHT hier eingehaengt, sondern erst ganz am Ende von
    # _app.py::create_app() -- Starlette matcht Routen in
    # Registrierungsreihenfolge, nicht nach Spezifitaet. repo_router
    # registriert u.a. den generischen Catch-all "/{os_type}/{repo_id}/
    # {path:path}", der sonst noch vor spezifischeren Routen wie /health
    # oder den Dashboard-Seiten (/archlinux, /debian, ...) gewinnen und
    # sie unerreichbar machen wuerde.
    return app
