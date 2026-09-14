"""astrapi_mirror.api.repo – Generischer Mirror-File-Server unter /.

Unterstützte OS-Typen werden in ``_OS_REGISTRY`` registriert.
Neue Distributionen können durch einen weiteren Eintrag eingebunden werden.
"""

import html as _html
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)

from astrapi_core.ui.file_listing import (
    Cell,
    copy_button as _copy_btn,
    dir_link as _dir_link,
    file_link as _file_link,
    list_dir_entries,
    render_link_row,
    render_page as _page,
    render_row,
    render_row_pair,
    safe_child as _safe_child,
)

router = APIRouter()


def _fmt_date(raw: str) -> str:
    """ISO-Datum 'YYYY-MM-DD HH:MM' → deutsches Format 'DD.MM.YYYY HH:MM'."""
    if not raw or raw == "—":
        return "—"
    parts = raw.split(" ", 1)
    if len(parts) == 2:
        dp = parts[0].split("-")
        if len(dp) == 3 and len(dp[0]) == 4:
            return f"{dp[2]}.{dp[1]}.{dp[0]} {parts[1]}"
    return raw


_DEBIAN_COLS = (
    ("c-name", "Name"), ("c-date", "Letzter Sync"),
    ("c-size", "Größe"), ("c-inst", "Installation"),
)
_ARCH_COLS = (
    ("c-name", "Name"), ("c-date", "Letzter Sync"), ("c-size2", "Größe"),
)


# ---------------------------------------------------------------------------
# OS-Registry – lazy callables um zirkuläre Imports beim Laden zu vermeiden
# ---------------------------------------------------------------------------


def _debian_mirror_root() -> Path:
    from astrapi_mirror._paths import mirror_path

    return mirror_path()


def _archlinux_mirror_root() -> Path:
    from astrapi_mirror._paths import archlinux_mirror_path

    return archlinux_mirror_path()


def _get_debian_store():
    from astrapi_mirror.modules.debian import store

    return store


def _get_archlinux_store():
    from astrapi_mirror.modules.archlinux import store

    return store


def _debian_hint(repo_id: str, repo_data: dict, request: Request) -> str:
    base_url = str(request.base_url).rstrip("/")

    keyring_deb = None
    try:
        repo_dir = _debian_mirror_root() / repo_id / "current"
        if repo_dir.exists():
            candidates = sorted(repo_dir.glob("*keyring*.deb"), key=lambda f: f.name, reverse=True)
            if candidates:
                keyring_deb = candidates[0].name
    except Exception:
        pass

    keyring_status = "✓ verfügbar" if keyring_deb else "⚠ noch nicht synchronisiert"
    dl_url = f"{base_url}/debian/{repo_id}/{keyring_deb or repo_id + '-keyring_1.0.0_all.deb'}"
    keyring_cmd = f"curl -k {dl_url} -o /tmp/{repo_id}-keyring.deb\nsudo dpkg -i /tmp/{repo_id}-keyring.deb"

    return (
        f'<div class="setup"><h2>Einrichtung</h2>'
        f'<p class="step">1 · Keyring installieren ({keyring_status})</p>'
        f'<div class="pre-wrap">{_copy_btn(f"kb-{repo_id}-key", keyring_cmd)}'
        f'<pre>{_html.escape(keyring_cmd)}</pre></div>'
        f'</div>'
    )


def _archlinux_hint(repo_id: str, repo_data: dict, request: Request) -> str:
    base_url = str(request.base_url).rstrip("/")

    try:
        from astrapi_mirror.modules.archlinux._sync_engine.downloader import (
            ArchDownloader,
            _detect_arch,
        )

        urls = ArchDownloader._get_mirror_list(repo_data)
        detected_archs = sorted({_detect_arch(u) for u in urls})
    except Exception:
        detected_archs = []
    archs = ", ".join(detected_archs or ["x86_64"])

    server_url = f"{base_url}/archlinux/{repo_id}/os/$arch"
    conf_snippet = f"[{repo_id}]\nServer = {server_url}"

    return (
        f'<div class="setup"><h2>Einrichtung</h2>'
        f'<p class="step">1 · In /etc/pacman.conf eintragen (Architekturen: {_html.escape(archs)})</p>'
        f'<div class="pre-wrap">{_copy_btn(f"pc-{repo_id}", conf_snippet)}'
        f'<pre>{_html.escape(conf_snippet)}</pre></div>'
        f'</div>'
    )


