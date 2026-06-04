"""astrapi_mirror.modules.debian.api – JSON-API-Router für Debian."""

from astrapi_core.ui.crud_router import make_crud_router as make_json_crud_router
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from . import KEY, RepoIn, store

# Generischer CRUD-Router (erstellt GET, POST, PUT, DELETE auf /{repo_id})
crud_router = make_json_crud_router(
    store,
    KEY,
    RepoIn,
)
router = APIRouter()


@router.post("/sync-all", summary="Alle Repos syncen")
def api_sync_all():
    from .jobs import sync_all_async

    sync_all_async()
    return JSONResponse({"status": "syncing"}, status_code=202)


@router.post("/{repo_id}/sync", summary="Einzelnes Repo syncen")
def api_sync_repo(repo_id: str):
    if not store.get(repo_id):
        raise HTTPException(404, "Nicht gefunden")
    from .jobs import sync_repo_async

    sync_repo_async(repo_id)
    return JSONResponse({"status": "syncing", "repo_id": repo_id}, status_code=202)


@router.get("/{repo_id}/validate", summary="Repo validieren")
def api_validate(repo_id: str):
    data = store.get(repo_id)
    if not data:
        raise HTTPException(404, "Nicht gefunden")
    from .engine import validate_repo

    return validate_repo(data)


@router.get(
    "/{repo_id}/sources-list",
    response_class=PlainTextResponse,
    summary="apt sources.list-Snippet",
)
def api_sources_list(repo_id: str, request: Request):
    data = store.get(repo_id)
    if not data:
        raise HTTPException(404, "Nicht gefunden")
    from .engine import client_sources_file

    base_url = str(request.base_url).rstrip("/")
    return client_sources_file(data, base_url)


@router.get(
    "/refrapt-config",
    response_class=PlainTextResponse,
    summary="Generierte refrapt.conf anzeigen",
)
def api_refrapt_config():
    from .engine import generate_refrapt_config

    repos = [{"id": k, **v} for k, v in store.list().items()]
    return generate_refrapt_config(repos)


# Registriere CRUD-Router (GET /debian, POST /debian, PUT /debian/{id}, DELETE /debian/{id})
router.include_router(crud_router)
