"""astrapi_mirror.modules.archlinux.jobs – Sync via run_logged/run_all (wie astrapi-backup)."""

import asyncio
import threading
from datetime import datetime

from astrapi_core.system.logger import log, log_context
from astrapi_core.system.runner import run_all, run_logged


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


_MAX_RETRIES = 5

# Verhindert parallele Syncs desselben Repos: manueller Sync-Button und
# "Alle synchronisieren"/Scheduler koennen run_single() sonst gleichzeitig
# fuer dieselbe repo_id aufrufen - beide Laeufe teilen sich dasselbe
# Staging-Verzeichnis und ueberschreiben unabhaengig voneinander
# last_status, ohne dass einer vom anderen weiss.
_running_lock = threading.Lock()
_running_repos: set[str] = set()


def _important(line: str) -> bool:
    """True für Zeilen die ins Activity-Log gehören (Phasen, Fehler, Zusammenfassungen)."""
    s = line.strip()
    if not s:
        return False
    return any(m in s for m in (
        "❌", "⚠️",
        "[1/", "[2/", "[3/", "[4/",
        "📦 Repo:", "Repo-ID:", "URLs:",
        "✅ Sync erfolgreich", "✅ Pacman", "✅ Swap",
        "Fehlgeschlagene Repos",
    ))


# ---------------------------------------------------------------------------
# run_single – wird von run_all/run_logged pro Repo aufgerufen
# ---------------------------------------------------------------------------


def run_single(repo_id: str, repo: dict | None = None) -> None:
    """Synchronisiert ein einzelnes Arch Linux Repo (blockierend, für run_all/run_logged).

    Verwirft den Aufruf, falls fuer dieselbe repo_id bereits ein Sync laeuft
    (siehe _running_lock oben) - sonst wuerden zwei Laeufe dasselbe Staging-
    Verzeichnis teilen und last_status gegenseitig ueberschreiben.
    """
    with _running_lock:
        if repo_id in _running_repos:
            log("WARNING", f"Sync für Arch Repo '{repo_id}' läuft bereits, übersprungen")
            return
        _running_repos.add(repo_id)
    try:
        _run_single(repo_id, repo)
    finally:
        with _running_lock:
            _running_repos.discard(repo_id)


def _run_single(repo_id: str, repo: dict | None) -> None:
    from . import store
    from ._sync_engine import SyncEngine, validate_repo
    from astrapi_mirror._paths import archlinux_mirror_path

    if repo is None:
        repo = store.get(repo_id)
    if not repo:
        log("ERROR", f"Arch Repo '{repo_id}' nicht gefunden")
        return

    repo_with_id = {"id": repo_id, **repo}
    label = repo.get("label", repo_id)

    with log_context("archlinux", repo_id):
        log("INFO", f"=== Arch Linux Repo '{label}' synchronisieren ===")
        store.upsert(repo_id, {"last_status": "running"})

        def _on_line(line: str) -> None:
            if _important(line):
                level = "ERROR" if "❌" in line else "WARNING" if "⚠️" in line else "INFO"
                log(level, line.strip())

        engine = SyncEngine(archlinux_mirror_path())

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            rc, _ = loop.run_until_complete(engine.sync_repo(repo_with_id, on_line=_on_line))
        finally:
            loop.close()

        if rc != 0:
            log("ERROR", "Sync fehlgeschlagen")
            store.upsert(repo_id, {"last_status": "error", "last_run": _now(), "last_sync_issues": []})
            return

        val = validate_repo(repo_with_id)
        if val["status"] == "error":
            for issue in val["issues"][:10]:
                log("ERROR", issue)
            store.upsert(repo_id, {
                "last_status": "error",
                "last_run": _now(),
                "last_sync_issues": val.get("issues", []),
            })
            return

        from astrapi_mirror._paths import archlinux_mirror_path
        from astrapi_mirror._repo_info import repo_info

        slug = repo.get("slug", repo_id)
        info = repo_info(
            archlinux_mirror_path() / slug,
            pkg_suffixes=(".zst", ".xz"),
        )
        store.upsert(repo_id, {
            "last_status": "ok",
            "last_run": _now(),
            "last_sync_issues": [],
            "last_info": info,
        })
        log("INFO", f"=== Arch Linux Repo '{label}' erfolgreich synchronisiert ===")


# ---------------------------------------------------------------------------
# Öffentliche Sync-Funktionen
# ---------------------------------------------------------------------------


def _retry_failed(repos: dict) -> None:
    """Wiederholt fehlgeschlagene Repos bis zu _MAX_RETRIES-mal; sendet ntfy bei dauerhaftem Fehler."""
    from . import store

    for attempt in range(1, _MAX_RETRIES + 1):
        failed_ids = [
            repo_id for repo_id in repos
            if (store.get(repo_id) or {}).get("last_status") == "error"
        ]
        if not failed_ids:
            return

        log(
            "INFO",
            f"Retry {attempt}/{_MAX_RETRIES} für {len(failed_ids)} Repo(s): "
            + ", ".join(repos[rid].get("label", rid) for rid in failed_ids),
        )

        for repo_id in failed_ids:
            repo = store.get(repo_id) or {}
            label = f"{repo.get('label', repo_id)} (Retry {attempt}/{_MAX_RETRIES})"
            run_logged(
                "archlinux",
                repo_id,
                label,
                lambda rid=repo_id, r=repo: run_single(rid, r),
            )

    still_failed = [
        repo_id for repo_id in repos
        if (store.get(repo_id) or {}).get("last_status") == "error"
    ]
    if still_failed:
        _notify_sync_failure(still_failed, repos)


def _notify_sync_failure(failed_ids: list[str], repos: dict) -> None:
    """Sendet ntfy-Benachrichtigung für dauerhaft fehlgeschlagene Repos."""
    labels = [repos[rid].get("label", rid) for rid in failed_ids]
    body = (
        f"Nach {_MAX_RETRIES} Versuchen fehlgeschlagen:\n"
        + "\n".join(f"• {label}" for label in labels)
    )
    try:
        from astrapi_core.modules.notify import engine as _ne

        _ne.send(
            title=f"Arch Mirror: {len(failed_ids)} Repo(s) nicht synchronisierbar",
            message=body,
            event=_ne.ERROR,
            source="archlinux",
            tags=["mirror", "sync-fehler"],
        )
    except Exception as e:
        log("WARNING", f"ntfy-Benachrichtigung fehlgeschlagen: {e}")


def sync_all() -> None:
    """Synchronisiert alle aktivierten Arch Linux Repos (blockierend)."""
    from . import store

    repos = {
        str(k): {**v, "id": k}
        for k, v in store.list().items()
        if v.get("enabled", True)
    }
    if not repos:
        return

    run_all(
        "archlinux",
        repos,
        run_single,
        desc_fn=lambda iid, e: e.get("label", iid),
        mark_pending_fn=lambda iid, e: store.upsert(iid, {"last_status": "pending"}),
    )
    _retry_failed(repos)


def sync_repo(repo_id: str) -> None:
    """Synchronisiert ein einzelnes Arch Linux Repo (blockierend)."""
    from . import store

    repo = store.get(repo_id)
    if not repo:
        return
    run_logged("archlinux", repo_id, repo.get("label", repo_id),
               lambda: run_single(repo_id, repo))


# ---------------------------------------------------------------------------
# Async-Wrapper
# ---------------------------------------------------------------------------


def sync_all_async() -> None:
    threading.Thread(target=sync_all, daemon=True).start()


def sync_repo_async(repo_id: str) -> None:
    threading.Thread(target=sync_repo, args=(repo_id,), daemon=True).start()
