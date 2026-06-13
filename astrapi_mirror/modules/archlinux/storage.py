"""astrapi_mirror.modules.archlinux.storage – ArchlinuxRepoStore.

Eigene SQLite-Tabelle `arch_repos` statt kvstore-JSON-Blobs.
Integer-ID (auto-increment) + slug (auto aus label, unveränderlich nach Anlage).
"""

from __future__ import annotations

import json
import re
import threading
from typing import Callable

_TABLE = "arch_repos"
_DDL = """
CREATE TABLE IF NOT EXISTS arch_repos (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    slug             TEXT UNIQUE NOT NULL DEFAULT '',
    label            TEXT NOT NULL DEFAULT '',
    url              TEXT NOT NULL DEFAULT '',
    mirror_urls      TEXT NOT NULL DEFAULT '',
    architectures    TEXT NOT NULL DEFAULT 'x86_64',
    enabled          INTEGER NOT NULL DEFAULT 1,
    last_status      TEXT NOT NULL DEFAULT 'neu',
    last_run         TEXT NOT NULL DEFAULT '',
    last_sync_issues TEXT NOT NULL DEFAULT '[]'
)"""

_MIGRATION_ALTER = "ALTER TABLE arch_repos ADD COLUMN mirror_urls TEXT NOT NULL DEFAULT ''"

_COLS = (
    "id",
    "slug",
    "label",
    "url",
    "mirror_urls",
    "architectures",
    "enabled",
    "last_status",
    "last_run",
    "last_sync_issues",
)
_LIST_COLS = frozenset({"mirror_urls"})
_BOOL_COLS = frozenset({"enabled"})

_log = __import__("logging").getLogger(__name__)


def _make_slug(label: str) -> str:
    s = label.lower().strip()
    s = re.sub(r"[^a-z0-9 -]", "", s)
    s = re.sub(r"\s+", "-", s).strip("-")
    return s or "repo"


def _db():
    from astrapi_core.system.db import _conn

    return _conn()