def _debian_virtual_file(repo_id: str, path: str, request: Request):
    """Gibt Response für virtuelle Debian-Dateien zurück oder None."""
    if path == f"{repo_id}.sources":
        try:
            from astrapi_mirror.modules.debian import store
            from astrapi_mirror.modules.debian._sync_engine.engine import client_sources_file

            data = store.get(repo_id) or {}
            base_url = str(request.base_url).rstrip("/")
            content = client_sources_file(data, base_url)
        except Exception:
            raise HTTPException(500, "Fehler beim Generieren der .sources-Datei")
        return PlainTextResponse(
            content,
            headers={"Content-Disposition": f'inline; filename="{repo_id}.sources"'},
        )
    if path == f"{repo_id}.gpg":
        try:
            from astrapi_mirror.modules.debian import store

            data = store.get(repo_id)
        except Exception:
            data = None
        if not data or not data.get("gpg_key"):
            raise HTTPException(404, "Kein GPG-Schlüssel hinterlegt")
        return Response(
            content=data["gpg_key"].encode(),
            media_type="application/pgp-keys",
            headers={"Content-Disposition": f'attachment; filename="{repo_id}.gpg"'},
        )
    return None


def _debian_virtual_entries(repo_id: str, os_type: str) -> list[tuple[str, str]]:
    """Gibt zusätzliche (Desktop-<tr>, Mobile-.m-card)-Paare für virtuelle
    Dateien im Repo-Root zurück."""
    rows = [
        render_row_pair(
            [
                Cell(_file_link(f"{repo_id}.sources", f"/{os_type}/{repo_id}/{repo_id}.sources")),
                Cell("—", label="Geändert"),
                Cell("—", label="Größe", css="size"),
            ]
        )
    ]
    try:
        from astrapi_mirror.modules.debian import store

        d = store.get(repo_id) or {}
        gpg = (d.get("gpg_key") or "").strip()
        if gpg and not gpg.startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----"):
            rows.append(
                render_row_pair(
                    [
                        Cell(_file_link(f"{repo_id}.gpg", f"/{os_type}/{repo_id}/{repo_id}.gpg")),
                        Cell("—", label="Geändert"),
                        Cell("—", label="Größe", css="size"),
                    ]
                )
            )
    except Exception:
        pass
    return rows


_OS_REGISTRY: dict[str, dict] = {
    "archlinux": {
        "label": "archlinux",
        "mirror_root_fn": _archlinux_mirror_root,
        "store_fn": _get_archlinux_store,
        "hint_fn": _archlinux_hint,
        "virtual_file_fn": None,
        "virtual_entries_fn": None,
    },
    "debian": {
        "label": "debian",
        "mirror_root_fn": _debian_mirror_root,
        "store_fn": _get_debian_store,
        "hint_fn": _debian_hint,
        "virtual_file_fn": _debian_virtual_file,
        "virtual_entries_fn": _debian_virtual_entries,
    },
}


def _resolve_repo_path(os_type: str, repo_id: str) -> Path | None:
    """Gibt {mirror_root}/{repo_id}/current zurück wenn vorhanden, sonst None."""
    try:
        cfg = _OS_REGISTRY[os_type]
        p = cfg["mirror_root_fn"]() / repo_id / "current"
        return p if p.exists() else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def files_index():
    rows = [render_link_row(cfg["label"] + "/", f"/{os}/") for os, cfg in _OS_REGISTRY.items()]
    return HTMLResponse(_page("Mirror", "", rows, col_headers=("Name",)))


@router.get("/{os_type}", include_in_schema=False)
def os_type_redirect(os_type: str):
    if os_type not in _OS_REGISTRY:
        raise HTTPException(404, f"Unbekannter OS-Typ: {os_type}")
    return RedirectResponse(f"/{os_type}/", status_code=301)


@router.get("/{os_type}/", response_class=HTMLResponse, include_in_schema=False)
def os_repo_listing(os_type: str, request: Request):
    cfg = _OS_REGISTRY.get(os_type)
    if not cfg:
        raise HTTPException(404, f"Unbekannter OS-Typ: {os_type}")
    try:
        repos = cfg["store_fn"]().list()
    except Exception:
        repos = {}

    is_debian = os_type == "debian"
    is_arch = os_type == "archlinux"
    base_url = str(request.base_url).rstrip("/")

    if is_debian:
        _cols = _DEBIAN_COLS
    elif is_arch:
        _cols = _ARCH_COLS
    else:
        _cols = None

    col_headers = tuple(h for _, h in _cols) if _cols else ("Name", "")
    colgroup = ("<colgroup>" + "".join(f'<col class="{c}">' for c, _ in _cols) + "</colgroup>") if _cols else ""
    rows = []

    for _key, repo_data in sorted(repos.items(), key=lambda x: x[1].get("label", "")):
        repo_id = repo_data.get("slug") or str(_key)
        if _resolve_repo_path(os_type, repo_id) is None:
            continue
        label = repo_data.get("label") or repo_id
        name_cell = Cell(_dir_link(label, f"/{os_type}/{repo_id}/"))

        if is_debian or is_arch:
            info = repo_data.get("last_info") or {}
            last_run = _fmt_date(repo_data.get("last_run") or "")
            size = info.get("current_size_fmt") or "—"
            size_class = "num-gap" if is_debian else "num"
            cells = [
                name_cell,
                Cell(_html.escape(last_run), label="Letzter Sync", css="num"),
                Cell(_html.escape(size), label="Größe", css=size_class),
            ]
            if is_debian:
                sources_url = f"{base_url}/{os_type}/{repo_id}/{repo_id}.sources"
                cmd = f"sudo curl -fsSL {sources_url} -o /etc/apt/sources.list.d/{repo_id}.sources"
                uid = f"curl-{repo_id}"
                cmd_html = (
                    f'<div class="cmd">{_copy_btn(uid, cmd)}<code>{_html.escape(cmd)}</code></div>'
                )
                cells.append(Cell(cmd_html, label="Installation"))
            rows.append(render_row_pair(cells))
        else:
            rows.append(render_row_pair([name_cell, Cell("—", label="Größe", css="size")]))

    return HTMLResponse(
        _page(
            f"{cfg['label']} Mirror",
            "" if rows else "Noch keine synchronisierten Repositories vorhanden.",
            rows,
            back="/",
            col_headers=col_headers,
            colgroup=colgroup,
            empty_message="Bitte zuerst einen Sync starten.",
        )
    )


