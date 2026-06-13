"""astrapi_mirror.modules.archlinux._sync_engine.downloader – Asyncio-basierter Downloader für Arch."""

import asyncio
import logging
from collections import defaultdict
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

_CHUNK_SIZE = 65536  # 64 KB

# Bekannte Arch-Linux-Architekturen zur Erkennung in URLs
_KNOWN_ARCHS = ("x86_64", "aarch64", "armv7h", "armv6h", "i686", "pentium4", "any")


def _detect_arch(url: str) -> str:
    """Erkennt die Architektur anhand eines bekannten Arch-Strings in der URL."""
    for arch in _KNOWN_ARCHS:
        if f"/{arch}" in url or f"/{arch}/" in url:
            return arch
    return "x86_64"


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

    @staticmethod
    def _get_mirror_list(repo: dict) -> list[str]:
        """Gibt die geordnete Liste aller Mirror-URLs zurück.

        Neue Repos nutzen `mirror_urls` (Liste vollständiger URLs).
        Alte Repos (url + architectures) werden automatisch konvertiert.
        """
        urls: list[str] = []

        # Neue Form: mirror_urls als Liste
        mirror_urls = repo.get("mirror_urls") or []
        if isinstance(mirror_urls, str):
            mirror_urls = [line.strip() for line in mirror_urls.splitlines() if line.strip()]
        for u in mirror_urls:
            u = u.rstrip("/")
            if u and u not in urls:
                urls.append(u)

        # Backward-compat: alte url + architectures → vollständige URLs konstruieren
        if not urls:
            base = (repo.get("url") or "").rstrip("/")
            if base:
                archs = repo.get("architectures") or ["x86_64"]
                if isinstance(archs, str):
                    archs = [a.strip() for a in archs.split(",") if a.strip()]
                for arch in archs:
                    full = f"{base}/os/{arch}"
                    if full not in urls:
                        urls.append(full)

        return urls

    async def download_repo(self, repo: dict) -> int:
        """Lädt ein komplettes Arch-Repository herunter.

        Jede URL in mirror_urls zeigt direkt auf ein Architektur-Verzeichnis,
        z.B. https://mirror.example.org/archlinux/extra/os/x86_64.
        Mehrere URLs für dieselbe Architektur dienen als Fallbacks.

        Returns:
            0 = OK, >0 = Fehler
        """
        all_urls = self._get_mirror_list(repo)
        if not all_urls:
            self._log("❌ Keine Mirror-URL konfiguriert")
            return 1

        # URLs nach erkannter Architektur gruppieren
        arch_groups: dict[str, list[str]] = defaultdict(list)
        for url in all_urls:
            arch = _detect_arch(url)
            arch_groups[arch].append(url)

        self._log(
            f"Lade {len(arch_groups)} Architektur(en) "
            f"({sum(len(v) for v in arch_groups.values())} URL(s) konfiguriert)..."
        )

        tasks = [
            asyncio.create_task(self._download_arch_group(arch, group_urls))
            for arch, group_urls in arch_groups.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        failed_count = sum(1 for r in results if isinstance(r, Exception) or r != 0)
        if failed_count > 0:
            self._log(f"\n⚠️ {failed_count}/{len(arch_groups)} Architektur(en) fehlgeschlagen")
            return 1

        return 0

    async def _download_arch_group(self, arch: str, urls: list[str]) -> int:
        """Lädt alle Dateien einer Architektur herunter; probiert URLs als Fallbacks."""
        arch_path = self.staging_path / "os" / arch
        arch_path.mkdir(parents=True, exist_ok=True)

        self._log(f"\n📦 Lade Architektur: {arch} ({len(urls)} Mirror(s))")

        # Alle Mirror-URLs normalisieren
        ordered_urls = [u.rstrip("/") + "/" for u in urls]

        # Ersten erreichbaren Mirror mit Dateiliste als primären Mirror wählen
        active_url: str | None = None
        file_list: list[str] = []
        for candidate_url in ordered_urls:
            try:
                candidate_list = await self._get_file_list(candidate_url)
                if candidate_list:
                    active_url = candidate_url
                    file_list = candidate_list
                    self._log(f"  Primärer Mirror: {active_url} ({len(file_list)} Dateien)")
                    break
                self._log(f"  ⚠️ {candidate_url}: keine Dateien, versuche nächsten...")
            except Exception as e:
                self._log(f"  ⚠️ {candidate_url} nicht erreichbar: {e}, versuche nächsten...")

        if active_url is None:
            self._log(f"❌ Alle Mirrors für {arch} nicht erreichbar oder leer")
            return 1

        # Primärer Mirror zuerst, dann Fallbacks in ursprünglicher Reihenfolge
        mirror_priority = [active_url] + [u for u in ordered_urls if u != active_url]

        # Parallele Downloads mit Mirror-Fallback pro Datei
        sem = asyncio.Semaphore(self.max_concurrent)

        async def download_with_semaphore(filename: str):
            async with sem:
                return await self._download_file_with_fallback(filename, mirror_priority, arch_path)

        tasks = [download_with_semaphore(fname) for fname in file_list]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        failed = sum(1 for r in results if isinstance(r, Exception) or r != 0)
        total = len(file_list)
        self._log(
            f"  Ergebnis: {total - failed}/{total} Dateien geladen"
            + (f", {failed} fehlgeschlagen" if failed else "")
        )
        if failed > 0:
            return 1

        # Vollständigkeitscheck: alle erwarteten Dateien vorhanden?
        missing = self._check_completeness(file_list, arch_path)
        if missing:
            self._log(f"❌ {len(missing)} Datei(en) fehlen nach Download: {missing[:5]}")
            return 1

        return 0

    async def _download_file_with_fallback(
        self, filename: str, mirror_urls: list[str], arch_path: Path
    ) -> int:
        """Lädt eine Datei herunter und wechselt bei Fehler auf den nächsten Mirror."""
        local_path = arch_path / filename
        if local_path.exists():
            self.stats["skipped"] += 1
            return 0

        partial_path = self.partial_root / filename
        loop = asyncio.get_event_loop()

        for i, base_url in enumerate(mirror_urls):
            # Beim Fallback-Mirror Partial verwerfen (sauberer Neustart)
            if i > 0:
                partial_path.unlink(missing_ok=True)

            remote_url = f"{base_url}{filename}"
            try:
                await loop.run_in_executor(
                    None,
                    lambda u=remote_url: self._sync_download(u, partial_path, local_path),
                )
                self.stats["downloaded"] += 1
                return 0
            except Exception as e:
                if i < len(mirror_urls) - 1:
                    self._log(
                        f"  ⚠️ {filename}: Mirror {i + 1} fehlgeschlagen, versuche Mirror {i + 2}..."
                    )
                else:
                    self._log(f"  ❌ Alle Mirrors fehlgeschlagen: {filename} ({str(e)[:50]})")
                    self.stats["failed"] += 1
                    self.stats["failed_files"].append((remote_url, str(e)))

        return 1

    @staticmethod
    def _check_completeness(file_list: list[str], arch_path: Path) -> list[str]:
        """Gibt fehlende Dateien zurück. .sig-Dateien werden toleriert."""
        return [
            fname for fname in file_list
            if not fname.endswith(".sig") and not (arch_path / fname).exists()
        ]

    async def _get_file_list(self, arch_url: str) -> list[str]:
        """Ruft Dateiliste vom Server ab (HTTP-Directory-Listing parsen)."""
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

        pattern = re.compile(r'href="([^"#][^"]*)"', re.IGNORECASE)
        files = []
        seen = set()
        for m in pattern.finditer(html):
            href = unquote(m.group(1).strip())
            name = href.split("/")[-1] or href
            if name and name not in seen and any(name.endswith(ext) for ext in _ARCH_EXTS):
                files.append(name)
                seen.add(name)

        return files

    def _sync_download(self, url: str, partial_path: Path, target_path: Path) -> None:
        """Synchroner Download mit Resume-Support."""
        try:
            partial_path.parent.mkdir(parents=True, exist_ok=True)

            headers = {}
            if partial_path.exists():
                headers["Range"] = f"bytes={partial_path.stat().st_size}-"

            req = Request(url, headers=headers)
            response = urlopen(req, timeout=self.timeout)

            mode = "ab" if headers.get("Range") else "wb"
            with open(partial_path, mode) as f:
                while True:
                    chunk = response.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)

            target_path.parent.mkdir(parents=True, exist_ok=True)
            partial_path.rename(target_path)
            self.stats["bytes"] += target_path.stat().st_size

        except Exception as e:
            raise RuntimeError(f"Download {url} fehlgeschlagen: {e}")

    def _log(self, msg: str) -> None:
        log.info(msg)
        self.on_line(msg)
