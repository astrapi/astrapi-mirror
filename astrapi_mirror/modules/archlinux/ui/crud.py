"""astrapi_mirror.modules.archlinux.ui – UI-Router für das Archlinux-Modul."""

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

    @staticmethod
    def _enrich(key: str, v: dict) -> dict:
        count = len(v.get("mirror_urls") or [])
        info = v.get("last_info") or {}
        return {
            **v,
            "description": v.get("label", key),
            "mirror_count": f"{count} Mirror" if count == 1 else f"{count} Mirrors",
            "info_pkg_count": str(info["pkg_count"]) if info.get("pkg_count") else "—",
            "info_size": info.get("total_size_fmt") or "—",
        }

    def list(self, **kwargs):
        raw = self._inner.list(**kwargs)
        return {k: self._enrich(k, v) for k, v in raw.items()}

    def get_enriched(self, item_id) -> dict:
        raw = self._inner.get(item_id) or {}
        return self._enrich(str(item_id), raw)


_wrapped_store = _LabelDescStore(store)

router = make_crud_router(
    _wrapped_store,
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

    return render(
        request,
        "partials/row_single.html",
        {
            "item_name": repo_id,
            "item_data": _wrapped_store.get_enriched(repo_id),
            "module": KEY,
            "container_id": f"mod-{KEY}",
            "loading_id": f"{KEY}-loading",
            "running": {f"{KEY}:{repo_id}": True},
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
# Info-Modal
# ---------------------------------------------------------------------------


@router.get(f"/ui/{KEY}/{{repo_id}}/info", response_class=HTMLResponse)
def ui_info_repo(repo_id: str, request: Request):
    item = store.get(repo_id) or {}
    return render(
        request,
        f"{KEY}/dialogs/info/modal.html",
        {
            "item": item,
            "item_id": repo_id,
            "label": item.get("label") or repo_id,
            "last_run": item.get("last_run") or "—",
            "last_status": item.get("last_status") or "neu",
            "issues": item.get("last_sync_issues") or [],
            "info": item.get("last_info") or {},
        },
    )


# ---------------------------------------------------------------------------
# Log-Action
# ---------------------------------------------------------------------------


@router.get(f"/ui/{KEY}/{{repo_id}}/log", response_class=HTMLResponse)
def ui_log_repo(repo_id: str, request: Request):
    data = store.get(repo_id) or {}
    return render(
        request,
        f"{KEY}/dialogs/log/modal.html",
        {
            "repo_id": repo_id,
            "label": data.get("label") or repo_id,
            "last_run": data.get("last_run") or "—",
            "last_status": data.get("last_status") or "neu",
            "issues": data.get("last_sync_issues") or [],
        },
    )
