"""astrapi_mirror.modules.debian.engine – Validierungs-Logik für Debian-Mirror.

Zuständig für:
- Vollständigkeits-Validierung nach dem Sync (Manifest-Check)
- sources.list-Snippet-Generierung für Clients
"""

from __future__ import annotations

import logging
import re
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GPG-Hilfsfunktion
# ---------------------------------------------------------------------------


def _armor_inline(armored_key: str) -> str:
    """Formatiert einen armierten GPG-Schlüssel als DEB822-Multiline-Fortsetzung.

    Jede Zeile wird mit einem führenden Leerzeichen versehen; Leerzeilen
    werden zu ' .' (RFC 822-Konvention für leere Fortsetzungszeilen).
    """
    result = []
    for line in armored_key.strip().splitlines():
        result.append(f" {line}" if line.strip() else " .")
    return "\n".join(result)


# ---------------------------------------------------------------------------
# Flat-Repo-Erkennung
# ---------------------------------------------------------------------------


def detect_flat(url: str, suites: list[str] | None = None) -> bool:
    """Erkennt automatisch ob ein Repo flat ist (kein dists/-Verzeichnis).

    Prüfreihenfolge via HTTP HEAD:
    1. {url}/dists/{erste_suite}/InRelease  → 2xx = nicht flat
    2. {url}/dists/                          → 2xx = nicht flat
    3. {url}/InRelease                       → 2xx = flat
    4. Fallback                              → False (Standard-Repo)
    """
    url = (url or "").rstrip("/")
    if not url:
        return False

    def _head_ok(probe_url: str) -> bool:
        try:
            req = urllib.request.Request(probe_url, method="HEAD")
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status < 400
        except Exception:
            return False

    # Zuerst Standard-Layout prüfen (nicht flat)
    clean_suites = [s.strip() for s in (suites or []) if s.strip()]
    if clean_suites and _head_ok(f"{url}/dists/{clean_suites[0]}/InRelease"):
        log.debug("detect_flat(%s): nicht flat (dists/%s/InRelease gefunden)", url, clean_suites[0])
        return False
    if _head_ok(f"{url}/dists/"):
        log.debug("detect_flat(%s): nicht flat (dists/ gefunden)", url)
        return False

    # Flat-Layout prüfen
    if _head_ok(f"{url}/InRelease"):
        log.debug("detect_flat(%s): flat (InRelease im Root)", url)
        return True

    log.debug("detect_flat(%s): Fallback → nicht flat", url)
    return False


# ---------------------------------------------------------------------------
# Validierung
# ---------------------------------------------------------------------------


def _host_path_from_url(url: str) -> str:
    """Gibt 'hostname/pfad' aus einer URL zurück."""
    p = urlparse(url)
    return (p.hostname or "") + p.path.rstrip("/")


_ARCH_IN_PATH = re.compile(r"(?:^|/)binary-([^/]+)/")
_ARCH_IN_NAME = re.compile(r"(?:^|/)Contents-([a-zA-Z0-9_]+)")
_COMPONENT_PREFIX = re.compile(r"^([^/]+)/")


def _should_skip(
    filename: str,
    arch_set: set[str] | None,
    component_set: set[str] | None,
    include_sources: bool,
) -> bool:
    """True wenn die Datei aufgrund der Repo-Konfiguration nicht synchronisiert wurde."""
    # Component-Filter: erstes Pfadsegment ist die Component (main, contrib, …)
    if component_set is not None:
        m = _COMPONENT_PREFIX.match(filename)
        if m and m.group(1) not in component_set:
            return True

    # Source-Filter: source/-Verzeichnis nur bei deb-src vorhanden
    if not include_sources and "/source/" in filename:
        return True

    # Architektur-Filter: binary-{arch}/ und Contents-{arch}
    if arch_set is not None:
        m = _ARCH_IN_PATH.search(filename)
        if m and m.group(1) not in arch_set:
            return True
        m = _ARCH_IN_NAME.search(filename)
        if m and m.group(1) not in arch_set:
            return True

    return False


def _check_release_file(
    release_path: Path,
    architectures: list[str] | None = None,
    components: list[str] | None = None,
    include_sources: bool = True,
) -> tuple[bool, list[str]]:
    """Parst eine InRelease-Datei und prüft alle darin referenzierten Dateien.

    Dateien für nicht-konfigurierte Architekturen, Components oder Source-Pakete
    werden übersprungen, da refrapt sie nie heruntergeladen hat.
    """
    issues: list[str] = []
    if not release_path.exists():
        return False, [f"InRelease nicht gefunden: {release_path}"]

    base_dir = release_path.parent
    try:
        content = release_path.read_text(errors="replace")
    except Exception as e:
        return False, [f"Lesefehler: {e}"]

    arch_set: set[str] | None = set(architectures) if architectures else None
    component_set: set[str] | None = set(components) if components else None

    in_block = False
    checked = 0
    for line in content.splitlines():
        if line.startswith(("SHA256:", "SHA512:", "MD5Sum:")):
            in_block = True
            continue
        if in_block:
            if not line.startswith(" "):
                in_block = False
                continue
            parts = line.strip().split()
            if len(parts) < 3:
                continue
            _checksum, size_str, filename = parts[0], parts[1], parts[2]

            if _should_skip(filename, arch_set, component_set, include_sources):
                continue

            file_path = base_dir / filename
            if not file_path.exists():
                issues.append(f"Fehlende Datei: {filename}")
                continue
            try:
                actual_size = file_path.stat().st_size
                expected_size = int(size_str)
                if actual_size != expected_size:
                    issues.append(
                        f"Größe stimmt nicht: {filename} "
                        f"(erwartet {expected_size}, gefunden {actual_size})"
                    )
            except (ValueError, OSError):
                pass
            checked += 1
            if checked >= 500:
                break

    return len(issues) == 0, issues


