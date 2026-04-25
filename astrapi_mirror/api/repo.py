"""astrapi_mirror.api.repo – Debian-Mirror HTTP-Server unter /repo/debian/.

URLs sind ID-basiert: /repo/debian/{repo_id}/… – der Upstream-Hostname
wird nicht in der URL abgebildet. Die Abbildung repo_id → Dateisystem-Pfad
erfolgt über den Store; refrapts physisches Layout (hostname/url-pfad) bleibt
auf Disk unverändert.
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

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
    div.hint pre { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 0.75rem 1rem; margin: 0.5rem 0 0; font-size: 0.82rem; white-space: pre; overflow-x: auto; color: #c9d1d9; }
    a { text-decoration: none; color: #58a6ff; }
    a:hover { text-decoration: underline; }
"""


def _mirror_root() -> Path:
    from astrapi_mirror._paths import mirror_path
    return mirror_path()


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
</body>
</html>"""


def _safe_child(base: Path, *parts: str) -> Path:
    """Gibt aufgelösten Pfad zurück; wirft 400 bei Path-Traversal."""
    resolved = (base / Path(*parts)).resolve()
    if not str(resolved).startswith(str(base.resolve())):
        raise HTTPException(400, "Ungültiger Pfad")
    return resolved


def _repo_real_path(repo_id: str) -> Path | None:
    """Gibt den realen Dateisystem-Pfad für ein Repo anhand seiner ID zurück.

    Schlägt den Eintrag im Store nach, leitet aus der URL den refrapt-Pfad
    (hostname/url-pfad) ab und kombiniert ihn mit mirror_root.
    Gibt None zurück wenn das Repo unbekannt oder die URL leer ist.
    """
    try:
        from astrapi_mirror.modules.debian.storage import store
        from astrapi_mirror.modules.debian.engine import _host_path_from_url
    except ImportError:
        return None
    data = store.get(repo_id)
    if not data:
        return None
    url = (data.get("url") or "").rstrip("/")
    if not url:
        return None
    return _mirror_root() / _host_path_from_url(url)


# ---------------------------------------------------------------------------
# /repo  →  /repo/
# ---------------------------------------------------------------------------
@router.get("/repo", include_in_schema=False)
def repo_redirect():
    return RedirectResponse(url="/repo/", status_code=301)


# ---------------------------------------------------------------------------
# /repo/  – OS-Typ-Übersicht
# ---------------------------------------------------------------------------
@router.get("/repo/", response_class=HTMLResponse, include_in_schema=False)
def repo_index():
    rows = '<tr><td><a href="/repo/debian/">debian/</a></td><td class="size">—</td></tr>'
    return HTMLResponse(_page("Repository", "Verfügbare Distributionen", rows))


# ---------------------------------------------------------------------------
# /repo/debian  →  /repo/debian/
# ---------------------------------------------------------------------------
@router.get("/repo/debian", include_in_schema=False)
def debian_redirect():
    return RedirectResponse(url="/repo/debian/", status_code=301)


# ---------------------------------------------------------------------------
# /repo/debian/  – Repo-Listing (ID-basiert, aus dem Store)
# ---------------------------------------------------------------------------
@router.get("/repo/debian/", response_class=HTMLResponse, include_in_schema=False)
def debian_index(request: Request):
    try:
        from astrapi_mirror.modules.debian.storage import store
        repos = [{"id": k, **v} for k, v in store.list().items()]
    except Exception:
        repos = []

    synced = [r for r in repos if (p := _repo_real_path(r["id"])) is not None and p.exists()]

    if not synced:
        return HTMLResponse(_page(
            "Debian Mirror",
            "Noch keine synchronisierten Repositories vorhanden.",
            "<tr><td colspan='2'>Bitte zuerst einen Sync starten.</td></tr>",
        ))

    rows = "\n".join(
        f'<tr>'
        f'<td><a href="/repo/debian/{r["id"]}/">{r.get("label") or r["id"]}</a></td>'
        f'<td class="size">{r.get("provider_group", "")}</td>'
        f'</tr>'
        for r in synced
    )
    return HTMLResponse(_page("Debian Mirror", "", rows))


# ---------------------------------------------------------------------------
# /repo/debian/{repo_id}.gpg  – GPG-Schlüssel-Download
# ---------------------------------------------------------------------------
@router.get("/repo/debian/{repo_id}.gpg", include_in_schema=False)
def debian_repo_gpg(repo_id: str):
    try:
        from astrapi_mirror.modules.debian.storage import store
        data = store.get(repo_id)
    except Exception:
        data = None
    if not data or not data.get("gpg_key"):
        raise HTTPException(404, "Kein GPG-Schlüssel hinterlegt")
    from fastapi.responses import Response
    return Response(
        content=data["gpg_key"].encode(),
        media_type="application/pgp-keys",
        headers={"Content-Disposition": f'attachment; filename="{repo_id}.gpg"'},
    )


# ---------------------------------------------------------------------------
# /repo/debian/{repo_id}  →  /repo/debian/{repo_id}/
# ---------------------------------------------------------------------------
@router.get("/repo/debian/{repo_id}", include_in_schema=False)
def debian_repo_redirect(repo_id: str):
    return RedirectResponse(url=f"/repo/debian/{repo_id}/", status_code=301)


# ---------------------------------------------------------------------------
# /repo/debian/{repo_id}/{path:path}  – Datei-Download / Directory-Listing
# ---------------------------------------------------------------------------
@router.get("/repo/debian/{repo_id}/{path:path}", include_in_schema=False)
def debian_repo_serve(repo_id: str, path: str, request: Request):
    real_root = _repo_real_path(repo_id)
    if real_root is None:
        raise HTTPException(404, "Repo nicht gefunden")

    # --- Virtuelle Dateien im Repo-Root -----------------------------------

    if path == f"{repo_id}.sources":
        try:
            from astrapi_mirror.modules.debian.storage import store
            from astrapi_mirror.modules.debian.engine import client_sources_file
            data = store.get(repo_id) or {}
            base_url = str(request.base_url).rstrip("/")
            content = client_sources_file({"id": repo_id, **data}, base_url)
        except Exception:
            raise HTTPException(500, "Fehler beim Generieren der .sources-Datei")
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(
            content,
            headers={"Content-Disposition": f'inline; filename="{repo_id}.sources"'},
        )

    if path == f"{repo_id}.gpg":
        return debian_repo_gpg(repo_id)

    # --- Normales Filesystem-Serving ---------------------------------------

    if not real_root.exists():
        return HTMLResponse(_page(
            f"debian/{repo_id}",
            "Noch nicht synchronisiert – bitte zuerst einen Sync starten.",
            "",
            back="/repo/debian/",
        ))

    target = _safe_child(real_root, path) if path else real_root

    if target.is_dir():
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
        rows = []

        # Virtuelle Einträge nur im Repo-Root einblenden
        if not path:
            src_name = f"{repo_id}.sources"
            rows.append(
                f'<tr><td><a href="/repo/debian/{repo_id}/{src_name}">{src_name}</a></td>'
                f'<td class="size">—</td></tr>'
            )
            try:
                from astrapi_mirror.modules.debian.storage import store as _st
                _d = _st.get(repo_id)
                if _d and _d.get("gpg_key"):
                    gpg_name = f"{repo_id}.gpg"
                    rows.append(
                        f'<tr><td><a href="/repo/debian/{gpg_name}">{gpg_name}</a></td>'
                        f'<td class="size">—</td></tr>'
                    )
            except Exception:
                pass

        for e in entries:
            name = e.name + ("/" if e.is_dir() else "")
            base_href = f"/repo/debian/{repo_id}"
            if path:
                href = f"{base_href}/{path.rstrip('/')}/{e.name}" + ("/" if e.is_dir() else "")
            else:
                href = f"{base_href}/{e.name}" + ("/" if e.is_dir() else "")
            size = "—" if e.is_dir() else _fmt_size(e.stat().st_size)
            rows.append(f'<tr><td><a href="{href}">{name}</a></td><td class="size">{size}</td></tr>')

        if path:
            path_parts = path.rstrip("/").split("/")
            if len(path_parts) > 1:
                parent = f"/repo/debian/{repo_id}/" + "/".join(path_parts[:-1])
            else:
                parent = f"/repo/debian/{repo_id}/"
        else:
            parent = "/repo/debian/"

        display = f"debian/{repo_id}" + (f"/{path.rstrip('/')}" if path else "")

        hint = ""
        if not path:
            try:
                from astrapi_mirror.modules.debian.storage import store as _st2
                from astrapi_mirror.modules.debian.engine import client_sources_file
                _d2 = _st2.get(repo_id) or {}
                base_url = str(request.base_url).rstrip("/")
                _src = client_sources_file({"id": repo_id, **_d2}, base_url)
                hint = f'{repo_id}.sources:<pre>{_src}</pre>'
                _gpg = _d2.get("gpg_key", "").strip()
                if _gpg:
                    hint += f'{repo_id}.gpg:<pre>{_gpg}</pre>'
            except Exception:
                pass

        return HTMLResponse(_page(
            display,
            hint,
            "\n".join(rows) or "<tr><td colspan='2'>Leer.</td></tr>",
            back=parent,
        ))

    if target.is_file():
        return FileResponse(str(target))

    raise HTTPException(404, "Nicht gefunden")
