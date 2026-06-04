"""astrapi_mirror.modules.archlinux._sync_engine.engine – Sync-Engine für Arch Linux."""

import logging
import shutil
import time
from pathlib import Path
from typing import Callable

from astrapi_mirror.modules.debian._sync_engine.versioning import (
    atomic_swap,
    cleanup_old_versions,
    prepare_staging,
)

from .downloader import ArchDownloader
from .validator import test_pacman_sync

log = logging.getLogger(__name__)

_TIMEOUT = 8 * 3600  # 8 Stunden max. (kürzer als Debian)


class SyncEngine:
    """Interne Sync-Engine für Arch Linux Repositories."""

    def __init__(self, mirror_root: Path, partial_root: Path | None = None):
        self.mirror_root = mirror_root
        self.partial_root = partial_root or mirror_root / ".partial"
        self.partial_root.mkdir(parents=True, exist_ok=True)

    async def sync_repos(
        self,
        repos: list[dict],
        on_line: Callable[[str], None] | None = None,
    ) -> tuple[int, str]:
        if not repos:
            msg = "Keine aktivierten Repos zum Synchronisieren"
            log.info(msg)
            return 0, msg

        log.info("sync_repos: %d Repos", len(repos))
        output_lines: list[str] = []

        def _log(line: str) -> None:
            output_lines.append(line)
            if on_line:
                on_line(line)

        failed_repos: list[str] = []

        for repo in repos:
            if not repo.get("enabled", True):
                continue

            repo_id = repo.get("slug") or str(repo.get("id", "unknown"))
            _log(f"\n{'=' * 60}")
            _log(f"Starte Sync: {repo_id}")
            _log(f"{'=' * 60}\n")

            try:
                rc, output = await self.sync_repo(repo, on_line=_log)
                if rc != 0:
                    failed_repos.append(repo_id)
                    _log(f"❌ Sync fehlgeschlagen: {repo_id}\n")
                else:
                    _log(f"✅ Sync erfolgreich: {repo_id}\n")
            except Exception as e:
                log.error("sync_repos: Fehler bei %s: %s", repo_id, e)
                _log(f"❌ Exception bei {repo_id}: {e}\n")
                failed_repos.append(repo_id)

        final_output = "".join(output_lines)
        if failed_repos:
            final_output += f"\n\n⚠️ Fehlgeschlagene Repos: {', '.join(failed_repos)}"
            return 1, final_output

        final_output += "\n\n✅ Alle Repos erfolgreich synchronisiert"
        return 0, final_output

    async def sync_repo(
        self,
        repo: dict,
        on_line: Callable[[str], None] | None = None,
    ) -> tuple[int, str]:
        repo_id = repo.get("slug") or str(repo.get("id", "unknown"))
        url = (repo.get("url") or "").rstrip("/")

        if not url:
            return 1, f"Repo {repo_id}: Keine URL definiert"

        output_lines: list[str] = []

        def _log(line: str) -> None:
            output_lines.append(line)
            if on_line:
                on_line(line)

        _log(f"Repo-ID: {repo_id}")
        _log(f"URL: {url}")
        _log(f"Architekturen: {', '.join(repo.get('architectures', ['x86_64']))}")

        t0 = time.time()

        try:
            # Phase 1: Staging vorbereiten (mit Hardlinks von current → vN)
            _log("\n[1/4] Staging vorbereiten...")
            production_path = self.mirror_root / repo_id
            staging_path = production_path / "staging"

            prepare_staging(production_path, staging_path, _log)

            # Phase 2: Downloaden
            _log("\n[2/4] Dateien herunterladen...")
            downloader = ArchDownloader(
                staging_path=staging_path,
                partial_root=self.partial_root,
                timeout=_TIMEOUT,
                on_line=_log,
            )
            rc = await downloader.download_repo(repo)
            if rc != 0:
                _log("❌ Download fehlgeschlagen")
                return 1, "".join(output_lines)

            # Phase 3: Docker-Test (pacman -Sy)
            _log("\n[3/4] Validierung (pacman -Sy Test)...")
            try:
                docker_ok, docker_msg = test_pacman_sync(repo_id, staging_path)
                if docker_ok:
                    _log("✅ Pacman -Sy Test erfolgreich")
                else:
                    _log(f"⚠️ Pacman -Sy Test fehlgeschlagen: {docker_msg}")
            except Exception as e:
                _log(f"⚠️ Docker nicht verfügbar: {e}")

            # Phase 4: Atomic Swap (staging → vN, current-Symlink → vN)
            _log("\n[4/4] Atomic Swap...")
            try:
                new_version_path = atomic_swap(staging_path, production_path)
                _log(f"✅ Swap erfolgreich zu {new_version_path.name}")

                cleanup_old_versions(production_path, keep=3)

                try:
                    if self.partial_root.exists():
                        shutil.rmtree(self.partial_root)
                        self.partial_root.mkdir(parents=True, exist_ok=True)
                except Exception as exc:
                    _log(f"⚠️ Partial-Cleanup fehlgeschlagen: {exc}")

            except Exception as e:
                _log(f"❌ Swap fehlgeschlagen: {e}")
                return 1, "".join(output_lines)

            duration = int(time.time() - t0)
            _log(f"\n{'=' * 60}")
            _log(f"✅ Sync erfolgreich abgeschlossen in {duration}s")
            _log(f"{'=' * 60}")

            return 0, "".join(output_lines)

        except Exception as e:
            log.exception("sync_repo: unerwarteter Fehler")
            return 1, f"Unerwarteter Fehler: {e}"


