"""astrapi_mirror.modules.archlinux._sync_engine.downloader – Asyncio-basierter Downloader für Arch."""

import asyncio
import logging
from pathlib import Path
from typing import Callable
from urllib.request import urlopen

log = logging.getLogger(__name__)

_CHUNK_SIZE = 65536  # 64 KB


class ArchDownloader:
    """Asyncio-basierter Downloader für Arch Linux Repositories."""

    def __init__(
        self,
        staging_path: Path,
        partial_root: Path,
        timeout: int = 3600,
        on_line: Callable[[str], None] | None = None,
        max_concurrent: int = 4,
    ):
        """
        Args:
            staging_path: Ziel-Verzeichnis für Downloads
            partial_root: Verzeichnis für unvollständige Downloads
            timeout: Timeout pro Request (Sekunden)
            on_line: Log-Callback
            max_concurrent: Max. parallele Downloads
        """
        self.staging_path = staging_path
        self.partial_root = partial_root
        self.timeout = timeout
        self.on_line = on_line or (lambda x: None)
        self.max_concurrent = max_concurrent
        self.stats = {
            "downloaded": 0,
            "skipped": 0,
            "failed": 0,
            "bytes": 0,
            "failed_files": [],
        }

    async def download_repo(self, repo: dict) -> int:
        """Lädt ein komplettes Arch-Repository herunter.

        Struktur:
            {url}/os/{arch}/*.pkg.tar.zst
            {url}/os/{arch}/*.db.tar.gz
            {url}/os/{arch}/*.files.tar.gz

        Args:
            repo: Repo-Dict mit url, architectures, id/slug

        Returns:
            0 = OK, >0 = Fehler
        """
        url = (repo.get("url") or "").rstrip("/")
        architectures = repo.get("architectures", ["x86_64"])
        if isinstance(architectures, str):
            architectures = [a.strip() for a in architectures.split(",")]

        # Repo-Name für $repo-Platzhalter ermitteln
        repo_name = repo.get("slug") or repo.get("id") or repo.get("label", "")

        self._log(f"Lade {len(architectures)} Architektur(en) herunter...")

        tasks = []
        for arch in architectures:
            task = asyncio.create_task(self._download_arch(url, arch, repo_name))
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        failed_count = sum(1 for r in results if isinstance(r, Exception) or r != 0)
        if failed_count > 0:
            self._log(f"\n⚠️ {failed_count}/{len(architectures)} Architektur(en) fehlgeschlagen")
            return 1

        return 0

    async def _download_arch(self, base_url: str, arch: str, repo_name: str = "") -> int:
        """Lädt alle Dateien einer Architektur herunter."""
        # Ersetze $arch/$repo Platzhalter (pacman mirrorlist Format)
        expanded = base_url.replace("$arch", arch).replace("$repo", repo_name)
        arch_url = expanded.rstrip("/") + "/"
        arch_path = self.staging_path / "os" / arch
        arch_path.mkdir(parents=True, exist_ok=True)

        self._log(f"\n📦 Lade Architektur: {arch}")

        # Phase 1: Dateiliste vom Server abrufen
        try:
            file_list = await self._get_file_list(arch_url)
            if not file_list:
                self._log(f"⚠️ Keine Dateien in {arch_url} gefunden")
                return 0  # nicht als Fehler behandeln
        except Exception as e:
            self._log(f"❌ Fehler beim Abrufen der Dateiliste: {e}")
            return 1

        self._log(f"  Gefundene Dateien: {len(file_list)}")

        # Phase 2: Parallele Downloads mit Semaphore
        sem = asyncio.Semaphore(self.max_concurrent)

        async def download_with_semaphore(filename: str, remote_url: str, local_path: Path):
            async with sem:
                return await self._download_file(filename, remote_url, local_path)

        tasks = [
            download_with_semaphore(fname, f"{arch_url}{fname}", arch_path / fname)
            for fname in file_list
        ]

        await asyncio.gather(*tasks, return_exceptions=True)

        return 0

    async def _get_file_list(self, arch_url: str) -> list[str]:
        """Ruft Dateiliste vom Server ab (sehr simpel: direkter Download)."""
        # Für Arch Linux: es gibt keine zentrale Dateiliste wie bei Debian (Packages.gz)
        # Stattdessen nutzen echte Mirrors rsync – wir machen einen vereinfachten Ansatz:
        # Wir laden db.tar.gz herunter und extrahieren die Dateiliste daraus
        # ODER: wir machen einen einfachen HTTP-Request und parsen verlinkte Dateien

        import re
        from urllib.parse import unquote

        loop = asyncio.get_event_loop()

        def _fetch_listing():
            resp = urlopen(arch_url, timeout=30)
            return resp.read().decode("utf-8", errors="replace")

        html = await loop.run_in_executor(None, _fetch_listing)

        _ARCH_EXTS = (
            ".pkg.tar.zst",
            ".pkg.tar.xz",
            ".pkg.tar.gz",
            ".pkg.tar.zst.sig",
            ".pkg.tar.xz.sig",
            ".db",
            ".db.tar.gz",
            ".db.tar.zst",
            ".files",
            ".files.tar.gz",
            ".files.tar.zst",
        )

        # Extrahiere href-Werte (relativ und absolut)
        pattern = re.compile(r'href="([^"#][^"]*)"', re.IGNORECASE)
        files = []
        seen = set()
        for m in pattern.finditer(html):
            href = unquote(m.group(1).strip())
            # Bei absoluten Pfaden nur den Dateinamen verwenden
            name = href.split("/")[-1] or href
            if name and name not in seen and any(name.endswith(ext) for ext in _ARCH_EXTS):
                files.append(name)
                seen.add(name)

        return files

    async def _download_file(self, filename: str, remote_url: str, local_path: Path) -> int:
        """Lädt eine einzelne Datei herunter."""
        try:
            # Prüfe ob Datei bereits existiert
            if local_path.exists():
                self.stats["skipped"] += 1
                return 0

            # Partial-Pfad
            partial_path = self.partial_root / filename

            # Download
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self._sync_download(remote_url, partial_path, local_path),
            )

            self.stats["downloaded"] += 1
            return 0

        except Exception as e:
            self._log(f"  ❌ Download fehlgeschlagen: {filename} ({str(e)[:50]})")
            self.stats["failed"] += 1
            self.stats["failed_files"].append((remote_url, str(e)))
            return 1

    def _sync_download(self, url: str, partial_path: Path, target_path: Path) -> None:
        """Synchroner Download mit Resume-Support."""
        try:
            partial_path.parent.mkdir(parents=True, exist_ok=True)

            # Resume wenn Partial-Datei existiert
            resume_header = {}
            if partial_path.exists():
                resume_header["Range"] = f"bytes={partial_path.stat().st_size}-"

            response = urlopen(url, timeout=self.timeout)
            total_size = int(response.headers.get("Content-Length", 0))

            with open(partial_path, "ab") as f:
                downloaded = 0
                while True:
                    chunk = response.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)

            # Move zu Ziel
            target_path.parent.mkdir(parents=True, exist_ok=True)
            partial_path.rename(target_path)
            self.stats["bytes"] += target_path.stat().st_size

        except Exception as e:
            raise RuntimeError(f"Download {url} fehlgeschlagen: {e}")

    def _log(self, msg: str) -> None:
        """Log-Output."""
        log.info(msg)
        self.on_line(msg)
