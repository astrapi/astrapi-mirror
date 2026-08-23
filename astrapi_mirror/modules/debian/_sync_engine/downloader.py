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


def _build_mirror_list(mirror_urls) -> list[str]:
    """Baut geordnete Mirror-Liste aus mirror_urls (T-186-MIRROR).

    Erster Eintrag = primärer Mirror, weitere = Fallbacks. Kein separates
    "Primäre URL"-Feld mehr -- angeglichen an archlinux, siehe
    modules/archlinux/_sync_engine/downloader.py::_get_mirror_list().
    """
    extra = mirror_urls or []
    if isinstance(extra, str):
        extra = [e.strip() for e in extra.splitlines() if e.strip()]
    return [m.rstrip("/") for m in extra if m.strip()]


def _is_http_404(error_msg: str) -> bool:
    """True wenn der Fehler ein HTTP 404 ist (Datei existiert nicht auf dem Mirror)."""
    return "HTTP Error 404" in error_msg or ": 404 " in error_msg


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


def _parse_limit_rate(raw: str) -> int | None:
    """Parst wget-Style Bandbreitenlimit: '200m' → 209715200 Bytes/s.

    Unterstützt k (KiB), m (MiB), g (GiB) als Suffix.
    """
    if not raw:
        return None
    raw = raw.strip().lower()
    for suffix, mult in (("g", 1 << 30), ("m", 1 << 20), ("k", 1 << 10)):
        if raw.endswith(suffix):
            try:
                return max(1, int(float(raw[:-1]) * mult))
            except ValueError:
                return None
    try:
        v = int(raw)
        return v if v > 0 else None
    except ValueError:
        return None


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


def _char_order(c: str) -> int:
    """dpkg-Zeichenordnung: ~ sortiert vor allem (auch vor Stringende),
    Stringende vor Buchstaben, Buchstaben vor allem anderen."""
    if c == "~":
        return -1
    if not c:
        return 0
    if c.isalpha():
        return ord(c)
    return ord(c) + 256


def _compare_non_digit(a: str, b: str) -> int:
    i = j = 0
    while i < len(a) or j < len(b):
        oa = _char_order(a[i] if i < len(a) else "")
        ob = _char_order(b[j] if j < len(b) else "")
        if oa != ob:
            return -1 if oa < ob else 1
        i += 1
        j += 1
    return 0


def _verrevcmp(a: str, b: str) -> int:
    """Vergleicht zwei Versions-Teilstrings (upstream oder Debian-Revision)
    per abwechselnden Nicht-Ziffern-/Ziffern-Laeufen, wie dpkg es tut."""
    i = j = 0
    while i < len(a) or j < len(b):
        si, sj = i, j
        while i < len(a) and not a[i].isdigit():
            i += 1
        while j < len(b) and not b[j].isdigit():
            j += 1
        c = _compare_non_digit(a[si:i], b[sj:j])
        if c != 0:
            return c
        si, sj = i, j
        while i < len(a) and a[i].isdigit():
            i += 1
        while j < len(b) and b[j].isdigit():
            j += 1
        na = int(a[si:i]) if i > si else 0
        nb = int(b[sj:j]) if j > sj else 0
        if na != nb:
            return -1 if na < nb else 1
    return 0


def compare_debian_versions(v1: str, v2: str) -> int:
    """Vergleicht zwei Debian-Versionsstrings nach Debian Policy 5.6.12
    ([epoch:]upstream_version[-debian_revision]). Gibt -1/0/1 zurueck.

    Naive String-Sortierung waere hier falsch (z.B. "2.0" > "10.0" als Text,
    aber "10.0" ist die neuere Version) -- deshalb eigene Implementierung
    statt eines externen Pakets (T-183-MIRROR).
    """

    def split_epoch(v: str) -> tuple[int, str]:
        if ":" in v:
            e, rest = v.split(":", 1)
            try:
                return int(e), rest
            except ValueError:
                return 0, v
        return 0, v

    def split_revision(v: str) -> tuple[str, str]:
        if "-" in v:
            up, rev = v.rsplit("-", 1)
            return up, rev
        return v, ""

    e1, r1 = split_epoch(v1)
    e2, r2 = split_epoch(v2)
    if e1 != e2:
        return -1 if e1 < e2 else 1

    up1, rev1 = split_revision(r1)
    up2, rev2 = split_revision(r2)
    c = _verrevcmp(up1, up2)
    if c != 0:
        return c
    return _verrevcmp(rev1, rev2)


