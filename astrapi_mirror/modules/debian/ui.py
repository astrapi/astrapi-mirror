"""astrapi_mirror.modules.debian.ui – UI-Router für das Debian-Modul."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from astrapi_core.ui.render import render
from astrapi_core.ui.crud_blueprint import make_crud_router

from .storage import store, KEY

_DIR = Path(__file__).parent

router = make_crud_router(
    store,
    KEY,
    schema_path=str(_DIR / "schema.yaml"),
    label="Debian-Repository",
    description_field="label",
    has_toggle=True,
    has_status=True,
)


# ---------------------------------------------------------------------------
# Sync-Action (gibt aktualisierten Listeneintrag zurück)
# ---------------------------------------------------------------------------

@router.post(f"/ui/{KEY}/{{repo_id}}/sync", response_class=HTMLResponse)
def ui_sync_repo(repo_id: str, request: Request):
    from .jobs import sync_repo_async
    from .storage import store as _store
    _store.upsert(repo_id, {"last_status": "syncing"})
    sync_repo_async(repo_id)
    return render(request, "content.html", {
        "cfg": _store.list(),
        "module": KEY,
        "container_id": f"tab-{KEY}",
        "loading_id": f"{KEY}-loading",
        "content_template": f"{KEY}/partials/card_body.html",
        "running": {},
        "has_run_buttons": False,
        "has_status": True,
        "has_toggle": True,
    })


# ---------------------------------------------------------------------------
# Alle Repos syncen (Page-Action)
# ---------------------------------------------------------------------------

@router.post(f"/ui/{KEY}/sync-all", response_class=HTMLResponse)
def ui_sync_all(request: Request):
    from .jobs import sync_all_async
    sync_all_async()
    repos = store.list()
    return render(request, f"{KEY}/content.html", {
        "cfg": repos,
        "module": KEY,
        "sync_running": True,
    })


# ---------------------------------------------------------------------------
# sources.list Modal
# ---------------------------------------------------------------------------

@router.get(f"/ui/{KEY}/{{repo_id}}/sources-list", response_class=HTMLResponse)
def ui_sources_list(repo_id: str, request: Request):
    data = store.get(repo_id) or {}
    from .engine import client_sources_file
    base_url = str(request.base_url).rstrip("/")
    snippet = client_sources_file({"id": repo_id, **data}, base_url)
    gpg_url = f"{base_url}/repo/debian/{repo_id}.gpg" if data.get("gpg_key") else None
    return render(request, f"{KEY}/modals/sources_list.html", {
        "repo_id": repo_id,
        "label": data.get("label") or repo_id,
        "snippet": snippet,
        "filename": f"{repo_id}.sources",
        "gpg_url": gpg_url,
    })


# ---------------------------------------------------------------------------
# Validierungs-Modal
# ---------------------------------------------------------------------------

@router.get(f"/ui/{KEY}/{{repo_id}}/validate", response_class=HTMLResponse)
def ui_validate(repo_id: str, request: Request):
    data = store.get(repo_id) or {}
    from .engine import validate_repo
    result = validate_repo({"id": repo_id, **data})
    return render(request, f"{KEY}/modals/validate.html", {
        "repo_id": repo_id,
        "label": data.get("label") or repo_id,
        "result": result,
    })


# ---------------------------------------------------------------------------
# Log-Modal
# ---------------------------------------------------------------------------

@router.get(f"/ui/{KEY}/{{repo_id}}/log", response_class=HTMLResponse)
def ui_log(repo_id: str, request: Request):
    data = store.get(repo_id) or {}
    issues = data.get("last_sync_issues") or []
    return render(request, f"{KEY}/modals/log.html", {
        "repo_id": repo_id,
        "label": data.get("label") or repo_id,
        "last_run": data.get("last_run", "—"),
        "last_status": data.get("last_status", "—"),
        "issues": issues,
    })
