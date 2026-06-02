"""astrapi_mirror.modules.debian._sync_engine.downloader – Async File Downloader mit Resume."""

import asyncio
import gzip
import hashlib
import logging
import lzma
import re
import time
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

# Regex für Architektur- und Component-Filter (analog engine.py)
_ARCH_IN_PATH = re.compile(r"(?:^|/)binary-([^/]+)/")
_ARCH_IN_NAME = re.compile(r"(?:^|/)Contents-([a-zA-Z0-9_]+)")
_DEP11_ARCH = re.compile(r"/dep11/Components-([^./]+)\.")
_COMPONENT_PREFIX = re.compile(r"^([^/]+)/")
_TRANSLATION_IN_PATH = re.compile(r"(?:^|/)i18n/Translation-([^./]+)")
_OPTIONAL_INDEX_SUFFIXES = ("/Packages", "/Sources")


def _index_group_key(filename: str) -> str | None:
    """Gruppiert alternative Index-Varianten auf denselben logischen Eintrag."""
    if filename.endswith(".diff/Index"):
        return None

    for suffix in (".xz", ".gz", ".bz2"):
        if filename.endswith(suffix):
            stem = filename[: -len(suffix)]
            break
    else:
        stem = filename

    if (
        stem.endswith(_OPTIONAL_INDEX_SUFFIXES)
        or "/i18n/Translation-" in stem
        or "/Contents-" in stem
        or "/dep11/" in stem
    ):
        return f"idx:{stem}"

    return filename


def _variant_rank(filename: str) -> int:
    """Bevorzugt komprimierte Index-Dateien gegenüber Plain-Text."""
    if filename.endswith(".xz"):
        return 0
    if filename.endswith(".gz"):
        return 1
    if filename.endswith(".bz2"):
        return 2
    return 3


def _select_preferred_index_entries(entries: list[dict]) -> list[dict]:
    """Behält pro logischem Index nur die beste vorhandene Variante."""
    selected: dict[str, dict] = {}
    passthrough: list[dict] = []

    for entry in entries:
        filename = entry["filename"]
        key = _index_group_key(filename)
        if key is None:
            continue
        if key == filename:
            passthrough.append(entry)
            continue
        current = selected.get(key)
        if current is None or _variant_rank(filename) < _variant_rank(current["filename"]):
            selected[key] = entry

    return passthrough + list(selected.values())


def _as_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _configured_languages() -> set[str] | None:
    from astrapi_core.ui.settings_registry import get_module

    raw = str(get_module("debian", "language", default="") or "").strip()
    if not raw:
        return None

    langs: set[str] = set()
    for token in raw.split(","):
        clean = token.strip().lower().replace("-", "_")
        if not clean:
            continue
        langs.add(clean)
        langs.add(clean.split("_", 1)[0])
    return langs or None


def _should_include_contents() -> bool:
    from astrapi_core.ui.settings_registry import get_module

    raw = get_module("debian", "contents", default="true")
    return _as_bool(raw, default=True)


def _should_skip_file(
    filename: str,
    arch_set: set[str] | None,
    component_set: set[str] | None,
    include_sources: bool,
    include_contents: bool,
    language_set: set[str] | None,
) -> bool:
    """True wenn diese Datei laut Repo-Konfiguration nicht benötigt wird."""
    if component_set is not None:
        m = _COMPONENT_PREFIX.match(filename)
        if m and m.group(1) not in component_set:
            return True
    if not include_sources and "/source/" in filename:
        return True
    if not include_contents and "/Contents-" in filename:
        return True
    if language_set is not None:
        m = _TRANSLATION_IN_PATH.search(filename)
        if m and m.group(1).lower() not in language_set:
            return True
    if arch_set is not None:
        m = _ARCH_IN_PATH.search(filename)
        if m and m.group(1) not in arch_set:
            return True
        m = _ARCH_IN_NAME.search(filename)
        if m and m.group(1) not in arch_set:
            return True
        m = _DEP11_ARCH.search(filename)
        if m and m.group(1) not in arch_set:
            return True
    return False


