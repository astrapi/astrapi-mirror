"""astrapi_mirror._repo_info – Hilfsfunktionen für Repo-Informationen (Disk, Versionen)."""

from pathlib import Path


def _fmt_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return f"{size:.1f} PB"


def _dir_size(path: Path) -> int:
    total = 0
    for f in path.rglob("*"):
        if f.is_file() and not f.is_symlink():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def _count_files(path: Path, suffixes: tuple[str, ...]) -> int:
    return sum(
        1 for f in path.rglob("*")
        if f.is_file() and not f.is_symlink() and f.suffix in suffixes
    )


def repo_info(repo_path: Path, pkg_suffixes: tuple[str, ...] = (".zst", ".deb")) -> dict:
    """Gibt Disk- und Versions-Informationen für ein Repo-Verzeichnis zurück.

    Args:
        repo_path:    Pfad zum Repo-Root (z.B. /storage/archlinux/extra)
        pkg_suffixes: Dateiendungen die als „Pakete" zählen

    Returns dict mit:
        current_version  – z.B. "v3" oder None
        current_size     – Größe der aktuellen Version in Bytes (oder None)
        current_size_fmt – Lesbare Größe der aktuellen Version
        total_size_fmt   – Lesbare Gesamtgröße aller Versionen
        version_count    – Anzahl vN-Verzeichnisse
        versions         – Liste der Versionsverzeichnisse (aufsteigend)
        pkg_count        – Anzahl Paketdateien in current
        staging_exists   – True wenn Staging-Verzeichnis vorhanden
        published        – True wenn current-Symlink existiert
    """
    if not repo_path.exists():
        return {"published": False}

    # Versionen ermitteln
    versions = sorted(
        d.name for d in repo_path.iterdir()
        if d.is_dir() and d.name.startswith("v") and d.name[1:].isdigit()
    )

    staging_exists = (repo_path / "staging").exists()
    current_link = repo_path / "current"
    published = current_link.exists()

    current_version: str | None = None
    current_size: int | None = None
    pkg_count = 0

    if published:
        try:
            current_version = current_link.resolve().name
            current_size = _dir_size(current_link)
            pkg_count = _count_files(current_link, pkg_suffixes)
        except OSError:
            pass

    total_size = sum(
        _dir_size(repo_path / v) for v in versions
    )
    if staging_exists:
        total_size += _dir_size(repo_path / "staging")

    return {
        "published": published,
        "current_version": current_version,
        "current_size": current_size,
        "current_size_fmt": _fmt_size(current_size) if current_size is not None else "—",
        "total_size_fmt": _fmt_size(total_size) if total_size else "—",
        "version_count": len(versions),
        "versions": versions,
        "pkg_count": pkg_count,
        "staging_exists": staging_exists,
    }
