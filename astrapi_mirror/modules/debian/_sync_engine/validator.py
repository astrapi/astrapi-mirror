"""astrapi_mirror.modules.debian._sync_engine.validator – Docker-basierte apt-Validierung.

Validiert nach dem Sync mittels Docker-Container, ob ein Repo mit apt-get update
funktioniert. Optional, da nicht immer Docker verfügbar ist.
"""

import logging
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


def test_apt_update(repo_id: str, staging_path: Path) -> tuple[bool, str]:
    """
    Testet einen Debian-Mirror via Docker-Container.

    Startet einen debian:bookworm Container mit dem Staging-Verzeichnis
    gemountet und führt apt-get update durch.

    Args:
        repo_id: Repo-ID (für Logging)
        staging_path: Pfad zum Staging-Verzeichnis

    Returns:
        (success: bool, message: str)
        - success=True wenn apt-get update erfolgreich war
        - success=False mit Fehlermeldung sonst
    """
    if not staging_path.exists():
        return False, f"Staging-Verzeichnis nicht gefunden: {staging_path}"

    # Erstelle temporäre sources.list für den Test
    sources_content = f"""deb [trusted=yes] file://{staging_path}/dists/bookworm bookworm main contrib non-free
deb [trusted=yes] file://{staging_path}/dists/bookworm-updates bookworm-updates main contrib non-free
"""

    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".list", delete=False) as tmp:
            tmp.write(sources_content)
            sources_file = tmp.name

        # Docker-Befehl
        cmd = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{staging_path}:{staging_path}:ro",
            "-v",
            f"{sources_file}:/etc/apt/sources.list.d/test.list:ro",
            "--entrypoint",
            "sh",
            "debian:bookworm",
            "-c",
            "apt-get update 2>&1 | head -20",
        ]

        result = subprocess.run(cmd, timeout=60, capture_output=True, text=True)

        Path(sources_file).unlink(missing_ok=True)

        if result.returncode == 0:
            log.info("docker-test [%s]: erfolgreich", repo_id)
            return True, "apt-get update erfolgreich"
        else:
            error_msg = result.stderr or result.stdout
            log.warning("docker-test [%s]: fehlgeschlagen\n%s", repo_id, error_msg)
            return False, error_msg[:200]

    except FileNotFoundError:
        log.debug("docker-test: Docker nicht verfügbar")
        return False, "Docker nicht installiert"
    except subprocess.TimeoutExpired:
        log.warning("docker-test [%s]: Timeout nach 60s", repo_id)
        return False, "Timeout (60s)"
    except Exception as e:
        log.warning("docker-test [%s]: %s", repo_id, e)
        return False, str(e)[:200]
