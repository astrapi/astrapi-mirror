"""astrapi_mirror.modules.debian.jobs – Hintergrund-Sync via refrapt."""

import logging
import subprocess
import tempfile
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_TIMEOUT = 12 * 3600  # 12 Stunden max.


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _run_refrapt(conf_path: str, on_line=None) -> tuple[int, str]:
    """Führt refrapt aus und gibt (returncode, output) zurück."""
    cmd = ["refrapt", "--conf", conf_path]
    log.info("debian.sync: %s", " ".join(cmd))
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        chunks: list[str] = []
        deadline = time.time() + _TIMEOUT
        for line in proc.stdout:
            chunks.append(line)
            if on_line:
                on_line(line)
            if time.time() > deadline:
                proc.kill()
                return 1, f"Timeout nach {_TIMEOUT}s\n{''.join(chunks)}"
        proc.wait()
        return proc.returncode, "".join(chunks)
    except FileNotFoundError:
        return 1, "refrapt nicht gefunden – ist es installiert? (pip install refrapt)"
    except Exception as e:
        return 1, str(e)


def _act_start(label: str, item_id: str | None = None) -> int | None:
    try:
        from astrapi_core.system.activity_log import log_activity
        return log_activity("job", "debian", label, status="running", item_id=item_id)
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


def _fetch_gpg_key(repo_id: str, url: str) -> str | None:
    """Lädt einen GPG-Schlüssel von url herunter; gibt den Inhalt zurück oder None."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "astrapi-mirror/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode(errors="replace")
    except Exception as e:
        log.warning("debian.gpg_key: %s – Download fehlgeschlagen: %s", repo_id, e)
        return None


def _notify(title: str, message: str, ok: bool) -> None:
    try:
        from astrapi_core.modules.notify import engine as _n
        _n.send(
            title=title,
            message=message,
            event=_n.SUCCESS if ok else _n.ERROR,
            source="debian",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Sync: alle aktivierten Repos
# ---------------------------------------------------------------------------

def sync_all() -> None:
    """Synchronisiert alle aktivierten Debian-Repos via refrapt (blockierend)."""
    from .storage import store
    from .engine import generate_refrapt_config, validate_all

    repos_raw = store.list()
    repos = [{"id": k, **v} for k, v in repos_raw.items() if v.get("enabled", True)]

    if not repos:
        log.info("debian.sync_all: keine aktivierten Repos")
        return

    act_id = _act_start("Debian: Alle Repos syncen")
    t0 = time.time()

    config_text = generate_refrapt_config(repos)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".conf", prefix="refrapt-", delete=False
    ) as tmp:
        tmp.write(config_text)
        conf_path = tmp.name

    log_lines: list[str] = []

    def _flush(line: str) -> None:
        log_lines.append(line)
        store.upsert("__sync_status__", {"last_log": "".join(log_lines[-500:])})

    rc, output = _run_refrapt(conf_path, on_line=_flush)

    try:
        Path(conf_path).unlink(missing_ok=True)
    except Exception:
        pass

    status = "ok" if rc == 0 else "error"
    duration = int(time.time() - t0)

    # Validierung nach dem Sync
    validation = {}
    if status == "ok":
        validation = validate_all(repos)
        failed = [rid for rid, r in validation.items() if r["status"] == "error"]
        if failed:
            status = "error"
            output += f"\n\nValidierung fehlgeschlagen für: {', '.join(failed)}"

    # GPG-Schlüssel herunterladen
    for repo in repos:
        url = repo.get("gpg_key_url", "").strip()
        if url:
            key = _fetch_gpg_key(repo["id"], url)
            if key:
                store.upsert(repo["id"], {"gpg_key": key})

    # Status pro Repo speichern
    _ts = _now()
    for repo_id in repos_raw:
        v = validation.get(repo_id, {})
        _s = v.get("status", status)
        store.upsert(repo_id, {
            "last_run": _ts,
            "last_status": _s,
            "last_sync_issues": v.get("issues", []),
        })

    _act_done(act_id, status, duration, output)
    _notify(
        f"Debian Mirror Sync {'erfolgreich' if status == 'ok' else 'fehlgeschlagen'}",
        f"{len(repos)} Repos, {duration}s",
        status == "ok",
    )
    log.info("debian.sync_all: %s (%ds)", status, duration)


# ---------------------------------------------------------------------------
# Sync: einzelnes Repo
# ---------------------------------------------------------------------------

def sync_repo(repo_id: str) -> None:
    """Synchronisiert ein einzelnes Repo (blockierend)."""
    from .storage import store
    from .engine import generate_refrapt_config, validate_repo

    repo_data = store.get(repo_id)
    if not repo_data:
        log.warning("debian.sync_repo: '%s' nicht gefunden", repo_id)
        return

    repo = {"id": repo_id, **repo_data}
    act_id = _act_start(f"Debian: {repo_id} syncen", item_id=repo_id)
    t0 = time.time()

    store.upsert(repo_id, {"last_status": "syncing"})

    config_text = generate_refrapt_config([repo])
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".conf", prefix="refrapt-", delete=False
    ) as tmp:
        tmp.write(config_text)
        conf_path = tmp.name

    rc, output = _run_refrapt(conf_path)

    try:
        Path(conf_path).unlink(missing_ok=True)
    except Exception:
        pass

    status = "ok" if rc == 0 else "error"
    duration = int(time.time() - t0)

    validation: dict = {}
    if status == "ok":
        validation = validate_repo(repo)
        if validation["status"] == "error":
            status = "error"
            issues_text = "\n".join(validation["issues"][:20])
            output += f"\n\nValidierung:\n{issues_text}"

    # GPG-Schlüssel herunterladen
    gpg_url = repo_data.get("gpg_key_url", "").strip()
    if gpg_url:
        key = _fetch_gpg_key(repo_id, gpg_url)
        if key:
            store.upsert(repo_id, {"gpg_key": key})

    store.upsert(repo_id, {
        "last_run": _now(),
        "last_status": validation.get("status", status),
        "last_sync_issues": validation.get("issues", []),
    })

    _act_done(act_id, status, duration, output)
    _notify(
        f"Debian: {repo_id} {'✓' if status == 'ok' else '✗'}",
        output[-400:].strip() if status == "error" else f"{duration}s",
        status == "ok",
    )
    log.info("debian.sync_repo: %s → %s (%ds)", repo_id, status, duration)


# ---------------------------------------------------------------------------
# Async-Wrapper
# ---------------------------------------------------------------------------

def sync_all_async() -> None:
    threading.Thread(target=sync_all, daemon=True).start()


def sync_repo_async(repo_id: str) -> None:
    threading.Thread(target=sync_repo, args=(repo_id,), daemon=True).start()
