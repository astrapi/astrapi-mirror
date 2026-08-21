"""astrapi_mirror.modules.debian.ui – UI-Router für das Debian-Modul."""

import json
from pathlib import Path

from astrapi_core.ui.crud_blueprint import make_crud_router
from astrapi_core.ui.render import render
from fastapi import Request
from fastapi.responses import HTMLResponse

from .. import KEY, store

_DIR = Path(__file__).parent.parent  # modules/debian/


class _LabelDescStore:
    """Thin wrapper: injects description=label so col-name renders the label."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    @staticmethod
    def _enrich(key: str, v: dict) -> dict:
        count = (1 if v.get("url") else 0) + len(v.get("mirror_urls") or [])
        info = v.get("last_info") or {}
        return {
            **v,
            "description": v.get("label", key),
            "mirror_count": f"{count} Quelle" if count == 1 else f"{count} Quellen",
            "info_pkg_count": str(info["pkg_count"]) if info.get("pkg_count") else "—",
            # current_size_fmt statt total_size_fmt: reagiert sofort auf
            # Pruning/Filter (T-185-MIRROR) -- total_size_fmt summiert ueber
            # alle noch aufbewahrten Sync-Generationen (Rollback-Sicherheits-
            # netz) und sinkt dadurch erst mit Verzoegerung von 2-3 Zyklen,
            # was in der Liste wie ein falscher Wert wirkt. Bleibt im
            # Detail-Dialog weiterhin sichtbar.
            "info_size": info.get("current_size_fmt") or "—",
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
    label="Debian-Repository",
    description_field="label",
    has_run_buttons=True,
    has_toggle=False,
    has_status=True,
)


# ---------------------------------------------------------------------------
# Laufende Repos – Grundlage für das Zeilen-Polling (status_oob.html)
# ---------------------------------------------------------------------------
# run_single()/run_all() (jobs.py) schreiben "running"/"pending" bereits
# zuverlaessig in den Store - kein eigenes In-Memory-Tracking noetig, anders
# als bei astrapi-backup/astrapi-packages (dort gibt es keinen Zwischenstand
# in der DB, waehrend der Job laeuft).


def _running_fn() -> dict:
    return {
        f"{KEY}:{repo_id}": item.get("last_status")
        for repo_id, item in store.list().items()
        if item.get("last_status") in ("running", "pending")
    }


# ---------------------------------------------------------------------------
# Status-Endpunkt für das Zeilen-Polling (T-159-MIRROR)
# ---------------------------------------------------------------------------


@router.get(f"/ui/{KEY}/status", response_class=HTMLResponse)
def ui_status(request: Request):
    return render(
        request,
        "partials/oob/status_oob.html",
        {
            "cfg": _wrapped_store.list(),
            "module": KEY,
            "running": _running_fn(),
        },
    )


# ---------------------------------------------------------------------------
# Sync-Action
# ---------------------------------------------------------------------------


@router.post(f"/ui/{KEY}/{{repo_id}}/sync", response_class=HTMLResponse)
def ui_sync_repo(repo_id: str, request: Request):
    from ..jobs import sync_repo_async

    store.upsert(repo_id, {"last_status": "running"})
    sync_repo_async(repo_id)

    row_html = render(
        request,
        "partials/lists/row_single.html",
        {
            "item_name": repo_id,
            "item_data": _wrapped_store.get_enriched(repo_id),
            "module": KEY,
            "container_id": f"mod-{KEY}",
            "loading_id": f"{KEY}-loading",
            "running": _running_fn(),
        },
    ).body.decode()

    # Antwort ist eine <tr> - kein <div> anhaengbar, das Polling wird deshalb
    # per Event angestossen (index.html aktiviert den im DOM vorhandenen
    # Poll-Div), status_oob.html schaltet ihn nach Sync-Ende wieder ab.
    trigger = json.dumps({"jobStarted": {"module": KEY}})
    return HTMLResponse(row_html, headers={"HX-Trigger": trigger})


# ---------------------------------------------------------------------------
# Sync-All-Action
# ---------------------------------------------------------------------------


@router.post(f"/ui/{KEY}/sync-all", response_class=HTMLResponse)
def ui_sync_all(request: Request):
    from ..jobs import sync_all_async

    sync_all_async()
    html = render(
        request,
        "content.html",
        {
            "cfg": store.list(),
            "module": KEY,
            "container_id": f"mod-{KEY}",
            "loading_id": f"{KEY}-loading",
            "running": _running_fn(),
        },
    ).body.decode()

    trigger = json.dumps({"jobStarted": {"module": KEY}})
    return HTMLResponse(html, headers={"HX-Trigger": trigger})


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


