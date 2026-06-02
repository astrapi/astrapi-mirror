"""astrapi_mirror.modules.archlinux – Arch Linux Repository Manager."""

from pathlib import Path
from typing import Optional

from astrapi_core.ui.crud_router import make_crud_router as _make_crud
from astrapi_core.ui.module_loader import load_modul
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from .storage import ArchlinuxRepoStore

_KEY = Path(__file__).parent.name
KEY = _KEY
store = ArchlinuxRepoStore()

# Auto-seed beim Laden
try:
    from ._seed import auto_seed

    auto_seed(store)
except Exception:
    pass

# ── Pydantic-Modell ───────────────────────────────────────────────────────────


class RepoIn(BaseModel):
    label: str = ""
    url: str = ""
    architectures: list[str] = ["x86_64"]
    enabled: bool = True


# ── JSON-Router ───────────────────────────────────────────────────────────────

router = APIRouter()
router.include_router(_make_crud(store, KEY, RepoIn))


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
    "/{repo_id}/sources-snippet", response_class=PlainTextResponse, summary="pacman.conf Snippet"
)
def api_sources_snippet(repo_id: str, request: Request):
    data = store.get(repo_id)
    if not data:
        raise HTTPException(404, "Nicht gefunden")
    from ._sync_engine.engine import client_pacman_snippet

    base_url = str(request.base_url).rstrip("/")
    return client_pacman_snippet(data, base_url)


# ── UI-Router + Modul ─────────────────────────────────────────────────────────

from .ui import router as ui_router  # noqa: E402

module = load_modul(
    Path(__file__).parent,
    _KEY,
    router,
    ui_router,
)

# ── Auto-Seed ──────────────────────────────────────────────────────────────────

try:
    from ._seed import auto_seed

    auto_seed(store)
except Exception:
    pass

# ── Scheduler Registration ─────────────────────────────────────────────────────

try:
    from astrapi_core.modules.scheduler.engine import register_action

    from .jobs import sync_all

    register_action(
        f"{_KEY}.sync_all",
        "Arch Linux: Alle Repos syncen",
        sync_all,
        source=_KEY,
        source_label="Arch Linux Mirror",
    )
except Exception:
    pass
