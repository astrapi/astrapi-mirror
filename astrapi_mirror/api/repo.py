"""astrapi_mirror.api.repo – Generischer Mirror-File-Server unter /files/.

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

router = APIRouter()

_UNITS = [("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)]

_CSS = """
    body { font-family: monospace; padding: 2rem; background: #0d1117; color: #c9d1d9; }
    h1 { color: #58a6ff; margin-bottom: 0.25rem; }
    p.hint { color: #8b949e; font-size: 0.85rem; margin-bottom: 1.5rem; }
    p.back { margin-bottom: 1rem; font-size: 0.85rem; }
    table { border-collapse: collapse; width: 100%; }
    thead th { text-align: left; padding: 0.4rem 1rem; border-bottom: 2px solid #30363d; color: #8b949e; }
    td { padding: 0.3rem 1rem; border-bottom: 1px solid #21262d; }
    td.size { text-align: right; color: #8b949e; white-space: nowrap; }
    div.hint { color: #8b949e; font-size: 0.85rem; margin-bottom: 1.5rem; }
    div.hint pre { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 0.75rem 2.5rem 0.75rem 1rem; margin: 0.5rem 0 0; font-size: 0.82rem; white-space: pre; overflow-x: auto; color: #c9d1d9; }
    a { text-decoration: none; color: #58a6ff; }
    a:hover { text-decoration: underline; }
    .pre-wrap { position: relative; }
    .copy-btn { position: absolute; top: 6px; right: 8px; background: none; border: none; cursor: pointer;
                padding: 4px; border-radius: 4px; opacity: .65; color: #8b949e; transition: opacity .15s; }
    .copy-btn:hover { opacity: 1; }
"""


def _fmt_size(n: int) -> str:
    for unit, div in _UNITS:
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n} B"


def _page(title: str, hint: str, rows_html: str, back: str | None = None) -> str:
    back_html = f'<p class="back"><a href="{back}">← Zurück</a></p>' if back else ""
    return f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="utf-8"><title>{title}</title><style>{_CSS}</style></head>
<body>
  {back_html}
  <h1>{title}</h1>
  <div class="hint">{hint}</div>
  <table>
    <thead><tr><th>Name</th><th>Größe</th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
<script>
function copySnippet(id, btn) {{
  var txt = document.getElementById(id).value;
  var done = function() {{
    var i = btn.querySelector('.ci'), c = btn.querySelector('.ck');
    i.style.display='none'; c.style.display='';
    setTimeout(function(){{i.style.display='';c.style.display='none';}},1500);
  }};
  if (navigator.clipboard) {{
    navigator.clipboard.writeText(txt).then(done).catch(done);
  }} else {{
    var ta = document.createElement('textarea');
    ta.value = txt; ta.style.position='fixed'; ta.style.opacity='0';
    document.body.appendChild(ta); ta.focus(); ta.select();
    try {{ document.execCommand('copy'); }} catch(e) {{}}
    document.body.removeChild(ta); done();
  }}
}}
</script>
</body>
</html>"""


def _safe_child(base: Path, *parts: str) -> Path:
    """Gibt aufgelösten Pfad zurück; wirft 400 bei Path-Traversal."""
    resolved = (base / Path(*parts)).resolve()
    if not str(resolved).startswith(str(base.resolve())):
        raise HTTPException(400, "Ungültiger Pfad")
    return resolved


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
    try:
        from astrapi_mirror.modules.debian.engine import client_sources_file

        base_url = str(request.base_url).rstrip("/")
        src_full = client_sources_file(repo_data, base_url)
        if "Signed-By:\n" in src_full:
            idx = src_full.find("Signed-By:\n")
            src_display = src_full[:idx] + "Signed-By: …\n"
        else:
            src_display = src_full
        safe_id = f"src-{repo_id}"
        _copy_icon = (
            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none"'
            ' stroke="currentColor" stroke-width="2">'
            '<rect x="9" y="9" width="13" height="13" rx="2"/>'
            '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>'
            "</svg>"
        )
        _check_icon = (
            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none"'
            ' stroke="currentColor" stroke-width="2.5">'
            '<polyline points="20 6 9 17 4 12"/>'
            "</svg>"
        )
        return (
            f"{repo_id}.sources:"
            f'<div class="pre-wrap">'
            f"<pre>{_html.escape(src_display)}</pre>"
            f'<textarea id="{safe_id}" style="display:none">{_html.escape(src_full)}</textarea>'
            f'<button class="copy-btn" title="Kopieren" onclick="copySnippet(\'{safe_id}\', this)">'
            f'<span class="ci">{_copy_icon}</span>'
            f'<span class="ck" style="display:none">{_check_icon}</span>'
            f"</button></div>"
        )
    except Exception:
        return ""


def _archlinux_hint(repo_id: str, repo_data: dict, request: Request) -> str:
    archs = ", ".join(repo_data.get("architectures") or ["x86_64"])
    return f"Repository: {_html.escape(repo_data.get('label', repo_id))} · Architekturen: {archs}"


def _debian_virtual_file(repo_id: str, path: str, request: Request):
    """Gibt Response für virtuelle Debian-Dateien zurück oder None."""
    if path == f"{repo_id}.sources":
        try:
            from astrapi_mirror.modules.debian import store
            from astrapi_mirror.modules.debian.engine import client_sources_file

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


def _debian_virtual_entries(repo_id: str, os_type: str) -> list[str]:
    """Gibt zusätzliche Tabellenzeilen für virtuelle Dateien im Repo-Root."""
    rows = [
        f'<tr><td><a href="/files/{os_type}/{repo_id}/{repo_id}.sources">{repo_id}.sources</a></td>'
        f'<td class="size">—</td></tr>'
    ]
    try:
        from astrapi_mirror.modules.debian import store

        d = store.get(repo_id) or {}
        gpg = (d.get("gpg_key") or "").strip()
        if gpg and not gpg.startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----"):
            rows.append(
                f'<tr><td><a href="/files/{os_type}/{repo_id}/{repo_id}.gpg">{repo_id}.gpg</a></td>'
                f'<td class="size">—</td></tr>'
            )
    except Exception:
        pass
    return rows


_OS_REGISTRY: dict[str, dict] = {
    "debian": {
        "label": "Debian",
        "mirror_root_fn": _debian_mirror_root,
        "store_fn": _get_debian_store,
        "hint_fn": _debian_hint,
        "virtual_file_fn": _debian_virtual_file,
        "virtual_entries_fn": _debian_virtual_entries,
    },
    "archlinux": {
        "label": "Arch Linux",
        "mirror_root_fn": _archlinux_mirror_root,
        "store_fn": _get_archlinux_store,
        "hint_fn": _archlinux_hint,
        "virtual_file_fn": None,
        "virtual_entries_fn": None,
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


@router.get("/files", include_in_schema=False)
def files_redirect():
    return RedirectResponse("/files/", status_code=301)


@router.get("/files/", response_class=HTMLResponse, include_in_schema=False)
def files_index():
    rows = "\n".join(
        f'<tr><td><a href="/files/{os}/">{_html.escape(cfg["label"])}/</a></td>'
        f'<td class="size">—</td></tr>'
        for os, cfg in _OS_REGISTRY.items()
    )
    return HTMLResponse(_page("Mirror", "Verfügbare Distributionen", rows))


@router.get("/files/{os_type}", include_in_schema=False)
def os_type_redirect(os_type: str):
    if os_type not in _OS_REGISTRY:
        raise HTTPException(404, f"Unbekannter OS-Typ: {os_type}")
    return RedirectResponse(f"/files/{os_type}/", status_code=301)


@router.get("/files/{os_type}/", response_class=HTMLResponse, include_in_schema=False)
def os_repo_listing(os_type: str):
    cfg = _OS_REGISTRY.get(os_type)
    if not cfg:
        raise HTTPException(404, f"Unbekannter OS-Typ: {os_type}")
    try:
        repos = cfg["store_fn"]().list()
    except Exception:
        repos = {}
    rows = []
    for _key, repo_data in sorted(repos.items(), key=lambda x: x[1].get("label", "")):
        repo_id = repo_data.get("slug") or str(_key)
        if _resolve_repo_path(os_type, repo_id) is None:
            continue
        label = repo_data.get("label") or repo_id
        rows.append(
            f'<tr><td><a href="/files/{os_type}/{repo_id}/">{_html.escape(label)}</a></td>'
            f'<td class="size">—</td></tr>'
        )
    if not rows:
        return HTMLResponse(
            _page(
                f"{cfg['label']} Mirror",
                "Noch keine synchronisierten Repositories vorhanden.",
                "<tr><td colspan='2'>Bitte zuerst einen Sync starten.</td></tr>",
                back="/files/",
            )
        )
    return HTMLResponse(_page(f"{cfg['label']} Mirror", "", "\n".join(rows), back="/files/"))


@router.get("/files/{os_type}/{repo_id}", include_in_schema=False)
def repo_redirect(os_type: str, repo_id: str):
    return RedirectResponse(f"/files/{os_type}/{repo_id}/", status_code=301)


@router.get("/files/{os_type}/{repo_id}/{path:path}", include_in_schema=False)
def generic_serve(os_type: str, repo_id: str, path: str, request: Request):
    cfg = _OS_REGISTRY.get(os_type)
    if not cfg:
        raise HTTPException(404, f"Unbekannter OS-Typ: {os_type}")

    # Virtuelle Dateien (OS-spezifisch)
    virtual_fn = cfg.get("virtual_file_fn")
    if virtual_fn and path:
        resp = virtual_fn(repo_id, path, request)
        if resp is not None:
            return resp

    real_root = _resolve_repo_path(os_type, repo_id)
    if real_root is None:
        return HTMLResponse(
            _page(
                f"{os_type}/{repo_id}",
                "Noch nicht synchronisiert – bitte zuerst einen Sync starten.",
                "",
                back=f"/files/{os_type}/",
            )
        )

    target = _safe_child(real_root, path.strip("/")) if path.strip("/") else real_root

    if target.is_file():
        return FileResponse(str(target))

    if target.is_dir():
        path_clean = path.rstrip("/")
        path_parts = path_clean.split("/") if path_clean else []
        if len(path_parts) > 1:
            back = f"/files/{os_type}/{repo_id}/" + "/".join(path_parts[:-1]) + "/"
        elif path_parts:
            back = f"/files/{os_type}/{repo_id}/"
        else:
            back = f"/files/{os_type}/"

        title = f"{os_type}/{repo_id}" + (f"/{path_clean}" if path_clean else "")

        hint = ""
        if not path_clean:
            hint_fn = cfg.get("hint_fn")
            if hint_fn:
                try:
                    repo_data = cfg["store_fn"]().get(repo_id) or {}
                    hint = hint_fn(repo_id, repo_data, request)
                except Exception:
                    pass

        try:
            fs_entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
        except PermissionError:
            raise HTTPException(403, "Zugriff verweigert")

        repo_prefix = f"/files/{os_type}/{repo_id}"
        rows = []
        if not path_clean:
            ve_fn = cfg.get("virtual_entries_fn")
            if ve_fn:
                rows.extend(ve_fn(repo_id, os_type))
        for e in fs_entries:
            display = e.name + ("/" if e.is_dir() else "")
            suffix = "/" if e.is_dir() else ""
            href = (
                f"{repo_prefix}/{path_clean}/{e.name}{suffix}"
                if path_clean
                else f"{repo_prefix}/{e.name}{suffix}"
            )
            size = "—" if e.is_dir() else _fmt_size(e.stat().st_size)
            rows.append(
                f'<tr><td><a href="{href}">{_html.escape(display)}</a></td>'
                f'<td class="size">{size}</td></tr>'
            )
        return HTMLResponse(
            _page(
                title,
                hint,
                "\n".join(rows) or "<tr><td colspan='2'>Leer.</td></tr>",
                back=back,
            )
        )

    raise HTTPException(404, "Nicht gefunden")
