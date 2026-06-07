"""astrapi_mirror.modules.archlinux.jobs – Hintergrund-Sync für Arch Linux."""

import asyncio
import logging
import threading
import time
import traceback
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
    output = ""
    status = "error"
    per_repo_rc: dict[str, int] = {}
    all_issues: dict[str, list[str]] = {}

    try:
        from astrapi_mirror._paths import archlinux_mirror_path

        engine = SyncEngine(archlinux_mirror_path())
        output_lines: list[str] = []

        async def _run_all() -> None:
            for repo in repos:
                repo_id = repo.get("slug") or str(repo.get("id", "unknown"))
                output_lines.append(f"\n{'=' * 60}")
                output_lines.append(f"Starte Sync: {repo_id}")
                output_lines.append(f"{'=' * 60}\n")
                try:
                    rc, repo_out = await engine.sync_repo(repo, on_line=None)
                    output_lines.append(repo_out)
                    per_repo_rc[repo_id] = rc
                    output_lines.append(
                        f"{'✅' if rc == 0 else '❌'} {repo_id}\n"
                    )
                except Exception:
                    per_repo_rc[repo_id] = 1
                    output_lines.append(
                        f"❌ Exception bei {repo_id}:\n{traceback.format_exc()}\n"
                    )

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_run_all())
        finally:
            loop.close()

        output = "\n".join(output_lines)
        failed = [rid for rid, rc in per_repo_rc.items() if rc != 0]
        status = "ok" if not failed else "error"

        # Validierung pro erfolgreich synchronisiertem Repo
        for repo in repos:
            repo_id = repo.get("slug") or str(repo.get("id"))
            if per_repo_rc.get(repo_id, 1) != 0:
                continue
            val = validate_repo(repo)
            if val["status"] == "error":
                all_issues[repo_id] = val.get("issues", [])

        if all_issues:
            if status == "ok":
                status = "error"
            issues_text = "\n".join(
                f"  {rid}: {', '.join(issues)}" for rid, issues in all_issues.items()
            )
            output += f"\n\n⚠️ Validierungsfehler:\n{issues_text}"

        # Status pro Repo speichern
        _ts = _now()
        for repo in repos:
            repo_id = repo.get("slug") or str(repo.get("id"))
            rc = per_repo_rc.get(repo_id, 1)
            issues = all_issues.get(repo_id, [])
            repo_status = "error" if (rc != 0 or issues) else "ok"
            store.upsert(
                repo_id,
                {
                    "last_status": repo_status,
                    "last_run": _ts,
                    "last_sync_issues": issues,
                },
            )

    except Exception:
        tb = traceback.format_exc()
        log.exception("archlinux.sync_all: unerwarteter Fehler")
        output += f"\n\n=== EXCEPTION ===\n{tb}"
        status = "error"

    finally:
        duration = int(time.time() - t0)
        _act_done(act_id, status, duration, output)

    # Benachrichtigung
    if status == "ok":
        _notify("Arch Linux", f"Alle {len(repos)} Repos erfolgreich synchronisiert", True)
    else:
        failed_ids = [rid for rid, rc in per_repo_rc.items() if rc != 0] + list(all_issues)
        _notify(
            "Arch Linux",
            f"Sync teilweise fehlgeschlagen: {', '.join(dict.fromkeys(failed_ids))} ({duration}s)",
            False,
        )

    log.info("archlinux.sync_all: abgeschlossen (status=%s, duration=%ds)", status, duration)


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
    output = ""
    status = "error"
    issues: list[str] = []

    try:
        from astrapi_mirror._paths import archlinux_mirror_path

        engine = SyncEngine(archlinux_mirror_path())
        repo_with_id = {"id": repo_id, **repo_data}

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            rc, output = loop.run_until_complete(engine.sync_repo(repo_with_id, on_line=None))
        finally:
            loop.close()

        status = "ok" if rc == 0 else "error"

        if status == "ok":
            val = validate_repo(repo_with_id)
            if val["status"] == "error":
                status = "error"
                issues = val.get("issues", [])
                issues_text = "\n  ".join(issues)
                output += f"\n\n⚠️ Validierungsfehler:\n  {issues_text}"

        store.upsert(
            repo_id,
            {
                "last_status": status,
                "last_run": _now(),
                "last_sync_issues": issues,
            },
        )

    except Exception:
        tb = traceback.format_exc()
        log.exception("archlinux.sync_repo: unerwarteter Fehler bei '%s'", repo_id)
        output += f"\n\n=== EXCEPTION ===\n{tb}"
        status = "error"
        store.upsert(repo_id, {"last_status": "error"})

    finally:
        duration = int(time.time() - t0)
        _act_done(act_id, status, duration, output)

    label = repo_data.get("label", repo_id)
    if status == "ok":
        _notify("Arch Linux", f"Repo '{label}' erfolgreich synchronisiert", True)
    else:
        _notify("Arch Linux", f"Sync für '{label}' fehlgeschlagen", False)

    log.info("archlinux.sync_repo: %s abgeschlossen (status=%s)", repo_id, status)


# ---------------------------------------------------------------------------
# Async-Wrapper
# ---------------------------------------------------------------------------


def sync_all_async() -> None:
    """Startet sync_all() im Hintergrund-Thread."""
    threading.Thread(target=sync_all, daemon=True).start()


def sync_repo_async(repo_id: str) -> None:
    """Startet sync_repo() im Hintergrund-Thread."""
    threading.Thread(target=sync_repo, args=(repo_id,), daemon=True).start()
