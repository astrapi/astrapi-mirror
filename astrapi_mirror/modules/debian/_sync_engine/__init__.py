"""astrapi_mirror.modules.debian._sync_engine – Interne Synchronisierungs-Engine für Debian-Mirror.

Ersatz für refrapt mit folgenden Features:
- Paralleles Downloading via asyncio
- Resume-Unterstützung für unterbrochene Downloads
- Retry-Logik mit Exponential-Backoff
- Checksummen-Validierung (SHA256/SHA512)
- Versioning mit Hardlinks für Speicher-Effizienz
- Atomare Swaps für sichere Übernahmen
- Docker-basierte apt-Validierung
"""

import asyncio
from typing import Callable

from .engine import SyncEngine as _SyncEngineAsync


class SyncEngine:
    """Synchrone Wrapper für die async Sync-Engine.

    Nutzt asyncio.run() um async-Funktionen von synchronem Code aus aufzurufen.
    """

    def __init__(self):
        """Initialisiert die Engine mit Standard-Pfaden."""
        from astrapi_mirror._paths import mirror_path

        self.mirror_root = mirror_path()
        self.partial_root = self.mirror_root / ".partial"
        self._engine = _SyncEngineAsync(self.mirror_root, self.partial_root)

    def sync_repos(
        self,
        repos: list[dict],
        on_line: Callable[[str], None] | None = None,
    ) -> tuple[int, str]:
        """Synchronisiert mehrere Repos synchron (wrapper für async)."""
        return asyncio.run(self._engine.sync_repos(repos, on_line))

    def sync_repo(
        self,
        repo: dict,
        on_line: Callable[[str], None] | None = None,
    ) -> tuple[int, str]:
        """Synchronisiert ein Repo synchron (wrapper für async)."""
        return asyncio.run(self._engine.sync_repo(repo, on_line))


__all__ = ["SyncEngine"]