@router.get("/{os_type}/{repo_id}", include_in_schema=False)
def repo_redirect(os_type: str, repo_id: str):
    return RedirectResponse(f"/{os_type}/{repo_id}/", status_code=301)


@router.get("/{os_type}/{repo_id}/{path:path}", include_in_schema=False)
def generic_serve(os_type: str, repo_id: str, path: str, request: Request):
    cfg = _OS_REGISTRY.get(os_type)
    if not cfg:
        raise HTTPException(404, f"Unbekannter OS-Typ: {os_type}")

    # Existenz zuerst gegen die DB pruefen, nicht nur gegen das Dateisystem --
    # sonst wird fuer ein gar nicht konfiguriertes Repo (z.B. Tippfehler in
    # der URL) faelschlich der "noch nicht synchronisiert"-Einrichtungshinweis
    # angezeigt statt eines 404.
    repo_entry = cfg["store_fn"]().get(repo_id)
    if repo_entry is None:
        raise HTTPException(404, f"Repo nicht konfiguriert: {os_type}/{repo_id}")

    # Virtuelle Dateien (OS-spezifisch)
    virtual_fn = cfg.get("virtual_file_fn")
    if virtual_fn and path:
        resp = virtual_fn(repo_id, path, request)
        if resp is not None:
            return resp

    # Hint für das Repo-Root vorab berechnen (wird auch bei "nicht synchronisiert" angezeigt)
    root_hint = ""
    if not path.strip("/"):
        hint_fn = cfg.get("hint_fn")
        if hint_fn:
            try:
                root_hint = hint_fn(repo_id, repo_entry, request)
            except Exception:
                pass

    real_root = _resolve_repo_path(os_type, repo_id)
    if real_root is None:
        return HTMLResponse(
            _page(
                f"{os_type}/{repo_id}",
                root_hint or "Noch nicht synchronisiert – bitte zuerst einen Sync starten.",
                [],
                back=f"/{os_type}/",
            )
        )

    target = _safe_child(real_root, path.strip("/")) if path.strip("/") else real_root

    if target.is_file():
        return FileResponse(str(target))

    if target.is_dir():
        path_clean = path.rstrip("/")
        path_parts = path_clean.split("/") if path_clean else []
        if len(path_parts) > 1:
            back = f"/{os_type}/{repo_id}/" + "/".join(path_parts[:-1]) + "/"
        elif path_parts:
            back = f"/{os_type}/{repo_id}/"
        else:
            back = f"/{os_type}/"

        title = f"{os_type}/{repo_id}" + (f"/{path_clean}" if path_clean else "")

        repo_prefix = f"/{os_type}/{repo_id}"

        def _href(name: str, is_dir: bool) -> str:
            suffix = "/" if is_dir else ""
            return (
                f"{repo_prefix}/{path_clean}/{name}{suffix}"
                if path_clean
                else f"{repo_prefix}/{name}{suffix}"
            )

        try:
            entries = list_dir_entries(target, _href)
        except PermissionError:
            raise HTTPException(403, "Zugriff verweigert")

        rows = []
        if not path_clean:
            ve_fn = cfg.get("virtual_entries_fn")
            if ve_fn:
                rows.extend(ve_fn(repo_id, os_type))
        rows.extend(render_row(e) for e in entries)
        return HTMLResponse(
            _page(
                title,
                root_hint,
                rows,
                back=back,
                col_headers=("Name", "Geändert", "Größe"),
                empty_message="Leer.",
            )
        )

    raise HTTPException(404, "Nicht gefunden")
