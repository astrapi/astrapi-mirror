"""astrapi_mirror.modules.debian.jobs – Hintergrund-Sync via interne Engine."""

import logging
import threading
import time
import urllib.request
from datetime import datetime

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


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


def _armor_binary_key(raw: bytes) -> str:
    """Konvertiert einen binären OpenPGP-Schlüssel in ASCII-armored Format (Pure Python).

    Fallback wenn ``gpg --armor`` nicht verfügbar oder fehlgeschlagen ist.
    """
    import base64

    def _crc24(data: bytes) -> int:
        crc = 0xB704CE
        for byte in data:
            crc ^= byte << 16
            for _ in range(8):
                crc <<= 1
                if crc & 0x1000000:
                    crc ^= 0x1864CFB
        return crc & 0xFFFFFF

    b64 = base64.encodebytes(raw).decode("ascii")  # auto-wrapped at 76 Zeichen
    crc = base64.b64encode(_crc24(raw).to_bytes(3, "big")).decode("ascii")
    return (
        "-----BEGIN PGP PUBLIC KEY BLOCK-----\n\n"
        + b64
        + "=" + crc + "\n"
        + "-----END PGP PUBLIC KEY BLOCK-----\n"
    )


def _fetch_gpg_key(repo_id: str, url: str) -> str | None:
    """Lädt einen GPG-Schlüssel herunter und gibt ihn als armored ASCII zurück.

    Binäre Keyring-Dateien (.gpg) werden via ``gpg --armor`` konvertiert.
    Schlägt die Konvertierung fehl, wird der Rohinhalt zurückgegeben (nicht
    inline einbettbar, aber als Datei-Referenz noch verwendbar).
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "astrapi-mirror/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw: bytes = resp.read()
    except Exception as e:
        log.warning("debian.gpg_key: %s – Download fehlgeschlagen: %s", repo_id, e)
        return None

    # Bereits armored ASCII?
    if raw.lstrip().startswith(b"-----BEGIN PGP PUBLIC KEY BLOCK-----"):
        return raw.decode("ascii", errors="replace")

    # Binär → via gpg --armor konvertieren
    try:
        import subprocess

        result = subprocess.run(
            ["gpg", "--armor"],
            input=raw,
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            log.debug("debian.gpg_key: %s – binary key armoriert", repo_id)
            return result.stdout.decode("ascii")
        log.warning(
            "debian.gpg_key: %s – gpg --armor fehlgeschlagen: %s",
            repo_id,
            result.stderr.decode(errors="replace"),
        )
    except Exception as e:
        log.warning("debian.gpg_key: %s – gpg nicht verfügbar: %s", repo_id, e)

    # Pure-Python-Fallback: CRC-24-korrektes ASCII-Armor ohne gpg-Befehl
    log.info("debian.gpg_key: %s – verwende Pure-Python-Armor-Konvertierung", repo_id)
    return _armor_binary_key(raw)


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
    """Synchronisiert alle aktivierten Debian-Repos via interne Engine (blockierend)."""
    from . import store
    from ._sync_engine import SyncEngine
    from .engine import validate_all

    repos_raw = store.list()
    repos = [v for v in repos_raw.values() if v.get("enabled", True)]

    if not repos:
        log.info("debian.sync_all: keine aktivierten Repos")
        return

    act_id = _act_start("Debian: Alle Repos syncen")
    t0 = time.time()

    log_lines: list[str] = []

    def _flush(line: str) -> None:
        log_lines.append(line)

    # Nutze neue SyncEngine
    engine = SyncEngine()
    rc, output = engine.sync_repos(repos, on_line=_flush)

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
            key = _fetch_gpg_key(repo.get("slug", str(repo["id"])), url)
            if key:
                store.upsert(str(repo["id"]), {"gpg_key": key})

    # Status pro Repo speichern
    _ts = _now()
    for repo in repos:
        slug = repo.get("slug") or str(repo["id"])
        v = validation.get(slug, {})
        _s = v.get("status", status)
        store.upsert(
            str(repo["id"]),
            {
                "last_run": _ts,
                "last_status": _s,
                "last_sync_issues": v.get("issues", []),
            },
        )

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
    """Synchronisiert ein einzelnes Repo via interne Engine (blockierend)."""
    from . import store
    from ._sync_engine import SyncEngine
    from .engine import validate_repo

    repo_data = store.get(repo_id)
    if not repo_data:
        log.warning("debian.sync_repo: '%s' nicht gefunden", repo_id)
        return

    repo = repo_data
    act_id = _act_start(f"Debian: {repo.get('slug', repo_id)} syncen", item_id=repo_id)
    t0 = time.time()

    store.upsert(repo_id, {"last_status": "syncing"})

    log_lines: list[str] = []

    def _flush(line: str) -> None:
        log_lines.append(line)

    # Nutze neue SyncEngine
    engine = SyncEngine()
    rc, output = engine.sync_repo(repo, on_line=_flush)

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

    store.upsert(
        repo_id,
        {
            "last_run": _now(),
            "last_status": validation.get("status", status),
            "last_sync_issues": validation.get("issues", []),
        },
    )

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
