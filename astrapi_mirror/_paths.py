# astrapi_mirror/_paths.py
from pathlib import Path

from astrapi_core.system.paths import db_path, log_dir, work_dir  # noqa: F401 – re-export


def package_dir() -> Path:
    """Pfad zum installierten Package – für app.yaml, Templates, Modul-YAMLs."""
    return Path(__file__).resolve().parent


def mirror_path() -> Path:
    """Wurzelverzeichnis des Debian-Spiegels (aus Settings oder Standard)."""
    from astrapi_core.ui.settings_registry import get_module

    raw = get_module("debian", "mirror_path", default="")
    return Path(raw).resolve() if raw else work_dir().resolve() / "mirror"


def archlinux_mirror_path() -> Path:
    """Wurzelverzeichnis des Arch Linux Spiegels (aus Settings oder Standard)."""
    from astrapi_core.ui.settings_registry import get_module

    raw = get_module("archlinux", "mirror_path", default="")
    return Path(raw).resolve() if raw else work_dir().resolve() / "mirror_arch"