class ArchlinuxRepoStore:
    """SQLite-backed Store mit eigener Tabelle `arch_repos`.

    Interface kompatibel mit YamlStorage/SqliteStorage für crud_blueprint.
    Primärschlüssel ist INTEGER AUTOINCREMENT; slug wird einmalig aus label
    abgeleitet und danach nicht mehr geändert.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._table_ready = False

    # ── Interna ──────────────────────────────────────────────────────────────

    def _ensure_table(self) -> bool:
        if self._table_ready:
            return True
        try:
            db = _db()
            db.execute(_DDL)
            db.commit()
            try:
                db.execute(_MIGRATION_ALTER)
                db.commit()
            except Exception:
                pass  # Spalte existiert bereits
            self._table_ready = True
            return True
        except Exception:
            return False

    def _row_to_dict(self, row) -> dict:
        d = dict(zip(_COLS, row))
        for col in _LIST_COLS:
            raw = d.get(col, "") or ""
            d[col] = [x.strip() for x in raw.split(",") if x.strip()]
        for col in _BOOL_COLS:
            d[col] = bool(d.get(col, 0))
        try:
            d["last_sync_issues"] = json.loads(d.get("last_sync_issues") or "[]")
        except Exception:
            d["last_sync_issues"] = []
        return d

    def _to_db(self, data: dict, include_slug: bool = False) -> dict:
        """Wandelt Python-Dict in DB-Spaltenwerte um (partial updates möglich)."""
        row: dict = {}
        for col in _COLS:
            if col == "id":
                continue
            if col == "slug" and not include_slug:
                continue
            if col not in data:
                continue
            val = data[col]
            if col in _LIST_COLS:
                row[col] = ",".join(val) if isinstance(val, list) else str(val or "")
            elif col in _BOOL_COLS:
                row[col] = 1 if val else 0
            elif col == "last_sync_issues":
                row[col] = json.dumps(val) if not isinstance(val, str) else val
            else:
                row[col] = val
        return row

    def _resolve_id(self, item_id) -> int | None:
        """Gibt die integer DB-ID zurück; akzeptiert int, int-string oder slug."""
        try:
            return int(item_id)
        except (ValueError, TypeError):
            pass
        row = _db().execute(f"SELECT id FROM {_TABLE} WHERE slug=?", (str(item_id),)).fetchone()
        return row[0] if row else None

    # ── Public interface ──────────────────────────────────────────────────────

    def list(
        self,
        filter_fn: "Callable[[str, dict], bool] | None" = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> dict:
        if not self._ensure_table():
            return {}
        with self._lock:
            rows = _db().execute(f"SELECT {','.join(_COLS)} FROM {_TABLE} ORDER BY id").fetchall()
        result = {str(r[0]): self._row_to_dict(r) for r in rows}
        if filter_fn:
            result = {k: v for k, v in result.items() if filter_fn(k, v)}
        return result

    def get(self, item_id) -> dict | None:
        if not self._ensure_table():
            return None
        with self._lock:
            int_id = self._resolve_id(item_id)
            if int_id is None:
                return None
            row = (
                _db()
                .execute(f"SELECT {','.join(_COLS)} FROM {_TABLE} WHERE id=?", (int_id,))
                .fetchone()
            )
        return self._row_to_dict(row) if row else None

    def create(self, item_id, data: dict) -> dict:
        """item_id wird ignoriert – slug wird automatisch aus label generiert."""
        if not self._ensure_table():
            raise RuntimeError("DB nicht verfügbar")
        data = dict(data)
        label = data.get("label", "")
        base_slug = _make_slug(label)
        with self._lock:
            existing = {r[0] for r in _db().execute(f"SELECT slug FROM {_TABLE}").fetchall()}
            slug = base_slug
            if slug in existing:
                i = 2
                while f"{base_slug}-{i}" in existing:
                    i += 1
                slug = f"{base_slug}-{i}"
            row = self._to_db(data)
            row["slug"] = slug
            row.setdefault("last_status", "neu")
            row.setdefault("architectures", "x86_64")
            cols = list(row.keys())
            db = _db()
            cur = db.execute(
                f"INSERT INTO {_TABLE} ({','.join(cols)}) VALUES ({','.join(['?'] * len(cols))})",
                [row[c] for c in cols],
            )
            db.commit()
            new_id = cur.lastrowid
        return self.get(str(new_id))

    def update(self, item_id, data: dict) -> dict:
        """Aktualisiert einen Eintrag; slug wird nie geändert."""
        if not self._ensure_table():
            raise KeyError("DB nicht verfügbar")
        row = self._to_db(dict(data))  # include_slug=False → slug bleibt unverändert
        if not row:
            raise KeyError("Keine Daten")
        with self._lock:
            int_id = self._resolve_id(item_id)
            if int_id is None:
                raise KeyError(f"Nicht gefunden: {item_id}")
            db = _db()
            sets = ", ".join(f"{k}=?" for k in row)
            db.execute(
                f"UPDATE {_TABLE} SET {sets} WHERE id=?",
                [*row.values(), int_id],
            )
            db.commit()
        return self.get(str(int_id))

    def upsert(self, item_id, data: dict) -> None:
        """Partielles Status-Update (von jobs.py); item_id: integer-string, int oder slug."""
        if not self._ensure_table():
            return
        row = self._to_db(data)
        if not row:
            return
        with self._lock:
            int_id = self._resolve_id(item_id)
            if int_id is None:
                return
            db = _db()
            sets = ", ".join(f"{k}=?" for k in row)
            db.execute(
                f"UPDATE {_TABLE} SET {sets} WHERE id=?",
                [*row.values(), int_id],
            )
            db.commit()

    def delete(self, item_id) -> None:
        if not self._ensure_table():
            raise KeyError("DB nicht verfügbar")
        with self._lock:
            int_id = self._resolve_id(item_id)
            if int_id is None:
                raise KeyError(f"Nicht gefunden: {item_id}")
            db = _db()
            db.execute(f"DELETE FROM {_TABLE} WHERE id=?", (int_id,))
            db.commit()

    def toggle(self, item_id) -> bool:
        if not self._ensure_table():
            raise KeyError("DB nicht verfügbar")
        with self._lock:
            int_id = self._resolve_id(item_id)
            if int_id is None:
                raise KeyError(f"Nicht gefunden: {item_id}")
            db = _db()
            cur_val = db.execute(f"SELECT enabled FROM {_TABLE} WHERE id=?", (int_id,)).fetchone()
            new_val = 0 if cur_val[0] else 1
            db.execute(f"UPDATE {_TABLE} SET enabled=? WHERE id=?", (new_val, int_id))
            db.commit()
        return bool(new_val)
