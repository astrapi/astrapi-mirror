"""astrapi_mirror.modules.archlinux.api – JSON-API-Router für Archlinux."""

import asyncio

from astrapi_core.ui.crud_router import make_crud_router as make_json_crud_router
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse

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
    from ._sync_engine.engine import validate_repo

    return validate_repo(data)


@router.get(
    "/{repo_id}/sources-snippet",
    response_class=PlainTextResponse,
    summary="pacman.conf Snippet",
)
def api_sources_snippet(repo_id: str, request: Request):
    data = store.get(repo_id)
    if not data:
        raise HTTPException(404, "Nicht gefunden")
    from ._sync_engine.engine import client_pacman_snippet

    base_url = str(request.base_url).rstrip("/")
    return client_pacman_snippet(data, base_url)


# ---------------------------------------------------------------------------
# Log-Viewer (wie astrapi-backup)
# ---------------------------------------------------------------------------


@router.get("/{repo_id}/logs", response_class=HTMLResponse)
def api_logs(repo_id: str, request: Request):
    from astrapi_core.system.activity_log import get_log_lines, list_runs_for_item
    from astrapi_core.ui.render import render

    data = store.get(repo_id) or {}
    runs = list_runs_for_item(KEY, repo_id)
    act_log_id = runs[0]["id"] if runs else None
    lines = [r["line"] for r in get_log_lines(act_log_id)] if act_log_id else []
    dates = [
        {"id": str(r["id"]), "label": r.get("started_at") or str(r["id"])}
        for r in runs
    ]
    return render(
        request,
        "partials/log_modal.html",
        {
            "module": KEY,
            "item_id": repo_id,
            "description": data.get("label") or data.get("slug") or repo_id,
            "dates": dates,
            "selected": str(act_log_id) if act_log_id else None,
            "lines": lines,
            "live": False,
        },
    )


@router.get("/{repo_id}/logs/stream")
async def api_logs_stream(repo_id: str):
    from astrapi_core.system.activity_log import (
        get_activity_log,
        get_latest_activity_log_id,
        get_log_lines,
    )

    async def _gen():
        act_log_id = None
        waited = 0.0
        while act_log_id is None and waited < 15:
            act_log_id = get_latest_activity_log_id(KEY, repo_id)
            if act_log_id is None:
                await asyncio.sleep(0.3)
                waited += 0.3

        if act_log_id is None:
            yield "event: done\ndata: \n\n"
            return

        last_id = 0
        idle_after_done = 0.0

        while True:
            for row in get_log_lines(act_log_id, after_id=last_id):
                last_id = row["id"]
                level = row["level"].lower()
                safe = row["line"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                yield f'data: <div class="log-line log-{level}">{safe}</div>\n\n'

            entry = get_activity_log(act_log_id)
            if entry and entry.get("status") == "running":
                idle_after_done = 0.0
            else:
                idle_after_done += 0.5
                if idle_after_done >= 3:
                    yield "event: done\ndata: \n\n"
                    return
            await asyncio.sleep(0.5)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{repo_id}/logs/{log_id}", response_class=HTMLResponse)
def api_log_by_id(repo_id: str, log_id: str, request: Request):
    from astrapi_core.system.activity_log import get_log_lines
    from astrapi_core.ui.render import render

    lines = [r["line"] for r in get_log_lines(int(log_id))] if log_id.isdigit() else []
    return render(request, "partials/log_content.html", {"lines": lines, "date": log_id})


# Registriere CRUD-Router (GET /archlinux, POST /archlinux, PUT /archlinux/{id}, DELETE /archlinux/{id})
router.include_router(crud_router)
