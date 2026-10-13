"""Tiny SQLite persistence for imported cases and user materials (stdlib only)."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import numpy as np

from .cases import Case, CaseMeta
from .materials import Material, MaterialLibrary, default_library

__all__ = ["Store"]


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS cases (
                    id TEXT PRIMARY KEY, name TEXT, meta TEXT, data TEXT, created TEXT, position INTEGER
                );
                CREATE TABLE IF NOT EXISTS materials (id TEXT PRIMARY KEY, payload TEXT);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
                """
            )
            self._db.commit()

    # ---------------------------------------------------------------- settings
    def get_setting(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, value))
            self._db.commit()

    # ---------------------------------------------------------------- cases
    def add_cases(self, cases: list[Case]) -> None:
        with self._lock:
            pos = self._db.execute("SELECT COALESCE(MAX(position), 0) FROM cases").fetchone()[0]
            for k, c in enumerate(cases):
                self._db.execute(
                    "INSERT OR REPLACE INTO cases(id, name, meta, data, created, position) VALUES (?,?,?,?,?,?)",
                    (c.id, c.name, c.meta.model_dump_json(), json.dumps(c.data), c.created, pos + k + 1),
                )
            self._db.commit()

    def list_cases(self) -> list[Case]:
        with self._lock:
            rows = self._db.execute("SELECT id, name, meta, data, created FROM cases ORDER BY position").fetchall()
        return [self._row(r) for r in rows]

    def get_case(self, case_id: str) -> Case | None:
        with self._lock:
            r = self._db.execute("SELECT id, name, meta, data, created FROM cases WHERE id=?", (case_id,)).fetchone()
        return self._row(r) if r else None

    def update_case(self, case_id: str, name: str | None = None, meta: CaseMeta | None = None) -> Case | None:
        c = self.get_case(case_id)
        if c is None:
            return None
        if name is not None:
            c.name = name
        if meta is not None:
            c.meta = meta
        with self._lock:
            self._db.execute("UPDATE cases SET name=?, meta=? WHERE id=?", (c.name, c.meta.model_dump_json(), case_id))
            self._db.commit()
        return c

    def delete_case(self, case_id: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM cases WHERE id=?", (case_id,))
            self._db.commit()
        return cur.rowcount > 0

    @staticmethod
    def _row(r) -> Case:
        return Case(id=r[0], name=r[1], meta=CaseMeta.model_validate_json(r[2]), data=json.loads(r[3]), created=r[4])

    # ---------------------------------------------------------------- materials
    def add_material(self, m: Material) -> None:
        payload = {
            "id": m.id,
            "name": m.name,
            "kind": m.kind,
            "family": m.family,
            "t_k": m.t_k.tolist(),
            "alpha": m.alpha.tolist(),
            "rho": m.rho.tolist(),
            "kappa": m.kappa.tolist(),
            "t_limit_k": m.t_limit_k,
            "density_g_cm3": m.density_g_cm3,
            "notes": m.notes,
            "source": m.source,
        }
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO materials(id, payload) VALUES (?, ?)", (m.id, json.dumps(payload)))
            self._db.commit()

    def delete_material(self, mat_id: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM materials WHERE id=?", (mat_id,))
            self._db.commit()
        return cur.rowcount > 0

    def custom_materials(self) -> list[Material]:
        with self._lock:
            rows = self._db.execute("SELECT payload FROM materials").fetchall()
        out = []
        for (p,) in rows:
            d = json.loads(p)
            out.append(
                Material(
                    id=d["id"],
                    name=d["name"],
                    kind=d["kind"],
                    family=d["family"],
                    t_k=np.array(d["t_k"]),
                    alpha=np.array(d["alpha"]),
                    rho=np.array(d["rho"]),
                    kappa=np.array(d["kappa"]),
                    t_limit_k=d["t_limit_k"],
                    density_g_cm3=d["density_g_cm3"],
                    notes=d.get("notes", ""),
                    source=d.get("source", "user"),
                )
            )
        return out

    def library(self) -> MaterialLibrary:
        lib = default_library()
        for m in self.custom_materials():
            lib.add(m)
        return lib