def _keep_latest_versions(entries: list[dict], keep: int) -> list[dict]:
    """Behaelt pro Paketname nur die `keep` neuesten Versionen (0/leer = alle).

    Gruppiert nach `Package:`-Feld (Fallback: Dateiname, falls das Feld beim
    Parsen fehlt). Pakete ohne erkannte Version (leeres `Version:`-Feld)
    werden nie herausgefiltert, um im Zweifel nichts zu verlieren.
    """
    if not keep:
        return entries
    from collections import defaultdict
    from functools import cmp_to_key

    groups: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        groups[e.get("package") or e["filename"]].append(e)

    result: list[dict] = []
    for group in groups.values():
        with_version = [e for e in group if e.get("version")]
        without_version = [e for e in group if not e.get("version")]
        with_version.sort(
            key=cmp_to_key(lambda a, b: compare_debian_versions(a["version"], b["version"])),
            reverse=True,
        )
        result.extend(with_version[:keep])
        result.extend(without_version)
    return result


def _matches_package_include(pool_filename: str, patterns: list[str]) -> bool:
    """Include-Filter fuer Pool-Dateien (T-182-MIRROR).

    Leere `patterns` = alles spiegeln (Standardverhalten unveraendert).
    Sonst muss der Dateiname (letztes Pfadsegment, z.B.
    "grafana-enterprise_11.5.1_amd64.deb") auf mindestens ein fnmatch-Muster
    passen -- analog zu den bestehenden exclude_patterns im archlinux-Modul,
    nur repo-spezifisch statt modulweit und als Include statt Exclude.
    """
    if not patterns:
        return True
    import fnmatch

    name = pool_filename.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(name, pat) for pat in patterns)


