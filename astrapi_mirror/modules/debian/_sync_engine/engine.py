"""astrapi_mirror.modules.debian._sync_engine.engine – Hauptlogik der Sync-Engine."""

import logging
import shutil
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .downloader import FileDownloader
from .validator import test_apt_update
from .versioning import atomic_swap, cleanup_old_versions, prepare_staging

log = logging.getLogger(__name__)

_TIMEOUT = 12 * 3600  # 12 Stunden max.


class SyncEngine:
    """Interne Sync-Engine für Debian-Mirror (refrapt-Ersatz)."""

    def __init__(self, mirror_root: Path, partial_root: Path | None = None):
        """
        Args:
            mirror_root: Basis-Verzeichnis für Mirrors (z.B. /var/lib/mirror)
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
        """Synchronisiert mehrere Repos parallel (mit Limit).

        Args:
            repos: Liste von Repo-Dicts (id, url, suites, components, etc.)
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

        # Synchronisiere Repos sequenziell (um Ressourcen zu sparen)
        # aber mit parallelem Download innerhalb jedes Repos
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
        """Synchronisiert ein einzelnes Repo.

        Args:
            repo: Repo-Dict (id, url, suites, components, architectures, repo_type, etc.)
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
        _log(f"Typ: {repo.get('repo_type', 'deb')}")
        _log(f"Suites: {', '.join(repo.get('suites', []))}")
        _log(f"Komponenten: {', '.join(repo.get('components', []))}")
        _log(f"Architekturen: {', '.join(repo.get('architectures', []))}")

        t0 = time.time()

        try:
            # Phase 1: Staging vorbereiten (mit Hardlinks)
            _log("\n[1/5] Staging vorbereiten...")
            # Repo-ID als Verzeichnisname – kürzer und stabil
            production_path = self.mirror_root / repo_id
            staging_path = production_path / "staging"

            prepare_staging(production_path, staging_path, _log)

            # Phase 2: Downloaden
            _log("\n[2/5] Dateien herunterladen...")
            downloader = FileDownloader(
                staging_path=staging_path,
                partial_root=self.partial_root,
                timeout=_TIMEOUT,
                on_line=_log,
            )
            rc = await downloader.download_repo(repo)
            if rc != 0:
                _log("❌ Download fehlgeschlagen")
                return 1, "".join(output_lines)

            # Phase 3: Manifest-Validierung (gegen Staging, nicht live)
            _log("\n[3/5] Manifest validieren...")
            from ..engine import validate_repo

            validation = validate_repo({"id": repo_id, **repo}, base_path=staging_path)
            if validation["status"] == "error":
                issues_text = "\n  ".join(validation.get("issues", [])[:10])
                _log(f"❌ Validierung fehlgeschlagen:\n  {issues_text}")
                return 1, "".join(output_lines)
            _log(f"✅ Validierung OK ({validation.get('checked_suites', 0)} Suites)")

            # Phase 4: Docker-Test (optional)
            docker_status = "ok"
            try:
                _log("\n[4/5] Docker apt-Test...")
                docker_ok, docker_msg = test_apt_update(repo_id, staging_path)
                if docker_ok:
                    _log("✅ Docker apt-Test erfolgreich")
                else:
                    _log(f"⚠️ Docker apt-Test fehlgeschlagen: {docker_msg}")
                    docker_status = "warning"
            except Exception as e:
                _log(f"⚠️ Docker nicht verfügbar: {e}")

            # Phase 5: Atomic Swap
            _log("\n[5/5] Atomic Swap...")
            try:
                new_version_path = atomic_swap(staging_path, production_path)
                _log(f"✅ Swap erfolgreich zu {new_version_path.name}")

                # Cleanup alte Versionen
                cleanup_old_versions(production_path, keep=3)

                # Partial-Verzeichnis leeren: erfolgreich heruntergeladene
                # Dateien wurden bereits verschoben; restliche Fragmente
                # sind veraltet und behindern bei einem Upstream-Update
                # die korrekte Checksummen-Validierung.
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
            log.exception("sync_repo: %s", repo_id)
            _log(f"\n❌ Exception: {e}")
            return 1, "".join(output_lines)

    @staticmethod
    def _host_path_from_url(url: str) -> str:
        """Extrahiert 'hostname/pfad' aus einer URL (nicht mehr für Pfade genutzt)."""
        parsed = urlparse(url)
        host = parsed.hostname or ""
        path = parsed.path.rstrip("/") or ""
        if path:
            return f"{host}{path}"
        return host