def validate_repo(repo: dict, base_path: Path | None = None) -> dict:
    """Validiert ein Arch-Repository.

    Args:
        repo: Repo-Dict mit id/slug, url, architectures
        base_path: Pfad zum Staging-Verzeichnis (optional)

    Returns:
        {'status': 'ok'/'error', 'issues': [...], 'checked_archs': n}
    """
    from astrapi_mirror._paths import archlinux_mirror_path

    if base_path is not None:
        mirror_base = Path(base_path)
    else:
        repo_id = repo.get("slug") or str(repo.get("id", ""))
        repo_root = archlinux_mirror_path() / repo_id / "current"
        if repo_root.exists():
            mirror_base = repo_root
        else:
            return {"status": "error", "issues": ["Repo nicht gefunden"], "checked_archs": 0}

    architectures = repo.get("architectures", ["x86_64"])
    if isinstance(architectures, str):
        architectures = [a.strip() for a in architectures.split(",")]

    issues: list[str] = []
    checked = 0

    # Prüfe pro Architektur
    for arch in architectures:
        arch_path = mirror_base / "os" / arch
        if not arch_path.exists():
            issues.append(f"Architektur-Verzeichnis nicht gefunden: {arch}")
            continue

        # Prüfe auf db.tar.gz
        db_file = arch_path / "*.db.tar.gz"
        import glob

        dbs = glob.glob(str(db_file))
        if not dbs:
            issues.append(f"Keine *.db.tar.gz in {arch} gefunden")
        checked += 1

    status = "ok" if not issues else "error"
    return {"status": status, "issues": issues[:10], "checked_archs": checked}


def client_pacman_snippet(repo: dict, base_url: str) -> str:
    """Erzeugt einen pacman.conf-Snippet für ein Arch-Repo."""
    base_url = base_url.rstrip("/")
    repo_id = repo.get("slug") or str(repo.get("id", ""))
    mirror_url = f"{base_url}/repo/arch/{repo_id}"

    lines = [
        f"# {repo.get('label', repo_id)} – Mirror",
        f"[{repo_id}]",
        f"Server = {mirror_url}/$arch",
    ]

    return "\n".join(lines) + "\n"
