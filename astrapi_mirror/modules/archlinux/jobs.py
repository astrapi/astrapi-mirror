"""astrapi_mirror.modules.archlinux.jobs – Hintergrund-Sync für Arch Linux."""

import asyncio
import logging
import threading
import time
from datetime import datetime

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _act_start(label: str, item_id: str | None = None) -> int | None:
    try:
        from astrapi_core.system.activity_log import log_activity

        return log_activity("job", "archlinux", label, status="running", item_id=item_id)
    except Exception:
        return None


def _act_done(act_id: int | None, status: str, duration: int, output: str) -> None:
    if act_id is None:
        return
    try:
        from astrapi_core.system.activity_log import update_activity_log

        update_activity_log(
            log_id=act_id,
            status=status,
            duration_s=duration,
            full_log=output[-20_000:],
            error_message=output[-500:] if status == "error" else None,
        )
    except Exception:
        pass


def _notify(title: str, message: str, ok: bool) -> None:
    try:
        from astrapi_core.modules.notify import engine as _n

        _n.send(
            title=title,
            message=message,
            event=_n.SUCCESS if ok else _n.ERROR,
            source="archlinux",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Sync: alle aktivierten Repos
# ---------------------------------------------------------------------------


def sync_all() -> None:
    """Synchronisiert alle aktivierten Arch Linux Repos (blockierend)."""
    from . import store
    from ._sync_engine import SyncEngine, validate_repo

    repos_raw = store.list()
    repos = [{"id": k, **v} for k, v in repos_raw.items() if v.get("enabled", True)]

    if not repos:
        log.info("archlinux.sync_all: keine aktivierten Repos")
        return

    act_id = _act_start("Arch Linux: Alle Repos syncen")
    t0 = time.time()

    log_lines: list[str] = []

    def _flush(line: str) -> None:
        log_lines.append(line)

    # Engine initialisieren
    from astrapi_mirror._paths import archlinux_mirror_path

    engine = SyncEngine(archlinux_mirror_path())

    # Asynchronen Sync starten (blockierend von Thread aus)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        rc, output = loop.run_until_complete(engine.sync_repos(repos, on_line=_flush))
    finally:
        loop.close()

    status = "ok" if rc == 0 else "error"
    duration = int(time.time() - t0)

    # Validierung nach dem Sync
    all_issues: dict[str, list[str]] = {}
    if status == "ok":
        for repo in repos:
            val = validate_repo(repo)
            if val["status"] == "error":
                all_issues[repo.get("slug") or str(repo.get("id"))] = val.get("issues", [])
        if all_issues:
            status = "error"
            issues_text = "\n".join(
                [f"  {rid}: {', '.join(issues)}" for rid, issues in all_issues.items()]
            )
            output += f"\n\n⚠️ Validierungsfehler:\n{issues_text}"

    # Activity-Log aktualisieren
    _act_done(act_id, status, duration, output)

    # Status in Store aktualisieren
    for repo in repos:
        repo_id = repo.get("slug") or str(repo.get("id"))
        issues = all_issues.get(repo_id, [])
        store.upsert(
            repo_id,
            {
                "last_status": status,
                "last_run": _now(),
                "last_sync_issues": issues,
            },
        )

    # Benachrichtigung
    if status == "ok":
        _notify("Arch Linux", f"Alle {len(repos)} Repos erfolgreich synchronisiert", True)
    else:
        _notify(
            "Arch Linux",
            f"Sync teilweise fehlgeschlagen ({duration}s)",
            False,
        )

    log.info(f"archlinux.sync_all: abgeschlossen (status={status}, duration={duration}s)")


# ---------------------------------------------------------------------------
# Sync: einzelnes Repo
# ---------------------------------------------------------------------------


def sync_repo(repo_id: str) -> None:
    """Synchronisiert ein einzelnes Arch Linux Repo (blockierend)."""
    from . import store
    from ._sync_engine import SyncEngine, validate_repo

    repo_data = store.get(repo_id)
    if not repo_data:
        log.warning(f"archlinux.sync_repo: Repo nicht gefunden: {repo_id}")
        return

    act_id = _act_start(f"Arch Linux: Sync {repo_id}", item_id=repo_id)
    t0 = time.time()

    log_lines: list[str] = []

    def _flush(line: str) -> None:
        log_lines.append(line)

    # Engine
    from astrapi_mirror._paths import archlinux_mirror_path

    engine = SyncEngine(archlinux_mirror_path())
    repo_with_id = {"id": repo_id, **repo_data}

    # Asynchronen Sync starten (blockierend von Thread aus)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        rc, output = loop.run_until_complete(engine.sync_repo(repo_with_id, on_line=_flush))
    finally:
        loop.close()

    status = "ok" if rc == 0 else "error"
    duration = int(time.time() - t0)
    issues = []

    # Validierung nach dem Sync
    if status == "ok":
        val = validate_repo(repo_with_id)
        if val["status"] == "error":
            status = "error"
            issues = val.get("issues", [])
            issues_text = "\n  ".join(issues)
            output += f"\n\n⚠️ Validierungsfehler:\n  {issues_text}"

    # Activity-Log aktualisieren
    _act_done(act_id, status, duration, output)

    # Status in Store aktualisieren
    store.upsert(
        repo_id,
        {
            "last_status": status,
            "last_run": _now(),
            "last_sync_issues": issues,
        },
    )

    # Benachrichtigung
    label = repo_data.get("label", repo_id)
    if status == "ok":
        _notify("Arch Linux", f"Repo '{label}' erfolgreich synchronisiert", True)
    else:
        _notify("Arch Linux", f"Sync für '{label}' fehlgeschlagen", False)

    log.info(f"archlinux.sync_repo: {repo_id} abgeschlossen (status={status})")


# ---------------------------------------------------------------------------
# Async-Wrapper
# ---------------------------------------------------------------------------


def sync_all_async() -> None:
    """Startet sync_all() im Hintergrund-Thread."""
    threading.Thread(target=sync_all, daemon=True).start()


def sync_repo_async(repo_id: str) -> None:
    """Startet sync_repo() im Hintergrund-Thread."""
    threading.Thread(target=sync_repo, args=(repo_id,), daemon=True).start()
