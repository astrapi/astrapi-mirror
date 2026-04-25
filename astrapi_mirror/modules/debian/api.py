"""astrapi_mirror.modules.debian.api – REST-API für das Debian-Modul."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from .storage import store, KEY

router = APIRouter()


class RepoIn(BaseModel):
    label: str = ""
    provider_group: str = ""
    url: str = ""
    repo_type: str = "deb"
    suites: list[str] = []
    components: list[str] = []
    architectures: list[str] = []
    is_flat: bool = False
    enabled: bool = True
    gpg_key_url: str = ""


# Standard-CRUD
from astrapi_core.ui.crud_router import make_crud_router as _make_crud
_crud = _make_crud(store, KEY, RepoIn)
router.include_router(_crud)


# ---------------------------------------------------------------------------
# Sync-Endpoints
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Validierung
# ---------------------------------------------------------------------------

@router.get("/{repo_id}/validate", summary="Repo validieren")
def api_validate(repo_id: str):
    data = store.get(repo_id)
    if not data:
        raise HTTPException(404, "Nicht gefunden")
    from .engine import validate_repo
    return validate_repo({"id": repo_id, **data})


# ---------------------------------------------------------------------------
# sources.list-Snippet
# ---------------------------------------------------------------------------

@router.get("/{repo_id}/sources-list", response_class=PlainTextResponse,
            summary="apt sources.list-Snippet")
def api_sources_list(repo_id: str, request: Request):
    data = store.get(repo_id)
    if not data:
        raise HTTPException(404, "Nicht gefunden")
    from .engine import client_sources_file
    base_url = str(request.base_url).rstrip("/")
    return client_sources_file({"id": repo_id, **data}, base_url)


# ---------------------------------------------------------------------------
# refrapt-Config (Debug)
# ---------------------------------------------------------------------------

@router.get("/refrapt-config", response_class=PlainTextResponse,
            summary="Generierte refrapt.conf anzeigen")
def api_refrapt_config():
    from .engine import generate_refrapt_config
    repos_raw = store.list()
    repos = [{"id": k, **v} for k, v in repos_raw.items()]
    return generate_refrapt_config(repos)
