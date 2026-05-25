"""astrapi_mirror.modules.debian.ui – UI-Router für das Debian-Modul."""

from pathlib import Path

from astrapi_core.ui.crud_blueprint import make_crud_router
from astrapi_core.ui.render import render
from fastapi import Request
from fastapi.responses import HTMLResponse

from .. import KEY, store

_DIR = Path(__file__).parent.parent  # modules/debian/

router = make_crud_router(
    store,
    KEY,
    schema_path=str(_DIR / "config" / "schema.yaml"),
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
# Alle Repos syncen (Page-Action)
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
# sources.list Modal
# ---------------------------------------------------------------------------


@router.get(f"/ui/{KEY}/{{repo_id}}/sources-list", response_class=HTMLResponse)
def ui_sources_list(repo_id: str, request: Request):
    data = store.get(repo_id) or {}
    from ..engine import client_sources_file

    base_url = str(request.base_url).rstrip("/")
    slug = data.get("slug", repo_id)
    snippet = client_sources_file(data, base_url)

    # Anzeige-Snippet: Inline-GPG-Block nicht anzeigen (nur Signed-By-Hinweis)
    if "Signed-By:\n" in snippet:
        idx = snippet.find("Signed-By:\n")
        display_snippet = snippet[:idx] + "Signed-By: …\n"
    else:
        display_snippet = snippet

    # GPG-curl-Befehl nur anzeigen wenn Key noch als Pfad-Referenz eingebunden ist
    gpg_key = (data.get("gpg_key") or "").strip()
    key_is_inline = gpg_key.startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----")
    gpg_url = f"{base_url}/repo/debian/{slug}.gpg" if gpg_key and not key_is_inline else None

    return render(
        request,
        f"{KEY}/dialogs/sources_list/modal.html",
        {
            "repo_id": repo_id,
            "label": data.get("label") or repo_id,
            "snippet": snippet,
            "display_snippet": display_snippet,
            "filename": f"{slug}.sources",
            "gpg_url": gpg_url,
        },
    )


# ---------------------------------------------------------------------------
# Validierungs-Modal
# ---------------------------------------------------------------------------


@router.get(f"/ui/{KEY}/{{repo_id}}/validate", response_class=HTMLResponse)
def ui_validate(repo_id: str, request: Request):
    data = store.get(repo_id) or {}
    from ..engine import validate_repo

    result = validate_repo(data)
    return render(
        request,
        f"{KEY}/dialogs/validate/modal.html",
        {
            "repo_id": repo_id,
            "label": data.get("label") or repo_id,
            "result": result,
        },
    )


# ---------------------------------------------------------------------------
# Log-Modal
# ---------------------------------------------------------------------------


@router.get(f"/ui/{KEY}/{{repo_id}}/log", response_class=HTMLResponse)
def ui_log(repo_id: str, request: Request):
    data = store.get(repo_id) or {}
    issues = data.get("last_sync_issues") or []
    return render(
        request,
        f"{KEY}/dialogs/log/modal.html",
        {
            "repo_id": repo_id,
            "label": data.get("label") or repo_id,
            "last_run": data.get("last_run", "—"),
            "last_status": data.get("last_status", "—"),
            "issues": issues,
        },
    )
