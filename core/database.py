"""
Capa de persistencia SQLite para Job Sprout.

Schema:
- perfiles:     busquedas guardadas (ej. "Esposa - Nomina", "Jesus - Full Stack")
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from config.settings import DB_PATH

SCHEMA_BASE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS perfiles (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre                TEXT    NOT NULL UNIQUE,
        descripcion           TEXT    NOT NULL DEFAULT '',
        activo                INTEGER NOT NULL DEFAULT 1,
        ubicaciones_aceptadas TEXT    NOT NULL DEFAULT 'Mosquera,Madrid,Funza',
        acepta_remoto         INTEGER NOT NULL DEFAULT 1,
        created_at            TEXT    NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS keywords (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        perfil_id  INTEGER NOT NULL,
        keyword    TEXT    NOT NULL,
        FOREIGN KEY (perfil_id) REFERENCES perfiles(id) ON DELETE CASCADE,
        UNIQUE (perfil_id, keyword)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS ejecuciones (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at  TEXT    NOT NULL,
        finished_at TEXT    NOT NULL DEFAULT '',
        status      TEXT    NOT NULL DEFAULT 'running',
        vacantes_n  INTEGER NOT NULL DEFAULT 0
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_keywords_perfil ON keywords(perfil_id);",
)


SCHEMA_VACANTES_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS vacantes (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        perfil_id         INTEGER,
        portal            TEXT    NOT NULL,
        external_id       TEXT    NOT NULL DEFAULT '',
        titulo            TEXT    NOT NULL,
        empresa           TEXT    NOT NULL DEFAULT '',
        ubicacion         TEXT    NOT NULL DEFAULT '',
        salario           TEXT    NOT NULL DEFAULT '',
        descripcion       TEXT    NOT NULL DEFAULT '',
        url               TEXT    NOT NULL,
        fecha_publicacion TEXT    NOT NULL DEFAULT '',
        bilingue          INTEGER NOT NULL DEFAULT 0,
        aprobado          INTEGER NOT NULL DEFAULT 1,
        relevance_score   REAL,
        geo_validated     INTEGER,
        ai_model          TEXT    NOT NULL DEFAULT '',
        ai_checked_at     TEXT    NOT NULL DEFAULT '',
        ai_reason         TEXT    NOT NULL DEFAULT '',
        created_at        TEXT    NOT NULL,
        UNIQUE (portal, external_id, url),
        FOREIGN KEY (perfil_id) REFERENCES perfiles(id) ON DELETE SET NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_vacantes_perfil ON vacantes(perfil_id);",
    "CREATE INDEX IF NOT EXISTS idx_vacantes_aprobado ON vacantes(aprobado);",
    "CREATE INDEX IF NOT EXISTS idx_vacantes_relevance ON vacantes(relevance_score);",
)


SCHEMA_STATEMENTS: tuple[str, ...] = SCHEMA_BASE_STATEMENTS + SCHEMA_VACANTES_STATEMENTS


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: Path | None = None) -> None:
    conn = get_connection(db_path)
    try:
        for stmt in SCHEMA_BASE_STATEMENTS:
            conn.execute(stmt)
        _migrate_perfiles(conn)
        for stmt in SCHEMA_VACANTES_STATEMENTS:
            conn.execute(stmt)
        _migrate_vacantes(conn)
        _seed_default_settings(conn)
        conn.commit()
    finally:
        conn.close()


def _seed_default_settings(conn: sqlite3.Connection) -> None:
    defaults = {
        "use_ai_scoring": "0",
        "ai_relevance_threshold": "0.4",
        "ai_validate_geo": "1",
        "ai_provider": "gemini",
        "ai_model": "gemini-2.5-flash",
        "ai_api_key_override": "",
        "linkedin_email": "",
        "linkedin_max_per_session": "20",
        "browser_engine": "chromium",
        "browser_path": "",
        "browser_mode": "ephemeral",
        "browser_profile_dir": "data/browser_profile",
    }
    for k, v in defaults.items():
        existing = conn.execute(
            "SELECT 1 FROM settings WHERE key = ?", (k,)
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                (k, v, now_iso()),
            )


def _migrate_perfiles(conn: sqlite3.Connection) -> None:
    """Anade columnas nuevas a `perfiles` en DBs existentes (no rompe schema)."""
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(perfiles)").fetchall()
    }
    if "ubicaciones_aceptadas" not in cols:
        conn.execute(
            "ALTER TABLE perfiles ADD COLUMN ubicaciones_aceptadas "
            "TEXT NOT NULL DEFAULT 'Mosquera,Madrid,Funza'"
        )
    if "acepta_remoto" not in cols:
        conn.execute(
            "ALTER TABLE perfiles ADD COLUMN acepta_remoto "
            "INTEGER NOT NULL DEFAULT 1"
        )


def _migrate_vacantes(conn: sqlite3.Connection) -> None:
    """Anade columnas AI a `vacantes` en DBs existentes."""
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(vacantes)").fetchall()
    }
    if "relevance_score" not in cols:
        conn.execute("ALTER TABLE vacantes ADD COLUMN relevance_score REAL")
    if "geo_validated" not in cols:
        conn.execute("ALTER TABLE vacantes ADD COLUMN geo_validated INTEGER")
    if "ai_model" not in cols:
        conn.execute(
            "ALTER TABLE vacantes ADD COLUMN ai_model "
            "TEXT NOT NULL DEFAULT ''"
        )
    if "ai_checked_at" not in cols:
        conn.execute(
            "ALTER TABLE vacantes ADD COLUMN ai_checked_at "
            "TEXT NOT NULL DEFAULT ''"
        )
    if "ai_reason" not in cols:
        conn.execute(
            "ALTER TABLE vacantes ADD COLUMN ai_reason "
            "TEXT NOT NULL DEFAULT ''"
        )


@contextmanager
def transaction(db_path: Path | None = None):
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def upsert_perfil(
    nombre: str,
    descripcion: str = "",
    keywords: Iterable[str] = (),
    *,
    ubicaciones_aceptadas: str = "Mosquera,Madrid,Funza",
    acepta_remoto: bool = True,
) -> int:
    """Crea o actualiza un perfil y reemplaza sus keywords. Retorna el id."""
    with transaction() as conn:
        existing = conn.execute(
            "SELECT id FROM perfiles WHERE nombre = ?", (nombre,)
        ).fetchone()
        if existing is None:
            cur = conn.execute(
                "INSERT INTO perfiles (nombre, descripcion, activo, "
                "ubicaciones_aceptadas, acepta_remoto, created_at) "
                "VALUES (?, ?, 1, ?, ?, ?)",
                (
                    nombre,
                    descripcion,
                    ubicaciones_aceptadas or "Mosquera,Madrid,Funza",
                    1 if acepta_remoto else 0,
                    now_iso(),
                ),
            )
            perfil_id = cur.lastrowid
        else:
            perfil_id = existing["id"]
            conn.execute(
                "UPDATE perfiles SET descripcion = ?, activo = 1, "
                "ubicaciones_aceptadas = ?, acepta_remoto = ? WHERE id = ?",
                (
                    descripcion,
                    ubicaciones_aceptadas or "Mosquera,Madrid,Funza",
                    1 if acepta_remoto else 0,
                    perfil_id,
                ),
            )

        conn.execute("DELETE FROM keywords WHERE perfil_id = ?", (perfil_id,))
        for kw in keywords:
            kw_clean = kw.strip()
            if not kw_clean:
                continue
            conn.execute(
                "INSERT INTO keywords (perfil_id, keyword) VALUES (?, ?)",
                (perfil_id, kw_clean),
            )
    return perfil_id


def list_perfiles(only_active: bool = False) -> list[dict[str, Any]]:
    with transaction() as conn:
        sql = "SELECT * FROM perfiles"
        if only_active:
            sql += " WHERE activo = 1"
        sql += " ORDER BY nombre"
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def get_keywords_for_perfil(perfil_id: int) -> list[str]:
    with transaction() as conn:
        rows = conn.execute(
            "SELECT keyword FROM keywords WHERE perfil_id = ? ORDER BY keyword",
            (perfil_id,),
        ).fetchall()
    return [r["keyword"] for r in rows]


def get_all_perfiles_with_keywords(
    only_active: bool = True,
) -> list[dict[str, Any]]:
    perfiles = list_perfiles(only_active=only_active)
    for p in perfiles:
        p["keywords"] = get_keywords_for_perfil(p["id"])
    return perfiles


def vacante_exists(
    portal: str, external_id: str, url: str
) -> bool:
    with transaction() as conn:
        row = conn.execute(
            "SELECT 1 FROM vacantes "
            "WHERE portal = ? AND external_id = ? AND url = ? LIMIT 1",
            (portal, external_id, url),
        ).fetchone()
    return row is not None


def insert_vacante(
    *,
    portal: str,
    titulo: str,
    url: str,
    perfil_id: int | None = None,
    external_id: str = "",
    empresa: str = "",
    ubicacion: str = "",
    salario: str = "",
    descripcion: str = "",
    fecha_publicacion: str = "",
    bilingue: bool = False,
    aprobado: bool = True,
    relevance_score: float | None = None,
    geo_validated: bool | None = None,
    ai_model: str = "",
    ai_checked_at: str = "",
    ai_reason: str = "",
) -> int | None:
    """Inserta una vacante. Retorna el id nuevo, o None si ya existia (duplicado)."""
    with transaction() as conn:
        if vacante_exists(portal, external_id, url):
            return None
        cur = conn.execute(
            """
            INSERT INTO vacantes (
                perfil_id, portal, external_id, titulo, empresa,
                ubicacion, salario, descripcion, url, fecha_publicacion,
                bilingue, aprobado,
                relevance_score, geo_validated, ai_model, ai_checked_at, ai_reason,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                perfil_id,
                portal,
                external_id,
                titulo,
                empresa,
                ubicacion,
                salario,
                descripcion,
                url,
                fecha_publicacion,
                1 if bilingue else 0,
                1 if aprobado else 0,
                relevance_score,
                1 if geo_validated is True else (0 if geo_validated is False else None),
                ai_model,
                ai_checked_at,
                ai_reason,
                now_iso(),
            ),
        )
        return cur.lastrowid


