"""astrapi_mirror.modules.debian.jobs – Sync via run_logged/run_all."""

import threading
import urllib.request
from datetime import datetime

from astrapi_core.system.logger import log, log_context
from astrapi_core.system.runner import run_all, run_logged


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


_MAX_RETRIES = 5


def _armor_binary_key(raw: bytes) -> str:
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

    b64 = base64.encodebytes(raw).decode("ascii")
    crc = base64.b64encode(_crc24(raw).to_bytes(3, "big")).decode("ascii")
    return (
        "-----BEGIN PGP PUBLIC KEY BLOCK-----\n\n"
        + b64
        + "="
        + crc
        + "\n"
        + "-----END PGP PUBLIC KEY BLOCK-----\n"
    )


def _fetch_gpg_key(repo_id: str, url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "astrapi-mirror/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw: bytes = resp.read()
    except Exception as e:
        log("WARNING", f"GPG-Key {repo_id}: Download fehlgeschlagen: {e}")
        return None

    if raw.lstrip().startswith(b"-----BEGIN PGP PUBLIC KEY BLOCK-----"):
        return raw.decode("ascii", errors="replace")
    return _armor_binary_key(raw)


def _important(line: str) -> bool:
    """True für Zeilen die ins Activity-Log gehören (Phasen, Fehler, Zusammenfassungen)."""
    s = line.strip()
    if not s:
        return False
    return any(m in s for m in (
        "❌", "⚠️",
        "[1/", "[2/", "[3/", "[4/", "[5/",
        "📊 Download-Statistik",
        "📦 Suite:", "📦 Pool:",
        "✅ Sync erfolgreich", "✅ Validierung", "✅ Swap",
        "Repo-ID:", "URL:", "Suites:", "Komponenten:", "Architekturen:",
        "Fehlgeschlagene Repos",
    ))


# ---------------------------------------------------------------------------
# run_single – wird von run_all/run_logged pro Repo aufgerufen
# ---------------------------------------------------------------------------


def run_single(repo_id: str, repo: dict | None = None) -> None:
    """Synchronisiert ein einzelnes Debian-Repo (blockierend, für run_all/run_logged)."""
    from . import store
    from ._sync_engine import SyncEngine
    from ._sync_engine.validator import validate_repo

    if repo is None:
        repo = store.get(repo_id)
    if not repo:
        log("ERROR", f"Debian Repo '{repo_id}' nicht gefunden")
        return

    slug = repo.get("slug", repo_id)

    with log_context("debian", repo_id):
        log("INFO", f"=== Debian Repo '{slug}' synchronisieren ===")
        store.upsert(repo_id, {"last_status": "syncing"})

        def _on_line(line: str) -> None:
            if _important(line):
                level = "ERROR" if "❌" in line else "WARNING" if "⚠️" in line else "INFO"
                log(level, line.strip())

        engine = SyncEngine()
        rc, _ = engine.sync_repo(repo, on_line=_on_line)

        if rc != 0:
            log("ERROR", "Sync fehlgeschlagen")
            store.upsert(repo_id, {"last_status": "error", "last_run": _now(), "last_sync_issues": []})
            return

        val = validate_repo(repo)
        if val["status"] == "error":
            for issue in val["issues"][:10]:
                log("ERROR", issue)
            store.upsert(repo_id, {
                "last_status": "error",
                "last_run": _now(),
                "last_sync_issues": val.get("issues", []),
            })
            return

        gpg_url = (repo.get("gpg_key_url") or "").strip()
        if gpg_url:
            key = _fetch_gpg_key(slug, gpg_url)
            if key:
                store.upsert(repo_id, {"gpg_key": key})

        from astrapi_mirror._paths import mirror_path
        from astrapi_mirror._repo_info import repo_info

        info = repo_info(mirror_path() / slug, pkg_suffixes=(".deb",))
        store.upsert(repo_id, {
            "last_status": "ok",
            "last_run": _now(),
            "last_sync_issues": [],
            "last_info": info,
        })
        log("INFO", f"=== Debian Repo '{slug}' erfolgreich synchronisiert ===")


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
                "debian",
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
            title=f"Debian Mirror: {len(failed_ids)} Repo(s) nicht synchronisierbar",
            message=body,
            event=_ne.ERROR,
            source="debian",
            tags=["mirror", "sync-fehler"],
        )
    except Exception as e:
        log("WARNING", f"ntfy-Benachrichtigung fehlgeschlagen: {e}")


def sync_all() -> None:
    """Synchronisiert alle aktivierten Debian-Repos (blockierend)."""
    from . import store

    repos = {
        str(k): {**v, "id": k}
        for k, v in store.list().items()
        if v.get("enabled", True)
    }
    if not repos:
        return

    run_all("debian", repos, run_single, desc_fn=lambda iid, e: e.get("label", iid))
    _retry_failed(repos)


def sync_repo(repo_id: str) -> None:
    """Synchronisiert ein einzelnes Debian-Repo (blockierend)."""
    from . import store

    repo = store.get(repo_id)
    if not repo:
        return
    run_logged("debian", repo_id, repo.get("label", repo_id),
               lambda: run_single(repo_id, repo))


# ---------------------------------------------------------------------------
# Async-Wrapper
# ---------------------------------------------------------------------------


def sync_all_async() -> None:
    threading.Thread(target=sync_all, daemon=True).start()


def sync_repo_async(repo_id: str) -> None:
    threading.Thread(target=sync_repo, args=(repo_id,), daemon=True).start()
