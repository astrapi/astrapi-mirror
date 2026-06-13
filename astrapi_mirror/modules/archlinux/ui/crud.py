"""astrapi_mirror.modules.archlinux.ui.crud – UI-Router für das Archlinux-Modul."""

from pathlib import Path

from astrapi_core.ui.crud_blueprint import make_crud_router
from astrapi_core.ui.render import render
from fastapi import Request
from fastapi.responses import HTMLResponse

from .. import KEY, store

_DIR = Path(__file__).parent.parent  # modules/archlinux/


class _LabelDescStore:
    """Thin wrapper: injects description=label so col-name renders the label."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def list(self, **kwargs):
        raw = self._inner.list(**kwargs)
        result = {}
        for k, v in raw.items():
            count = len(v.get("mirror_urls") or [])
            label = f"{count} Mirror" if count == 1 else f"{count} Mirrors"
            result[k] = {**v, "description": v.get("label", k), "mirror_count": label}
        return result


router = make_crud_router(
    _LabelDescStore(store),
    KEY,
    schema_path=str(_DIR / "config" / "schema.yaml"),
    label="Arch Linux Repository",
    description_field="label",
    has_run_buttons=True,
    has_toggle=True,
    has_status=True,
)


# ---------------------------------------------------------------------------
# Sync-Action
# ---------------------------------------------------------------------------


@router.post(f"/ui/{KEY}/{{repo_id}}/sync", response_class=HTMLResponse)
def ui_sync_repo(repo_id: str, request: Request):
    from ..jobs import sync_repo_async

    store.upsert(repo_id, {"last_status": "syncing"})
    sync_repo_async(repo_id)

    item_data = store.get(repo_id) or {}
    return render(
        request,
        "partials/row_single.html",
        {
            "item_name": repo_id,
            "item_data": item_data,
            "module": KEY,
            "container_id": f"mod-{KEY}",
            "loading_id": f"{KEY}-loading",
            "running": {},
        },
    )


# ---------------------------------------------------------------------------
# Sync-All-Action
# ---------------------------------------------------------------------------


@router.post(f"/ui/{KEY}/sync-all", response_class=HTMLResponse)
def ui_sync_all(request: Request):
    from ..jobs import sync_all_async

    sync_all_async()
    return render(
        request,
        "content.html",
        {
            "cfg": store.list(),
            "module": KEY,
            "container_id": f"mod-{KEY}",
            "loading_id": f"{KEY}-loading",
        },
    )


# ---------------------------------------------------------------------------
# Validate-Action
# ---------------------------------------------------------------------------


@router.get(f"/ui/{KEY}/{{repo_id}}/validate", response_class=HTMLResponse)
def ui_validate_repo(repo_id: str, request: Request):
    from .._sync_engine import validate_repo

    item = store.get(repo_id)
    if not item:
        return "<p>Nicht gefunden</p>"

    validation = validate_repo({"id": repo_id, **item})

    return render(
        request,
        f"{KEY}/dialogs/validate/modal.html",
        {
            "item": item,
            "item_id": repo_id,
            "validation": validation,
        },
    )


# ---------------------------------------------------------------------------
# Sources-Snippet-Action
# ---------------------------------------------------------------------------


@router.get(f"/ui/{KEY}/{{repo_id}}/sources-snippet", response_class=HTMLResponse)
def ui_sources_snippet(repo_id: str, request: Request):
    from .._sync_engine.engine import client_pacman_snippet

    item = store.get(repo_id)
    if not item:
        return "<p>Nicht gefunden</p>"

    base_url = str(request.base_url).rstrip("/")
    snippet = client_pacman_snippet(item, base_url)

    return render(
        request,
        f"{KEY}/dialogs/sources-snippet/modal.html",
        {
            "item": item,
            "item_id": repo_id,
            "base_url": base_url,
        },
    )


# ---------------------------------------------------------------------------
# Log-Action
# ---------------------------------------------------------------------------


@router.get(f"/ui/{KEY}/{{repo_id}}/log", response_class=HTMLResponse)
def ui_log_repo(repo_id: str, request: Request):
    item = store.get(repo_id)
    if not item:
        return "<p>Nicht gefunden</p>"

    return render(
        request,
        f"{KEY}/dialogs/log/modal.html",
        {
            "item": item,
            "item_id": repo_id,
        },
    )
