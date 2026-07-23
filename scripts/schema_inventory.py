"""Print the current SQLite schema as a JSON inventory for review.
Run against a real database: python scripts/schema_inventory.py data/rag_agent.db
"""
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine


async def inventory(db_path: str) -> dict:
    url = f"sqlite+aiosqlite:///{Path(db_path).resolve()}"
    engine = create_async_engine(url, echo=False)
    tables = {}

    async with engine.begin() as conn:
        # ORM tables via inspector
        def sync_inspect(sync_conn):
            insp = inspect(sync_conn)
            result = {}
            for table in insp.get_table_names():
                cols = []
                for c in insp.get_columns(table):
                    cols.append({
                        "name": c["name"],
                        "type": str(c["type"]),
                        "nullable": c.get("nullable", True),
                        "default": str(c.get("default")) if c.get("default") is not None else None,
                    })
                pks = list(insp.get_pk_constraint(table).get("constrained_columns", []))
                fks = [
                    {
                        "cols": fk["constrained_columns"],
                        "ref_table": fk["referred_table"],
                        "ref_cols": fk["referred_columns"],
                    }
                    for fk in insp.get_foreign_keys(table)
                ]
                idxs = [
                    {"name": idx["name"], "cols": idx["column_names"], "unique": idx.get("unique", False)}
                    for idx in insp.get_indexes(table)
                ]
                result[table] = {"columns": cols, "primary_keys": pks, "foreign_keys": fks, "indexes": idxs}
            return result

        tables = await conn.run_sync(sync_inspect)

        # Virtual tables (FTS5, etc.) via sqlite_master
        vtab_result = await conn.execute(
            text("SELECT name, sql FROM sqlite_master WHERE type='table' AND sql LIKE '%VIRTUAL%'")
        )
        virtual_tables = {}
        for row in vtab_result.fetchall():
            virtual_tables[row[0]] = row[1]

        # All sqlite_master entries for full coverage
        all_result = await conn.execute(
            text("SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name")
        )
        all_objects = [[r[0], r[1], r[2]] for r in all_result.fetchall()]

    await engine.dispose()
    return {
        "orm_tables": tables,
        "virtual_tables": virtual_tables,
        "all_sqlite_master": all_objects,
    }


async def main():
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <path/to/database.db>", file=sys.stderr)
        sys.exit(1)
    result = await inventory(sys.argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
