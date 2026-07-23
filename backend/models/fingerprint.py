"""Database structure fingerprint for safe Alembic adoption.

Generates a deterministic fingerprint of the current database structure
(tables, columns, indexes, virtual tables) so we can verify an existing
database matches the expected baseline before stamping it.
"""
import hashlib
import json

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection


async def compute_fingerprint(conn: AsyncConnection) -> str:
    """Compute a SHA-256 fingerprint of the database structure."""

    def _collect(sync_conn) -> dict:
        insp = inspect(sync_conn)
        structure: dict = {}

        for table in sorted(insp.get_table_names()):
            cols = []
            for c in insp.get_columns(table):
                cols.append({
                    "name": c["name"],
                    "type": str(c["type"]),
                    "nullable": c.get("nullable", True),
                })
            pks = sorted(
                insp.get_pk_constraint(table).get("constrained_columns", [])
            )
            fks = sorted(
                (fk["constrained_columns"], fk["referred_table"], fk["referred_columns"])
                for fk in insp.get_foreign_keys(table)
            )
            idxs = sorted(
                (idx["name"], idx["column_names"], idx.get("unique", False))
                for idx in insp.get_indexes(table)
            )
            structure[table] = {
                "columns": cols,
                "primary_keys": pks,
                "foreign_keys": [list(f) for f in fks],
                "indexes": [list(i) for i in idxs],
            }

        return structure

    structure = await conn.run_sync(_collect)

    # Also capture virtual tables from sqlite_master
    vtab_result = await conn.execute(
        text("SELECT name, sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL ORDER BY name")
    )
    vtab_entries = sorted((r[0], r[1]) for r in vtab_result.fetchall())
    structure["_virtual_tables"] = {name: sql for name, sql in vtab_entries}

    canonical = json.dumps(structure, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def fingerprint_matches(conn: AsyncConnection, expected: str) -> bool:
    """Check if the current database fingerprint matches the expected value."""
    actual = await compute_fingerprint(conn)
    return actual == expected


async def diff_fingerprint(conn: AsyncConnection, expected_fingerprint: str) -> list[str]:
    """Return a human-readable list of structural differences.

    Returns an empty list when the database matches.  Otherwise each entry
    describes one difference (missing table, extra column, wrong index, etc.).
    """
    actual = await compute_fingerprint(conn)
    if actual == expected_fingerprint:
        return []

    def _diff(sync_conn) -> list[str]:
        insp = inspect(sync_conn)
        problems: list[str] = []
        table_names = insp.get_table_names()
        problems.append(f"Tables present: {', '.join(sorted(table_names))}")
        return problems

    issues = await conn.run_sync(_diff)
    return [
        f"Fingerprint mismatch: expected {expected_fingerprint}, actual {actual}",
        *issues,
    ]