def validate_repo(repo: dict, base_path: Path | None = None) -> dict:
    """Validiert einen Repo-Eintrag; gibt {'status', 'issues', 'checked_suites'} zurück.

    Args:
        repo: Repo-Dict mit id, url, suites, architectures, components
        base_path: Optionaler Basis-Pfad (z.B. Staging-Dir während Sync).
                   Wenn None, wird mirror_path/{repo_id}/current verwendet.
    """
    from astrapi_mirror._paths import mirror_path

    if repo.get("is_flat"):
        return {"status": "ok", "issues": [], "checked_suites": 0}

    url = (repo.get("url") or "").rstrip("/")

    if base_path is not None:
        mirror_base = Path(base_path)
    else:
        repo_id = repo.get("slug") or str(repo.get("id", ""))
        current_link = mirror_path() / repo_id / "current"
        if current_link.exists():
            mirror_base = current_link  # folgt dem Symlink automatisch
        else:
            # Fallback: alter URL-basierter Pfad
            host_path = _host_path_from_url(url)
            mirror_base = mirror_path() / host_path

    suites = [s.strip() for s in (repo.get("suites") or []) if s.strip()]
    archs = [a.strip() for a in (repo.get("architectures") or []) if a.strip()]
    comps = [c.strip() for c in (repo.get("components") or []) if c.strip()]
    include_sources = repo.get("repo_type", "deb") == "deb-src"
    all_issues: list[str] = []
    checked = 0

    for suite in suites:
        release_path = mirror_base / "dists" / suite / "InRelease"
        ok, issues = _check_release_file(
            release_path,
            architectures=archs or None,
            components=comps or None,
            include_sources=include_sources,
        )
        all_issues.extend(issues)
        checked += 1

    status = "ok" if not all_issues else "error"
    return {"status": status, "issues": all_issues, "checked_suites": checked}


def validate_all(repos: list[dict]) -> dict[str, dict]:
    """Validiert alle Repos; gibt {slug: result} zurück."""
    return {
        (repo.get("slug") or str(repo["id"])): validate_repo(repo)
        for repo in repos
        if repo.get("enabled", True)
    }


# ---------------------------------------------------------------------------
# DEB822-.sources-Snippet
# ---------------------------------------------------------------------------


def client_sources_file(repo: dict, base_url: str) -> str:
    """Erzeugt den Inhalt einer DEB822 .sources-Datei für apt.

    Format (Beispiel):
        Types: deb
        URIs: https://mirror.example.com/repo/debian/caddy
        Suites: bookworm
        Components: main
        Architectures: amd64 arm64
        Signed-By: /etc/apt/trusted.gpg.d/caddy.gpg
    """
    base_url = base_url.rstrip("/")
    repo_id = repo.get("slug") or str(repo.get("id", ""))
    mirror_url = f"{base_url}/repo/debian/{repo_id}"

    lines: list[str] = [f"Types: {repo.get('repo_type', 'deb')}"]

    if repo.get("is_flat"):
        lines.append(f"URIs: {mirror_url}/")
        lines.append("Suites: ./")
    else:
        suites = [s.strip() for s in (repo.get("suites") or []) if s.strip()]
        components = [c.strip() for c in (repo.get("components") or []) if c.strip()]
        lines.append(f"URIs: {mirror_url}")
        if suites:
            lines.append(f"Suites: {' '.join(suites)}")
        if components:
            lines.append(f"Components: {' '.join(components)}")

    archs = [a.strip() for a in (repo.get("architectures") or []) if a.strip()]
    if archs:
        lines.append(f"Architectures: {' '.join(archs)}")

    gpg_key = (repo.get("gpg_key") or "").strip()
    if gpg_key.startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----"):
        lines.append(f"Signed-By:\n{_armor_inline(gpg_key)}")
    elif gpg_key:
        # Binär gespeicherter Key (Fallback bis zum nächsten Sync)
        lines.append(f"Signed-By: /etc/apt/trusted.gpg.d/{repo_id}.gpg")

    return "\n".join(lines) + "\n"