class FileDownloader:
    """Parallel File Downloader mit Resume und Checksummen-Validierung."""

    def __init__(
        self,
        staging_path: Path,
        partial_root: Path,
        timeout: int = 12 * 3600,
        on_line: Callable[[str], None] | None = None,
        max_concurrent: int = 4,
    ):
        """
        Args:
            staging_path: Zielverzeichnis für Downloads
            partial_root: Verzeichnis für Partial-Dateien
            timeout: Globales Timeout in Sekunden
            on_line: Callback pro Zeile Output
            max_concurrent: Max. parallele Downloads
        """
        self.staging_path = staging_path
        self.partial_root = partial_root
        self.timeout = timeout
        self.on_line = on_line
        self.max_concurrent = max_concurrent
        self.deadline = time.time() + timeout
        self.stats = {"downloaded": 0, "skipped": 0, "failed": 0, "bytes": 0, "failed_files": []}

    def _log(self, msg: str) -> None:
        if self.on_line:
            self.on_line(msg)

    async def download_repo(self, repo: dict) -> int:
        """Lädt alle Dateien eines Repos herunter (InRelease → dists/ → pool/).

        Ablauf pro Suite:
        1. InRelease herunterladen
        2. InRelease parsen → Dateiliste
        3. Index-Dateien (Packages, Release, Contents, …) gefiltert herunterladen
        4. Packages parsen → Pool-Dateipfade extrahieren
        5. Pool-Dateien (.deb) herunterladen
        """
        url = (repo.get("url") or "").rstrip("/")
        if not url:
            self._log("❌ Keine URL definiert")
            return 1

        # Flat-Repos haben keine dists/-Struktur
        if repo.get("is_flat"):
            return await self._download_flat_repo(repo)

        suites = [s.strip() for s in (repo.get("suites") or []) if s.strip()]
        architectures = [a.strip() for a in (repo.get("architectures") or []) if a.strip()]
        components = [c.strip() for c in (repo.get("components") or []) if c.strip()]
        include_sources = repo.get("repo_type", "deb") == "deb-src"
        include_contents = _should_include_contents()
        language_set = _configured_languages()

        arch_set = set(architectures) if architectures else None
        comp_set = set(components) if components else None

        if not suites:
            self._log("ℹ️ Keine Suites definiert (Flat-Repo?)")
            return 0

        # Sammelt alle Pool-Dateien aus allen Suites (dedupliziert am Ende)
        pool_files: list[tuple[str, Path, str | None]] = []

        # ---------------------------------------------------------------
        # Phase A: InRelease + dists/-Dateien pro Suite
        # ---------------------------------------------------------------
        for suite in suites:
            suite_url = f"{url}/dists/{suite}"
            suite_path = self.staging_path / "dists" / suite
            suite_path.mkdir(parents=True, exist_ok=True)

            self._log(f"\n📦 Suite: {suite}")

            # 1. InRelease herunterladen (immer aktuell holen, auch beim Resume)
            inrelease_path = suite_path / "InRelease"
            rc, _ = await self._download_file(f"{suite_url}/InRelease", inrelease_path, force=True)
            if rc != 0:
                self._log(f"❌ InRelease nicht abrufbar: {suite}")
                return 1

            # 2. Dateiliste aus InRelease parsen
            try:
                index_entries = self._parse_inrelease(inrelease_path.read_text(errors="replace"))
            except Exception as e:
                self._log(f"❌ InRelease-Parse-Fehler: {e}")
                return 1

            # 3. Filtern nach Architektur/Komponente
            filtered = [
                e
                for e in index_entries
                if not _should_skip_file(
                    e["filename"],
                    arch_set,
                    comp_set,
                    include_sources,
                    include_contents,
                    language_set,
                )
            ]
            filtered = _select_preferred_index_entries(filtered)
            self._log(f"  {len(filtered)}/{len(index_entries)} Dateien nach Filter")

            # 4. Index-Dateien parallel herunterladen
            sem = asyncio.Semaphore(self.max_concurrent)
            tasks = [
                asyncio.create_task(
                    self._bounded_download(
                        sem,
                        f"{suite_url}/{e['filename']}",
                        suite_path / e["filename"],
                        e.get("sha256"),
                    )
                )
                for e in filtered
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

            # 5. Pool-Pfade aus Packages-Dateien extrahieren
            for entry in filtered:
                fname = entry["filename"]
                if re.search(r"/Packages(\.gz|\.xz)?$", fname):
                    pkg_path = suite_path / fname
                    try:
                        for p in self._extract_pool_files(pkg_path):
                            pool_files.append(
                                (
                                    f"{url}/{p['filename']}",
                                    self.staging_path / p["filename"],
                                    p.get("sha256"),
                                )
                            )
                    except Exception as e:
                        self._log(f"⚠️ Pool-Extraktion fehlgeschlagen ({fname}): {e}")

        # ---------------------------------------------------------------
        # Phase B: Pool-Dateien herunterladen (dedupliziert)
        # ---------------------------------------------------------------
        seen: set[str] = set()
        unique_pool = []
        for pu, pt, pcs in pool_files:
            key = str(pt)
            if key not in seen:
                seen.add(key)
                unique_pool.append((pu, pt, pcs))

        if unique_pool:
            self._log(f"\n📦 Pool: {len(unique_pool)} Pakete herunterladen...")
            sem = asyncio.Semaphore(self.max_concurrent)
            tasks = [
                asyncio.create_task(self._bounded_download(sem, pu, pt, pcs))
                for pu, pt, pcs in unique_pool
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

        self._log(
            f"\n📊 Download-Statistik: {self.stats['downloaded']} heruntergeladen, "
            f"{self.stats['skipped']} übersprungen, "
            f"{self.stats['failed']} Fehler, "
            f"{self._fmt_size(self.stats['bytes'])} gesamt"
        )
        if self.stats["failed_files"]:
            self._log("\n⚠️ Fehlgeschlagene Dateien:")
            for _url, _err in self.stats["failed_files"]:
                self._log(f"  ❌ {_url}  ({_err})")

        return 0 if self.stats["failed"] == 0 else 1

    async def _download_flat_repo(self, repo: dict) -> int:
        """Lädt ein Flat-Repo herunter (kein dists/-Unterverzeichnis).

        Bei Flat-Repos liegen InRelease, Packages und .deb-Dateien direkt
        an der Basis-URL (z.B. OpenSUSE Build Service, LLDAP, ...).

        Struktur:
            {url}/InRelease
            {url}/Packages(.gz)
            {url}/{arch}/*.deb
        """
        url = (repo.get("url") or "").rstrip("/")
        architectures = [a.strip() for a in (repo.get("architectures") or []) if a.strip()]
        arch_set = set(architectures) if architectures else None

        self.staging_path.mkdir(parents=True, exist_ok=True)

        # 1. InRelease herunterladen (immer aktuell holen, auch beim Resume)
        inrelease_path = self.staging_path / "InRelease"
        rc, _ = await self._download_file(f"{url}/InRelease", inrelease_path, force=True)
        if rc != 0:
            release_path = self.staging_path / "Release"
            rc, _ = await self._download_file(f"{url}/Release", release_path, force=True)
            if rc != 0:
                self._log("❌ Weder InRelease noch Release abrufbar")
                return 1
            inrelease_path = release_path

        # 2. Dateiliste parsen
        try:
            entries = self._parse_inrelease(inrelease_path.read_text(errors="replace"))
        except Exception as e:
            self._log(f"❌ InRelease-Parse-Fehler: {e}")
            return 1

        # 3. Arch-Filter für Flat-Repos: erstes Pfadsegment = Architektur
        _known_arches = {
            "amd64",
            "arm64",
            "i386",
            "armhf",
            "armel",
            "ppc64el",
            "s390x",
            "riscv64",
            "armv7l",
        }

        def _flat_skip(filename: str) -> bool:
            if arch_set is None:
                return False
            first = filename.split("/")[0] if "/" in filename else ""
            if first in _known_arches:
                return first not in arch_set
            return False

        filtered = [e for e in entries if not _flat_skip(e["filename"])]
        filtered = _select_preferred_index_entries(filtered)
        self._log(f"  {len(filtered)}/{len(entries)} Dateien nach Filter")

        # 4. Index-Dateien parallel herunterladen
        sem = asyncio.Semaphore(self.max_concurrent)
        tasks = [
            asyncio.create_task(
                self._bounded_download(
                    sem,
                    f"{url}/{e['filename']}",
                    self.staging_path / e["filename"],
                    e.get("sha256"),
                )
            )
            for e in filtered
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        # 5. Pool-Pfade aus Packages extrahieren und herunterladen
        pool_files: list[tuple[str, Path, str | None]] = []
        for entry in filtered:
            if re.search(r"Packages(\.gz|\.xz)?$", entry["filename"]):
                pkg_path = self.staging_path / entry["filename"]
                try:
                    for p in self._extract_pool_files(pkg_path):
                        if arch_set:
                            # Filename beginnt mit arch/ (z.B. amd64/lldap.deb)
                            first = p["filename"].split("/")[0]
                            if first in _known_arches and first not in arch_set:
                                continue
                        pool_files.append(
                            (
                                f"{url}/{p['filename']}",
                                self.staging_path / p["filename"],
                                p.get("sha256"),
                            )
                        )
                except Exception as e:
                    self._log(f"⚠️ Packages-Parse-Fehler: {e}")

        seen: set[str] = set()
        unique_pool = []
        for pu, pt, pcs in pool_files:
            key = str(pt)
            if key not in seen:
                seen.add(key)
                unique_pool.append((pu, pt, pcs))

        if unique_pool:
            self._log(f"\n📦 Pakete: {len(unique_pool)} herunterladen...")
            tasks = [
                asyncio.create_task(self._bounded_download(sem, pu, pt, pcs))
                for pu, pt, pcs in unique_pool
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

        self._log(
            f"\n📊 Download-Statistik: {self.stats['downloaded']} heruntergeladen, "
            f"{self.stats['skipped']} übersprungen, "
            f"{self.stats['failed']} Fehler, "
            f"{self._fmt_size(self.stats['bytes'])} gesamt"
        )
        if self.stats["failed_files"]:
            self._log("\n⚠️ Fehlgeschlagene Dateien:")
            for _url, _err in self.stats["failed_files"]:
                self._log(f"  ❌ {_url}  ({_err})")
        return 0 if self.stats["failed"] == 0 else 1

    async def _bounded_download(
        self,
        sem: asyncio.Semaphore,
        url: str,
        path: Path,
        checksum: str | None = None,
        force: bool = False,
    ) -> None:
        """Download mit Semaphore-Begrenzung."""
        async with sem:
            await self._download_file(url, path, checksum=checksum, force=force)

    @staticmethod
    def _parse_inrelease(content: str) -> list[dict]:
        """Parst den SHA256-Block einer InRelease-Datei.

        Returns:
            Liste von {sha256, size, filename} für alle referenzierten Dateien
        """
        entries: list[dict] = []
        in_block = False
        for line in content.splitlines():
            if line.startswith("SHA256:"):
                in_block = True
                continue
            if in_block:
                if not line.startswith(" "):
                    in_block = False
                    continue
                parts = line.strip().split()
                if len(parts) >= 3:
                    try:
                        entries.append(
                            {
                                "sha256": parts[0],
                                "size": int(parts[1]),
                                "filename": parts[2],
                            }
                        )
                    except (ValueError, IndexError):
                        pass
        return entries

    @staticmethod
    def _extract_pool_files(packages_path: Path) -> list[dict]:
        """Parst eine Packages-Datei (plain/gz/xz) und gibt Pool-Pfade zurück.

        Returns:
            Liste von {filename, sha256, size} für alle Pakete
        """
        name = packages_path.name.lower()
        if name.endswith(".gz"):
            opener = lambda: gzip.open(packages_path, "rt", errors="replace")
        elif name.endswith(".xz"):
            opener = lambda: lzma.open(packages_path, "rt", errors="replace")
        else:
            opener = lambda: open(packages_path, "r", errors="replace")

        entries: list[dict] = []
        current: dict = {}

        with opener() as f:
            for line in f:
                line = line.rstrip("\n")
                if line.startswith("Filename:"):
                    current["filename"] = line.split(":", 1)[1].strip()
                elif line.startswith("SHA256:") and " " not in line.split(":", 1)[1].strip():
                    # Einzelne SHA256-Zeile im Packages-Format (kein Block)
                    current["sha256"] = line.split(":", 1)[1].strip()
                elif line.startswith("Size:"):
                    try:
                        current["size"] = int(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
                elif line == "" and "filename" in current:
                    entries.append(current)
                    current = {}

        if "filename" in current:
            entries.append(current)

        return entries

    async def _download_file(
        self, url: str, target_path: Path, checksum: str | None = None, force: bool = False
    ) -> tuple[int, str]:
        """Lädt eine einzelne Datei herunter mit Resume-Unterstützung.

        Args:
            url: URL der Datei
            target_path: Ziel-Pfad
            checksum: Optional SHA256-Checksumme
            force: Immer herunterladen, auch wenn Datei bereits existiert

        Returns:
            (returncode, message)
        """
        # Prüfe Timeout
        if time.time() > self.deadline:
            msg = "Timeout überschritten"
            self._log(f"⏱️ {target_path.name}: {msg}")
            return 1, msg

        # Prüfe ob Datei schon vollständig existiert (außer bei force=True)
        if not force and target_path.exists():
            try:
                size = target_path.stat().st_size
                if checksum:
                    file_hash = self._compute_sha256(target_path)
                    if file_hash != checksum:
                        self._log(
                            f"♻️ {target_path.name}: lokale Datei veraltet, lade neu "
                            f"({self._fmt_size(size)})"
                        )
                        try:
                            target_path.unlink()
                        except OSError:
                            pass
                    else:
                        self._log(
                            f"⏭️ {target_path.name}: bereits vorhanden ({self._fmt_size(size)})"
                        )
                        self.stats["skipped"] += 1
                        return 0, "Already exists"
                else:
                    self._log(f"⏭️ {target_path.name}: bereits vorhanden ({self._fmt_size(size)})")
                    self.stats["skipped"] += 1
                    return 0, "Already exists"
            except Exception:
                pass

        # Erstelle Partial-Datei
        partial_path = self.partial_root / f"{target_path.relative_to(self.staging_path)}"
        partial_path.parent.mkdir(parents=True, exist_ok=True)

        if checksum and partial_path.exists():
            try:
                partial_hash = self._compute_sha256(partial_path)
            except OSError:
                partial_hash = None
            if partial_hash and partial_hash == checksum:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                partial_path.replace(target_path)
                size = target_path.stat().st_size
                self._log(f"✅ {target_path.name}: {self._fmt_size(size)}")
                self.stats["downloaded"] += 1
                return 0, "OK"
            try:
                partial_path.unlink()
            except OSError:
                pass

        try:
            # Download starten
            start_size = partial_path.stat().st_size if partial_path.exists() else 0

            req = Request(url, headers={"User-Agent": "astrapi-mirror/1.0"})
            if start_size > 0:
                req.add_header("Range", f"bytes={start_size}-")

            with urlopen(req, timeout=300) as resp:
                # Schreibe zu Partial-Datei
                partial_path.parent.mkdir(parents=True, exist_ok=True)
                mode = "ab" if start_size > 0 else "wb"

                with open(partial_path, mode) as f:
                    while True:
                        if time.time() > self.deadline:
                            return 1, "Timeout"

                        chunk = resp.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)
                        self.stats["bytes"] += len(chunk)

            # Validiere Checksumme (falls vorhanden)
            if checksum:
                file_hash = self._compute_sha256(partial_path)
                if file_hash != checksum:
                    msg = f"Checksumme stimmt nicht: {checksum} vs {file_hash}"
                    self._log(f"❌ {target_path.name}: {msg}")
                    self.stats["failed"] += 1
                    self.stats["failed_files"].append((url, msg))
                    return 1, msg

            # Verschiebe zu Final-Pfad
            target_path.parent.mkdir(parents=True, exist_ok=True)
            partial_path.replace(target_path)

            size = target_path.stat().st_size
            self._log(f"✅ {target_path.name}: {self._fmt_size(size)}")
            self.stats["downloaded"] += 1
            return 0, "OK"

        except Exception as e:
            self._log(f"❌ {target_path.name}: {e}")
            self.stats["failed"] += 1
            self.stats["failed_files"].append((url, str(e)))
            return 1, str(e)

    @staticmethod
    def _compute_sha256(file_path: Path) -> str:
        """Berechnet SHA256-Hash einer Datei."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    @staticmethod
    def _fmt_size(size: int) -> str:
        """Formatiert Dateigröße lesbar."""
        for unit, div in [("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)]:
            if size >= div:
                return f"{size / div:.1f} {unit}"
        return f"{size} B"
