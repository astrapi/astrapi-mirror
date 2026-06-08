"""astrapi_mirror.modules.archlinux.jobs – Sync via run_logged/run_all (wie astrapi-backup)."""

import asyncio
import threading
from datetime import datetime

from astrapi_core.system.logger import log, log_context
from astrapi_core.system.runner import run_all, run_logged


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _important(line: str) -> bool:
    """True für Zeilen die ins Activity-Log gehören (Phasen, Fehler, Zusammenfassungen)."""
    s = line.strip()
    if not s:
        return False
    return any(m in s for m in (
        "❌", "⚠️",
        "[1/", "[2/", "[3/", "[4/",
        "📊 Download-Statistik",
        "📦 Repo:", "Repo-ID:", "URL:", "Architekturen:",
        "✅ Sync erfolgreich", "✅ Pacman", "✅ Swap",
        "Fehlgeschlagene Repos",
    ))


# ---------------------------------------------------------------------------
# run_single – wird von run_all/run_logged pro Repo aufgerufen
# ---------------------------------------------------------------------------


def run_single(repo_id: str, repo: dict | None = None) -> None:
    """Synchronisiert ein einzelnes Arch Linux Repo (blockierend, für run_all/run_logged)."""
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
        store.upsert(repo_id, {"last_status": "syncing"})

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

        store.upsert(repo_id, {
            "last_status": "ok",
            "last_run": _now(),
            "last_sync_issues": [],
        })
        log("INFO", f"=== Arch Linux Repo '{label}' erfolgreich synchronisiert ===")


# ---------------------------------------------------------------------------
# Öffentliche Sync-Funktionen
# ---------------------------------------------------------------------------


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
    run_all("archlinux", repos, run_single, desc_fn=lambda iid, e: e.get("label", iid))


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
