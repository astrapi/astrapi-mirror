"""astrapi_mirror.modules.debian._sync_engine.validator – Validierungs-Logik für Debian-Mirror."""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Docker-Validierung
# ---------------------------------------------------------------------------


def test_apt_update(repo_id: str, staging_path: Path) -> tuple[bool, str]:
    """Testet einen Debian-Mirror via Docker-Container (apt-get update)."""
    if not staging_path.exists():
        return False, f"Staging-Verzeichnis nicht gefunden: {staging_path}"

    sources_content = f"""deb [trusted=yes] file://{staging_path}/dists/bookworm bookworm main contrib non-free
deb [trusted=yes] file://{staging_path}/dists/bookworm-updates bookworm-updates main contrib non-free
"""
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".list", delete=False) as tmp:
            tmp.write(sources_content)
            sources_file = tmp.name

        cmd = [
            "docker", "run", "--rm",
            "-v", f"{staging_path}:{staging_path}:ro",
            "-v", f"{sources_file}:/etc/apt/sources.list.d/test.list:ro",
            "--entrypoint", "sh", "debian:bookworm",
            "-c", "apt-get update 2>&1 | head -20",
        ]
        result = subprocess.run(cmd, timeout=60, capture_output=True, text=True)
        Path(sources_file).unlink(missing_ok=True)

        if result.returncode == 0:
            log.info("docker-test [%s]: erfolgreich", repo_id)
            return True, "apt-get update erfolgreich"
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


# ---------------------------------------------------------------------------
# Strukturelle Validierung (kein Docker nötig)
# ---------------------------------------------------------------------------


def _host_path_from_url(url: str) -> str:
    p = urlparse(url)
    return (p.hostname or "") + p.path.rstrip("/")


_ARCH_IN_PATH = re.compile(r"(?:^|/)binary-([^/]+)/")
_ARCH_IN_NAME = re.compile(r"(?:^|/)Contents-([a-zA-Z0-9_]+)")
_DEP11_ARCH = re.compile(r"/dep11/Components-([^./]+)\.")
_COMPONENT_PREFIX = re.compile(r"^([^/]+)/")
_TRANSLATION_IN_PATH = re.compile(r"(?:^|/)i18n/Translation-([^./]+)")
_OPTIONAL_INDEX_SUFFIXES = ("/Packages", "/Sources")


def _index_group_key(filename: str) -> str | None:
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
    if filename.endswith(".xz"):
        return 0
    if filename.endswith(".gz"):
        return 1
    if filename.endswith(".bz2"):
        return 2
    return 3


def _select_expected_release_files(filenames: list[str]) -> set[str]:
    selected: dict[str, str] = {}
    passthrough: set[str] = set()
    for filename in filenames:
        key = _index_group_key(filename)
        if key is None:
            continue
        if key == filename:
            passthrough.add(filename)
            continue
        current = selected.get(key)
        if current is None or _variant_rank(filename) < _variant_rank(current):
            selected[key] = filename
    return passthrough | set(selected.values())


def _as_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


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


def _should_skip(
    filename: str,
    arch_set: set[str] | None,
    component_set: set[str] | None,
    include_sources: bool,
    include_contents: bool,
    language_set: set[str] | None,
) -> bool:
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


def _check_release_file(
    release_path: Path,
    architectures: list[str] | None = None,
    components: list[str] | None = None,
    include_sources: bool = True,
    include_contents: bool = True,
    language_set: set[str] | None = None,
) -> tuple[bool, list[str]]:
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

    selected_filenames = _select_expected_release_files(
        [
            parts[2]
            for line in content.splitlines()
            if line.startswith(" ") and len((parts := line.strip().split())) >= 3
        ]
    )
    seen_files: set[str] = set()
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
            if filename not in selected_filenames:
                continue
            if filename in seen_files:
                continue
            seen_files.add(filename)
            if _should_skip(filename, arch_set, component_set, include_sources, include_contents, language_set):
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
    """Validiert einen Repo-Eintrag; gibt {'status', 'issues', 'checked_suites'} zurück."""
    from astrapi_mirror._paths import mirror_path

    url = (repo.get("url") or "").rstrip("/")

    if base_path is not None:
        mirror_base = Path(base_path)
    else:
        repo_id = repo.get("slug") or str(repo.get("id", ""))
        current_link = mirror_path() / repo_id / "current"
        if current_link.exists():
            mirror_base = current_link
        else:
            host_path = _host_path_from_url(url)
            mirror_base = mirror_path() / host_path

    if not (mirror_base / "dists").is_dir():
        return {"status": "ok", "issues": [], "checked_suites": 0}

    suites = [s.strip() for s in (repo.get("suites") or []) if s.strip()]
    archs = [a.strip() for a in (repo.get("architectures") or []) if a.strip()]
    comps = [c.strip() for c in (repo.get("components") or []) if c.strip()]
    include_sources = repo.get("repo_type", "deb") == "deb-src"
    include_contents = _should_include_contents()
    language_set = _configured_languages()
    all_issues: list[str] = []
    checked = 0

    for suite in suites:
        release_path = mirror_base / "dists" / suite / "InRelease"
        ok, issues = _check_release_file(
            release_path,
            architectures=archs or None,
            components=comps or None,
            include_sources=include_sources,
            include_contents=include_contents,
            language_set=language_set,
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
