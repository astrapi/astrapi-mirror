# astrapi_mirror/_paths.py
from pathlib import Path

from astrapi_core.system.paths import db_path, log_dir, work_dir  # noqa: F401 – re-export


def package_dir() -> Path:
    """Pfad zum installierten Package – für app.yaml, Templates, Modul-YAMLs."""
    return Path(__file__).resolve().parent


def _extra_disk() -> str:
    """Gibt den ersten konfigurierten Zusatzspeicher zurück, oder ''."""
    from astrapi_core.ui.settings_registry import get_module

    raw = get_module("system", "extra_disks", default="") or ""
    for part in raw.split(","):
        path = part.strip()
        if path:
            return path
    return ""


def mirror_path() -> Path:
    """Wurzelverzeichnis des Debian-Spiegels."""
    disk = _extra_disk()
    if disk:
        return Path(disk).resolve() / "debian"
    return work_dir().resolve() / "mirror"


def archlinux_mirror_path() -> Path:
    """Wurzelverzeichnis des Arch Linux Spiegels."""
    disk = _extra_disk()
    if disk:
        return Path(disk).resolve() / "archlinux"
    return work_dir().resolve() / "mirror_arch"
