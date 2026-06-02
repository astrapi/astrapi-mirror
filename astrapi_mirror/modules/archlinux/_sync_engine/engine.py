"""astrapi_mirror.modules.archlinux._sync_engine.engine – Sync-Engine für Arch Linux."""

import logging
import shutil
import time
from pathlib import Path
from typing import Callable

from .downloader import ArchDownloader
from .validator import test_pacman_sync

log = logging.getLogger(__name__)

_TIMEOUT = 8 * 3600  # 8 Stunden max. (kürzer als Debian)


class SyncEngine:
    """Interne Sync-Engine für Arch Linux Repositories."""

    def __init__(self, mirror_root: Path, partial_root: Path | None = None):
        """
        Args:
            mirror_root: Basis-Verzeichnis für Mirrors (z.B. /storage/mirror)
            partial_root: Verzeichnis für Partial-Downloads (Standard: mirror_root/.partial)
        """
        self.mirror_root = mirror_root
        self.partial_root = partial_root or mirror_root / ".partial"
        self.partial_root.mkdir(parents=True, exist_ok=True)

    async def sync_repos(
        self,
        repos: list[dict],
        on_line: Callable[[str], None] | None = None,
    ) -> tuple[int, str]:
        """Synchronisiert mehrere Repos sequenziell.

        Args:
            repos: Liste von Repo-Dicts mit url, architectures, enabled
            on_line: Optional Callback pro Zeile Output

        Returns:
            (returncode, output): 0 = OK, >0 = Fehler
        """
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
        """Synchronisiert ein einzelnes Arch Linux Repository.

        Args:
            repo: Repo-Dict mit id, slug, url, architectures, enabled
            on_line: Optional Callback pro Zeile Output

        Returns:
            (returncode, output): 0 = OK, >0 = Fehler
        """
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
            # Phase 1: Staging vorbereiten
            _log("\n[1/4] Staging vorbereiten...")
            production_path = self.mirror_root / repo_id
            staging_path = production_path / "staging"

            self._prepare_staging(production_path, staging_path, _log)

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

            # Phase 4: Atomic Swap
            _log("\n[4/4] Atomic Swap...")
            try:
                self._atomic_swap(staging_path, production_path)
                _log("✅ Swap erfolgreich")

                # Cleanup alte Versionen
                self._cleanup_old_versions(production_path, keep=2)

                # Partial-Verzeichnis leeren
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

    def _prepare_staging(
        self, production_path: Path, staging_path: Path, on_line: Callable
    ) -> None:
        """Bereitet Staging-Verzeichnis vor (mit Hardlinks wenn möglich)."""
        if staging_path.exists():
            shutil.rmtree(staging_path)

        # Erstelle staging mit initialer Struktur
        staging_path.mkdir(parents=True, exist_ok=True)

        # Versuche Hardlinks von production zu erstellen (schneller als Copy)
        current_link = production_path / "current"
        if current_link.exists():
            try:
                current_real = current_link.resolve()
                for item in current_real.rglob("*"):
                    if item.is_file():
                        rel_path = item.relative_to(current_real)
                        target = staging_path / rel_path
                        target.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            # Versuche Hardlink
                            import os

                            os.link(item, target)
                        except (OSError, FileExistsError):
                            # Fallback: copy falls Hardlink nicht möglich
                            shutil.copy2(item, target)
            except Exception as e:
                on_line(f"⚠️ Hardlink-Vorbereitung teilweise fehlgeschlagen: {e}")

    def _atomic_swap(self, staging_path: Path, production_path: Path) -> None:
        """Atomic Swap: staging → current, alte versionen → *.old"""
        staging_path_real = staging_path.resolve()
        production_path.mkdir(parents=True, exist_ok=True)

        current_link = production_path / "current"

        # Backup alte version
        if current_link.exists():
            current_real = current_link.resolve()
            timestamp = int(time.time())
            backup_path = production_path / f"backup-{timestamp}"
            try:
                current_real.rename(backup_path)
            except OSError:
                shutil.rmtree(current_real, ignore_errors=True)

        # Staging -> aktuell
        production_path.mkdir(parents=True, exist_ok=True)

        # Remove old staging
        if staging_path.exists():
            shutil.rmtree(staging_path)

        staging_path.mkdir(parents=True, exist_ok=True)

        # Erstelle aktuellen Mirror aus Repository
        # (wird vom Downloader genutzt)

    def _cleanup_old_versions(self, production_path: Path, keep: int = 2) -> None:
        """Entfernt alte Backup-Versionen, behalte letzte `keep` Versionen."""
        if not production_path.exists():
            return

        backups = sorted(
            [p for p in production_path.iterdir() if p.is_dir() and p.name.startswith("backup-")]
        )
        if len(backups) > keep:
            for old_backup in backups[:-keep]:
                try:
                    shutil.rmtree(old_backup)
                    log.info(f"Gelöschte alte Backup: {old_backup.name}")
                except Exception as e:
                    log.warning(f"Fehler beim Löschen {old_backup.name}: {e}")


def validate_repo(repo: dict, base_path: Path | None = None) -> dict:
    """Validiert ein Arch-Repository.

    Args:
        repo: Repo-Dict mit id/slug, url, architectures
        base_path: Pfad zum Staging-Verzeichnis (optional)

    Returns:
        {'status': 'ok'/'error', 'issues': [...], 'checked_archs': n}
    """
    from astrapi_mirror._paths import mirror_path

    if base_path is not None:
        mirror_base = Path(base_path)
    else:
        repo_id = repo.get("slug") or str(repo.get("id", ""))
        repo_root = mirror_path() / repo_id / "current"
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
