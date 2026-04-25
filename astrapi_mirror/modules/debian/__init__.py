from pathlib import Path
from astrapi_core.ui.module_loader import load_modul
from .api import router
from .ui import router as ui_router
from .storage import store
from ._seed import auto_seed

_KEY = Path(__file__).parent.name
module = load_modul(Path(__file__).parent, _KEY, router, ui_router)

auto_seed(store)

try:
    from astrapi_core.modules.scheduler.engine import register_action
    from .jobs import sync_all
    register_action(f"{_KEY}.sync_all", "Debian: Alle Repos syncen",
                    sync_all, source=_KEY, source_label="Debian Mirror")
except Exception:
    pass