def list_vacantes(
    *,
    perfil_id: int | None = None,
    aprobado: bool | None = None,
    bilingue: bool | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if perfil_id is not None:
        clauses.append("perfil_id = ?")
        params.append(perfil_id)
    if aprobado is not None:
        clauses.append("aprobado = ?")
        params.append(1 if aprobado else 0)
    if bilingue is not None:
        clauses.append("bilingue = ?")
        params.append(1 if bilingue else 0)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    sql = (
        "SELECT v.*, p.nombre AS perfil_nombre "
        "FROM vacantes v LEFT JOIN perfiles p ON p.id = v.perfil_id"
        f"{where} ORDER BY v.created_at DESC LIMIT ?"
    )
    with transaction() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def count_vacantes() -> int:
    with transaction() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM vacantes").fetchone()["n"]


def delete_perfil(nombre: str) -> bool:
    with transaction() as conn:
        cur = conn.execute("DELETE FROM perfiles WHERE nombre = ?", (nombre,))
        return cur.rowcount > 0


def delete_vacantes(ids: Iterable[int]) -> int:
    """Elimina vacantes por id. Retorna el numero eliminado."""
    id_list = [int(i) for i in ids]
    if not id_list:
        return 0
    placeholders = ",".join("?" * len(id_list))
    with transaction() as conn:
        cur = conn.execute(
            f"DELETE FROM vacantes WHERE id IN ({placeholders})", id_list
        )
        return cur.rowcount


def get_setting(key: str, default: str | None = None) -> str | None:
    with transaction() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with transaction() as conn:
        existing = conn.execute(
            "SELECT 1 FROM settings WHERE key = ?", (key,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE settings SET value = ?, updated_at = ? WHERE key = ?",
                (value, now_iso(), key),
            )
        else:
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, now_iso()),
            )


def get_all_settings() -> dict[str, str]:
    try:
        with transaction() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
    except Exception:
        init_db()
        with transaction() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


if __name__ == "__main__":
    init_db()
    print(f"DB inicializada en: {DB_PATH}")
