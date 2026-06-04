from pathlib import Path
from typing import Optional

from astrapi_core.ui.module_loader import load_modul
from pydantic import BaseModel

from .storage import DebianRepoStore

_KEY = Path(__file__).parent.name
KEY = _KEY
store = DebianRepoStore()

# ── Pydantic-Modell ───────────────────────────────────────────────────────────


class RepoIn(BaseModel):
    label: str = ""
    url: str = ""
    repo_type: str = "deb"
    suites: list[str] = []
    components: list[str] = []
    architectures: list[str] = []
    enabled: bool = True
    gpg_key_url: str = ""


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
            Col.trunc("url", "URL"),
            Col.text("slug", "Slug"),
        ],
    ),
)

try:
    from astrapi_core.modules.scheduler.engine import register_action

    from .jobs import sync_all

    register_action(
        f"{_KEY}.sync_all",
        "Debian: Alle Repos syncen",
        sync_all,
        source=_KEY,
        source_label="Debian Mirror",
    )
except Exception:
    pass
