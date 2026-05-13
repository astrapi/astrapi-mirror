"""astrapi-mirror-spezifische Konfiguration des Core-Sysinfo-Moduls."""

from astrapi_core.modules.system import module  # noqa: F401 – sicherstellt dass Modul registriert
from astrapi_core.modules.system.engine import configure

from astrapi_mirror._paths import db_path as _db_path
from astrapi_mirror._paths import package_dir as _package_dir


def _db_info() -> str:
    from astrapi_core.system.format import fmt_bytes

    p = _db_path()
    size = fmt_bytes(p.stat().st_size) if p.exists() else "—"
    return f"{p} · {size}"


def _pyproject_deps() -> dict[str, str]:
    """Installierte Versionen aller direkten Paket-Abhängigkeiten."""
    import re
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import requires as _requires
    from importlib.metadata import version as _ver

    result = {}
    try:
        for req in _requires("astrapi-mirror") or []:
            if ";" in req:
                continue
            name = re.split(r"[>=<!~\[\s]", req)[0].strip()
            if not name:
                continue
            try:
                result[name] = _ver(name)
            except PackageNotFoundError:
                result[name] = "—"
    except Exception:
        pass
    return dict(sorted(result.items()))


def _extra_info() -> dict:
    return {
        "Datenbank": _db_info(),
        **_pyproject_deps(),
    }


def _discover_services() -> list[str]:
    try:
        import subprocess

        import yaml as _yaml

        app_yaml = _package_dir() / "app.yaml"
        name = str((_yaml.safe_load(app_yaml.read_text()) or {}).get("name", ""))
        if not name:
            return []
        out = subprocess.run(
            [
                "systemctl",
                "list-units",
                "--all",
                "--no-legend",
                "--plain",
                "--type=service",
                f"{name}*",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        return [
            line.split()[0].removesuffix(".service") for line in out.splitlines() if line.strip()
        ]
    except Exception:
        return []


def _update_packages():
    from astrapi_core.modules.system.engine import get_packages_with_versions

    return get_packages_with_versions()


configure(
    services=_discover_services(),
    extra_info_fn=_extra_info,
    update_packages_fn=_update_packages,
)
