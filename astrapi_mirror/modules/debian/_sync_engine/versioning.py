"""astrapi_mirror.modules.debian._sync_engine.versioning – Versioning mit Hardlinks."""

import logging
import shutil
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)


def prepare_staging(
    production_path: Path,
    staging_path: Path,
    on_line: Callable[[str], None] | None = None,
) -> None:
    """Bereitet Staging-Verzeichnis vor (mit Hardlinks zur aktuellen Version).

    Args:
        production_path: Basis-Verzeichnis des Repos (enthält current-Symlink + vN-Dirs)
        staging_path: Pfad zum Staging-Verzeichnis (muss innerhalb production_path liegen)
        on_line: Optional Callback für Logging
    """

    def _log(msg: str) -> None:
        if on_line:
            on_line(msg)

    staging_path = Path(staging_path)
    production_path = Path(production_path)

    # Falls Staging bereits existiert, wurde ein vorheriger Sync unterbrochen.
    # Wir behalten es, damit der Downloader bereits heruntergeladene Dateien
    # überspringen und abgebrochene Downloads fortsetzen kann (Resume-Modus).
    if staging_path.exists():
        _log(f"Resuming interrupted sync: {staging_path.name} already exists")
        return

    staging_path.mkdir(parents=True, exist_ok=True)

    # Hardlinks vom `current`-Symlink-Ziel (z.B. v2/), NICHT vom Basis-Verzeichnis
    current_link = production_path / "current"
    if current_link.is_symlink() or current_link.exists():
        source = current_link.resolve()
        if source.is_dir():
            _log(f"Creating hardlinks from {source.name}/ to staging/")
            _hardlink_tree(source, staging_path)
            return
    _log("No existing production version, starting fresh")


def _hardlink_tree(src: Path, dst: Path) -> int:
    """Erstellt rekursiv Hardlinks von src zu dst.

    Returns:
        Anzahl der erstellten Hardlinks
    """
    count = 0
    for item in src.rglob("*"):
        if item.is_dir():
            (dst / item.relative_to(src)).mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            rel_path = item.relative_to(src)
            dst_file = dst / rel_path
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                # Versuche Hardlink zu erstellen
                dst_file.unlink(missing_ok=True)
                dst_file.hardlink_to(item)
                count += 1
            except Exception as e:
                # Fallback auf Copy bei Fehler (z.B. Cross-Filesystem)
                log.warning("Hardlink failed for %s, falling back to copy: %s", item, e)
                shutil.copy2(item, dst_file)
                count += 1
    return count


def atomic_swap(staging_path: Path, production_path: Path) -> Path:
    """Führt atomaren Swap von Staging zu Production durch.

    Strategie:
    1. Erstelle neue Version (v2) vom Staging
    2. Erstelle neuen symlink `current` → v2
    3. Alte Version bleibt als Fallback erhalten

    Args:
        staging_path: Pfad zum Staging-Verzeichnis (z.B. v2-staging/)
        production_path: Basis-Pfad (z.B. /mirror/deb.debian.org/debian/)

    Returns:
        Neuer Versions-Pfad (z.B. v2/)
    """
    staging_path = Path(staging_path)
    production_base = (
        Path(production_path).parent
        if production_path.name.endswith("-staging")
        else Path(production_path)
    )

    if not staging_path.exists():
        raise RuntimeError(f"Staging path does not exist: {staging_path}")

    # Bestimme neue Versionsnummer
    new_version_num = _get_next_version_number(production_base)
    new_version_path = production_base / f"v{new_version_num}"

    # Falls neue Version existiert, lösche sie
    if new_version_path.exists():
        shutil.rmtree(new_version_path)

    # Rename staging → vN
    staging_path.rename(new_version_path)
    log.info("Renamed %s to %s", staging_path, new_version_path)

    # Update symlink `current` → vN
    current_link = production_base / "current"
    temp_link = production_base / ".current.tmp"

    try:
        # Erstelle neuen symlink in temp
        temp_link.unlink(missing_ok=True)
        temp_link.symlink_to(new_version_path.name)

        # Atomarer Swap (rename ist atomar auf POSIX)
        temp_link.replace(current_link)
        log.info("Updated symlink: current -> %s", new_version_path.name)
    except Exception as e:
        # Fallback: direkt überschreiben (weniger atomar, aber sicherer)
        current_link.unlink(missing_ok=True)
        current_link.symlink_to(new_version_path.name)
        log.warning("Fallback symlink update: %s", e)

    return new_version_path


def _get_next_version_number(base_path: Path) -> int:
    """Bestimmt nächste Versionsnummer basierend auf existierenden vN-Verzeichnissen."""
    base_path = Path(base_path)
    max_num = 0

    for item in base_path.glob("v*"):
        if item.is_dir():
            try:
                num = int(item.name[1:])  # v1 → 1
                max_num = max(max_num, num)
            except ValueError:
                pass

    return max_num + 1


def cleanup_old_versions(base_path: Path, keep: int = 3) -> None:
    """Löscht alte Versionen, behalte die letzten N Versionen.

    Args:
        base_path: Basis-Pfad mit vN-Verzeichnissen
        keep: Anzahl der zu behaltenden Versionen
    """
    base_path = Path(base_path)

    # Sammle alle vN-Verzeichnisse mit deren Versionsnummern
    versions = []
    for item in base_path.glob("v*"):
        if item.is_dir():
            try:
                num = int(item.name[1:])
                versions.append((num, item))
            except ValueError:
                pass

    # Sortiere nach Nummer (absteigend) und lösche alte
    versions.sort(reverse=True)

    for num, version_path in versions[keep:]:
        log.info("Deleting old version: %s", version_path)
        shutil.rmtree(version_path, ignore_errors=True)
