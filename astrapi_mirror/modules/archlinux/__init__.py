"""astrapi_mirror.modules.archlinux – Arch Linux Repository Manager."""

from pathlib import Path
from typing import Optional

from astrapi_core.ui.module_loader import load_modul
from pydantic import BaseModel

from .storage import ArchlinuxRepoStore

_KEY = Path(__file__).parent.name
KEY = _KEY
store = ArchlinuxRepoStore()

# ── Pydantic-Modell ───────────────────────────────────────────────────────────


class RepoIn(BaseModel):
    label: str = ""
    mirror_urls: list[str] = []
    enabled: bool = True


# ── Router laden ───────────────────────────────────────────────────────────────

# ── Modul registrieren ─────────────────────────────────────────────────────────
from astrapi_core.ui.controls import Col, ContentTable  # noqa: E402

from .api import router  # noqa: E402
from .ui import router as ui_router  # noqa: E402

module = load_modul(
    Path(__file__).parent,
    _KEY,
    router,
    ui_router,
    ui_content=ContentTable(
        columns=[
            Col.text("mirror_count", "Mirrors", sortable=False),
            Col.text("slug", "Slug"),
        ],
    ),
)

# ── Auto-Seed ──────────────────────────────────────────────────────────────────

try:
    from ._seed import auto_seed

    auto_seed(store)
except Exception:
    pass

# ── Scheduler ──────────────────────────────────────────────────────────────────

try:
    from astrapi_core.modules.scheduler.engine import register_action

    from .jobs import sync_all

    register_action(
        f"{_KEY}.sync_all",
        "Arch Linux: Alle Repos syncen",
        sync_all,
        source=_KEY,
        source_label="Arch Linux Mirror",
    )
except Exception:
    pass