class FileDownloader:
    """Parallel File Downloader mit Resume und Checksummen-Validierung."""

    def __init__(
        self,
        staging_path: Path,
        partial_root: Path,
        timeout: int = 12 * 3600,
        on_line: Callable[[str], None] | None = None,
        max_concurrent: int = 4,
        limit_rate: int | None = None,
        file_timeout: int | None = 300,
    ):
        """
        Args:
            staging_path: Zielverzeichnis für Downloads
            partial_root: Verzeichnis für Partial-Dateien
            timeout: Globales Timeout in Sekunden
            on_line: Callback pro Zeile Output
            max_concurrent: Max. parallele Downloads
            limit_rate: Bandbreitenlimit in Bytes/s pro Download-Task (None = kein Limit)
            file_timeout: Timeout pro Einzeldatei in Sekunden (None = kein Limit)
        """
        self.staging_path = staging_path
        self.partial_root = partial_root
        self.timeout = timeout
        self.on_line = on_line
        self.max_concurrent = max_concurrent
        self.limit_rate = limit_rate
        self.file_timeout = file_timeout
        self.deadline = time.time() + timeout
        self.stats = {
            "downloaded": 0,
            "skipped": 0,
            "failed": 0,
            "pruned": 0,
            "bytes": 0,
            "failed_files": [],
        }

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
        self._mirrors = _build_mirror_list(repo.get("mirror_urls"))
        if not self._mirrors:
            self._log("❌ Keine URL definiert")
            return 1
        url = self._primary_url = self._mirrors[0]
        if len(self._mirrors) > 1:
            self._log(f"ℹ️ {len(self._mirrors)} Mirror(s) konfiguriert")

        suites = [s.strip() for s in (repo.get("suites") or []) if s.strip()]
        architectures = [a.strip() for a in (repo.get("architectures") or []) if a.strip()]
        components = [c.strip() for c in (repo.get("components") or []) if c.strip()]
        package_include = [p.strip() for p in (repo.get("package_include") or []) if p.strip()]
        try:
            keep_versions = int(repo.get("keep_versions") or 0)
        except (TypeError, ValueError):
            keep_versions = 0
        include_sources = repo.get("repo_type", "deb") == "deb-src"
        include_contents = _should_include_contents()
        language_set = _configured_languages()

        arch_set = set(architectures) if architectures else None
        comp_set = set(components) if components else None

        if not suites:
            self._log("ℹ️ Keine Suites definiert – behandle als Flat-Repo")
            return await self._download_flat_repo(repo)

        # Flat-Repo-Erkennung: prüfe ob dists/{suite}/InRelease erreichbar ist
        is_flat = await asyncio.to_thread(
            self._check_is_flat, url, suites[0], self.deadline
        )
        if is_flat:
            self._log(f"ℹ️ dists/{suites[0]}/InRelease nicht vorhanden – Flat-Repo erkannt")
            return await self._download_flat_repo(repo)

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
            # Contents-* Dateien sind optional – nicht alle Repos stellen sie bereit
            sem = asyncio.Semaphore(self.max_concurrent)
            tasks = [
                asyncio.create_task(
                    self._bounded_download(
                        sem,
                        f"{suite_url}/{e['filename']}",
                        suite_path / e["filename"],
                        e.get("sha256"),
                        soft=e["filename"].split("/")[-1].startswith("Contents-"),
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
                        entries = _keep_latest_versions(
                            self._extract_pool_files(pkg_path), keep_versions
                        )
                        for p in entries:
                            if not _matches_package_include(p["filename"], package_include):
                                continue
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

        if self.stats["failed"] == 0:
            referenced = {pt for _pu, pt, _pcs in unique_pool}
            # Nicht auf staging_path/"pool" beschraenken: das Filename:-Feld im
            # Packages-Index bestimmt den tatsaechlichen Pfad und ist nicht
            # zwingend "pool/..." (z.B. repos.influxdata.com nutzt "packages/...").
            # scan_root=staging_path (wie im Flat-Repo-Pfad) deckt beide Faelle ab,
            # _prune_stale_pool_files() filtert ohnehin nur .deb/.udeb (T-184-MIRROR).
            self._prune_stale_pool_files(self.staging_path, referenced)

        self._log(
            f"\n📊 Download-Statistik: {self.stats['downloaded']} heruntergeladen, "
            f"{self.stats['skipped']} übersprungen, "
            f"{self.stats['failed']} Fehler, "
            f"{self.stats['pruned']} veraltet entfernt, "
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
        self._mirrors = _build_mirror_list(repo.get("mirror_urls"))
        if not self._mirrors:
            self._log("❌ Keine URL definiert")
            return 1
        url = self._primary_url = self._mirrors[0]
        architectures = [a.strip() for a in (repo.get("architectures") or []) if a.strip()]
        arch_set = set(architectures) if architectures else None
        package_include = [p.strip() for p in (repo.get("package_include") or []) if p.strip()]
        try:
            keep_versions = int(repo.get("keep_versions") or 0)
        except (TypeError, ValueError):
            keep_versions = 0

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

        # Release und Release.gpg sind redundant wenn InRelease vorhanden ist.
        # Inkonsistenzen (z.B. veraltete Release-Datei) sollen den Sync nicht abbrechen.
        _SOFT_NAMES = frozenset({"Release", "Release.gpg"})
        soft_entries = [e for e in filtered if e["filename"] in _SOFT_NAMES]
        filtered = [e for e in filtered if e["filename"] not in _SOFT_NAMES]
        self._log(f"  {len(filtered)}/{len(entries)} Dateien nach Filter")

        # 4. Index-Dateien parallel herunterladen
        # Contents-* Dateien sind optional – nicht alle Repos stellen sie bereit
        sem = asyncio.Semaphore(self.max_concurrent)
        tasks = [
            asyncio.create_task(
                self._bounded_download(
                    sem,
                    f"{url}/{e['filename']}",
                    self.staging_path / e["filename"],
                    e.get("sha256"),
                    soft="/Contents-" in e["filename"],
                )
            )
            for e in filtered
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        # Release / Release.gpg ohne Checksummen-Validierung (soft)
        for e in soft_entries:
            await self._bounded_download(
                sem,
                f"{url}/{e['filename']}",
                self.staging_path / e["filename"],
                checksum=None,
                soft=True,
            )

        # 5. Pool-Pfade aus Packages extrahieren und herunterladen
        pool_files: list[tuple[str, Path, str | None]] = []
        for entry in filtered:
            if re.search(r"Packages(\.gz|\.xz)?$", entry["filename"]):
                pkg_path = self.staging_path / entry["filename"]
                try:
                    entries = _keep_latest_versions(
                        self._extract_pool_files(pkg_path), keep_versions
                    )
                    for p in entries:
                        if arch_set:
                            # Filename beginnt mit arch/ (z.B. amd64/lldap.deb)
                            first = p["filename"].split("/")[0]
                            if first in _known_arches and first not in arch_set:
                                continue
                        if not _matches_package_include(p["filename"], package_include):
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

        if self.stats["failed"] == 0:
            referenced = {pt for _pu, pt, _pcs in unique_pool}
            self._prune_stale_pool_files(self.staging_path, referenced)

        self._log(
            f"\n📊 Download-Statistik: {self.stats['downloaded']} heruntergeladen, "
            f"{self.stats['skipped']} übersprungen, "
            f"{self.stats['failed']} Fehler, "
            f"{self.stats['pruned']} veraltet entfernt, "
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
        soft: bool = False,
    ) -> None:
        """Download mit Semaphore-Begrenzung."""
        async with sem:
            await self._download_file(url, path, checksum=checksum, force=force, soft=soft)

    _POOL_EXTS = (".deb", ".udeb")

    def _prune_stale_pool_files(self, scan_root: Path, referenced: set[Path]) -> None:
        """Entfernt .deb/.udeb-Dateien, die kein aktueller Packages-Index mehr referenziert.

        Analog zum Stale-Pruning im Archlinux-Modul (downloader.py::_download_arch_group):
        anders als dort gibt es hier keine Downloader-native Verzeichnisliste vom Server,
        daher der Umweg über die aus den Packages-Dateien extrahierten Pool-Pfade als
        Referenz. Nur .deb/.udeb betroffen -- Index-Dateien (InRelease, Packages,
        Contents, ...) werden ohnehin bei jedem Sync unbedingt neu geladen/überschrieben
        und sind hier nicht das Problem.
        """
        if not scan_root.is_dir():
            return
        for path in scan_root.rglob("*"):
            if path.is_file() and path.suffix in self._POOL_EXTS and path not in referenced:
                try:
                    path.unlink()
                    self.stats["pruned"] += 1
                    self._log(f"  🗑️ Veraltet: {path.relative_to(self.staging_path)}")
                except OSError:
                    pass

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
            Liste von {filename, sha256, size, package, version} für alle Pakete
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
                elif line.startswith("Package:"):
                    current["package"] = line.split(":", 1)[1].strip()
                elif line.startswith("Version:"):
                    current["version"] = line.split(":", 1)[1].strip()
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
        self,
        url: str,
        target_path: Path,
        checksum: str | None = None,
        force: bool = False,
        soft: bool = False,
    ) -> tuple[int, str]:
        """Lädt eine einzelne Datei herunter mit Resume-Unterstützung.

        Args:
            url: URL der Datei
            target_path: Ziel-Pfad
            checksum: Optional SHA256-Checksumme
            soft: Fehler nicht in stats["failed"] zählen (z.B. redundante Dateien)
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
            start_size = partial_path.stat().st_size if partial_path.exists() else 0

            # Download in Thread-Pool (blockiert nicht die Event-Loop)
            rc, bytes_written, error_msg = await asyncio.wait_for(
                asyncio.to_thread(
                    self._blocking_download_to_partial, url, start_size, partial_path, self.deadline, self.limit_rate
                ),
                timeout=self.file_timeout,
            )
            self.stats["bytes"] += bytes_written

            # Mirror-Fallback bei Netzwerkfehler (nicht bei HTTP 404)
            if rc != 0 and not _is_http_404(error_msg):
                mirrors = getattr(self, "_mirrors", [])
                primary = getattr(self, "_primary_url", "")
                if primary and url.startswith(primary) and len(mirrors) > 1:
                    rel = url[len(primary):].lstrip("/")
                    for fallback in mirrors[1:]:
                        fallback_url = f"{fallback}/{rel}"
                        self._log(f"  ⚠️ {target_path.name}: Netzwerkfehler, versuche {fallback}...")
                        partial_path.unlink(missing_ok=True)
                        rc2, bw2, err2 = await asyncio.wait_for(
                            asyncio.to_thread(
                                self._blocking_download_to_partial, fallback_url, 0, partial_path, self.deadline, self.limit_rate
                            ),
                            timeout=self.file_timeout,
                        )
                        self.stats["bytes"] += bw2
                        if rc2 == 0:
                            rc, error_msg = 0, ""
                            break

            if rc != 0:
                if soft:
                    self._log(f"⚠️ {target_path.name}: {error_msg} (nicht kritisch)")
                else:
                    self._log(f"❌ {target_path.name}: {error_msg}")
                    self.stats["failed"] += 1
                    self.stats["failed_files"].append((url, error_msg))
                return 1, error_msg

            # Validiere Checksumme (falls vorhanden)
            if checksum:
                file_hash = self._compute_sha256(partial_path)
                if file_hash != checksum:
                    msg = f"Checksumme stimmt nicht: {checksum} vs {file_hash}"
                    if soft:
                        self._log(f"⚠️ {target_path.name}: {msg} (nicht kritisch)")
                    else:
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

        except asyncio.TimeoutError:
            msg = f"Datei-Timeout ({self.file_timeout}s)"
            self._log(f"⏱️ {target_path.name}: {msg}")
            if not soft:
                self.stats["failed"] += 1
                self.stats["failed_files"].append((url, msg))
            return 1, msg
        except Exception as e:
            if soft:
                self._log(f"⚠️ {target_path.name}: {e} (nicht kritisch)")
            else:
                self._log(f"❌ {target_path.name}: {e}")
                self.stats["failed"] += 1
                self.stats["failed_files"].append((url, str(e)))
            return 1, str(e)

    @staticmethod
    def _check_is_flat(url: str, first_suite: str, deadline: float) -> bool:
        """True wenn dists/{suite}/InRelease mit HTTP 404 antwortet (= Flat-Repo).

        Nutzt GET mit Range-Header statt HEAD, da manche Server HEAD nicht unterstützen.
        Bei Timeout, Verbindungsfehlern oder anderen HTTP-Fehlern wird False zurückgegeben
        (Standard-Repo annehmen, Sync soll mit echtem Fehler abbrechen).
        """
        from urllib.error import HTTPError

        probe = f"{url}/dists/{first_suite}/InRelease"
        timeout = min(15.0, max(1.0, deadline - time.time()))
        try:
            req = Request(
                probe,
                headers={"User-Agent": "astrapi-mirror/1.0", "Range": "bytes=0-0"},
            )
            with urlopen(req, timeout=timeout):
                return False  # 200/206 → Standard-Repo
        except HTTPError as e:
            return e.code == 404
        except Exception:
            return False  # Netzwerkfehler → Standard-Repo annehmen

    @staticmethod
    def _blocking_download_to_partial(
        url: str,
        start_size: int,
        partial_path: Path,
        deadline: float,
        limit_rate: int | None = None,
    ) -> tuple[int, int, str]:
        """Blockierender HTTP-Download in Partial-Datei (läuft via asyncio.to_thread).

        Returns:
            (returncode, bytes_written, error_msg)
        """
        bytes_written = 0
        try:
            req = Request(url, headers={"User-Agent": "astrapi-mirror/1.0"})
            if start_size > 0:
                req.add_header("Range", f"bytes={start_size}-")

            with urlopen(req, timeout=300) as resp:
                partial_path.parent.mkdir(parents=True, exist_ok=True)
                mode = "ab" if start_size > 0 else "wb"
                t_throttle = time.monotonic()
                bytes_throttle = 0
                with open(partial_path, mode) as f:
                    while True:
                        if time.time() > deadline:
                            return 1, bytes_written, "Timeout"
                        chunk = resp.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)
                        bytes_written += len(chunk)
                        if limit_rate:
                            bytes_throttle += len(chunk)
                            expected = bytes_throttle / limit_rate
                            elapsed = time.monotonic() - t_throttle
                            if expected > elapsed:
                                time.sleep(expected - elapsed)
            return 0, bytes_written, ""
        except Exception as e:
            return 1, bytes_written, str(e)

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
