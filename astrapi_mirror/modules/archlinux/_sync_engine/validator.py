"""astrapi_mirror.modules.archlinux._sync_engine.validator – Validierung via Docker."""

import logging
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


def test_pacman_sync(repo_id: str, staging_path: Path) -> tuple[bool, str]:
    """Testet einen Arch Linux Mirror via Docker-Container.

    Startet einen archlinux Container mit dem Staging-Verzeichnis
    gemountet und führt pacman -Sy durch.

    Args:
        repo_id: Repo-ID (für Logging)
        staging_path: Pfad zum Staging-Verzeichnis

    Returns:
        (success: bool, message: str)
        - success=True wenn pacman -Sy erfolgreich war
        - success=False mit Fehlermeldung sonst
    """
    if not staging_path.exists():
        return False, f"Staging-Verzeichnis nicht gefunden: {staging_path}"

    try:
        # Erstelle temporäre pacman.conf für den Test
        pacman_conf = _create_test_pacman_conf(repo_id, staging_path)

        # Docker-Befehl: Archlinux Container mit Test-Repo
        cmd = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{staging_path}:{staging_path}:ro",
            "-v",
            f"{pacman_conf}:/etc/pacman.conf:ro",
            "--entrypoint",
            "sh",
            "archlinux:latest",
            "-c",
            "pacman -Sy --noconfirm 2>&1 | head -30",
        ]

        result = subprocess.run(cmd, timeout=60, capture_output=True, text=True)

        Path(pacman_conf).unlink(missing_ok=True)

        if result.returncode == 0:
            log.info("docker-test [%s]: erfolgreich", repo_id)
            return True, "pacman -Sy erfolgreich"
        else:
            error_msg = result.stderr or result.stdout
            log.warning("docker-test [%s]: fehlgeschlagen\n%s", repo_id, error_msg)
            return False, error_msg[:200]

    except FileNotFoundError:
        log.debug("docker-test: Docker nicht verfügbar")
        return False, "Docker nicht installiert"
    except subprocess.TimeoutExpired:
        log.warning("docker-test [%s]: Timeout", repo_id)
        return False, "Timeout bei pacman -Sy (>60s)"
    except Exception as e:
        log.error("docker-test [%s]: Fehler: %s", repo_id, e)
        return False, str(e)[:100]


def _create_test_pacman_conf(repo_id: str, staging_path: Path) -> str:
    """Erstellt temporäre pacman.conf für den Docker-Test."""
    staging_abs = staging_path.resolve()

    conf_content = f"""[options]
HoldPkg = pacman glibc
Architecture = auto
CheckSpace
ParallelDownloads = 5

[{repo_id}]
Server = file://{staging_abs}/$arch
"""

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False)
    tmp.write(conf_content)
    tmp.close()
    return tmp.name


def quick_validate(repo: dict, base_path: Path) -> tuple[bool, list[str]]:
    """Schnelle Validierung ohne Docker (nur Dateistruktur-Checks).

    Returns:
        (ok: bool, issues: list[str])
    """
    issues = []

    architectures = repo.get("architectures", ["x86_64"])
    if isinstance(architectures, str):
        architectures = [a.strip() for a in architectures.split(",")]

    for arch in architectures:
        arch_path = base_path / "os" / arch
        if not arch_path.exists():
            issues.append(f"Architektur {arch}: Verzeichnis nicht gefunden")
            continue

        # Prüfe auf db.tar.gz
        db_files = list(arch_path.glob("*.db.tar.gz"))
        if not db_files:
            issues.append(f"Architektur {arch}: Keine *.db.tar.gz gefunden")

    return len(issues) == 0, issues
