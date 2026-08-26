# astrapi_mirror/_paths.py
from pathlib import Path

from astrapi_core.system.paths import db_path, log_dir, work_dir  # noqa: F401 – re-export


def package_dir() -> Path:
    """Pfad zum installierten Package – für app.yaml, Templates, Modul-YAMLs."""
    return Path(__file__).resolve().parent


def mirror_path() -> Path:
    """Wurzelverzeichnis des Debian-Spiegels. Zusatzspeicher ist Pflicht
    (kein stiller Rückfall aufs Arbeitsverzeichnis) -- ein Debian-Spiegel
    ist um Größenordnungen zu groß für die Root-Partition."""
    from astrapi_core.system.paths import require_extra_disk

    return Path(require_extra_disk()).resolve() / "debian"


def archlinux_mirror_path() -> Path:
    """Wurzelverzeichnis des Arch Linux Spiegels. Zusatzspeicher ist
    Pflicht, siehe mirror_path()."""
    from astrapi_core.system.paths import require_extra_disk

    return Path(require_extra_disk()).resolve() / "archlinux"
