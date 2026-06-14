"""Run backend/db/schema.sql against DATABASE_URL.

Usage:
    cd backend
    .\.venv\Scripts\python.exe scripts\setup_db.py
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv


async def main() -> None:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is empty. Add the Supabase Postgres connection string to backend/.env first.")

    import asyncpg

    schema_path = Path(__file__).resolve().parents[1] / "db" / "schema.sql"
    schema = schema_path.read_text(encoding="utf-8")
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(schema)
    finally:
        await conn.close()
    print(f"Schema applied: {schema_path}")


if __name__ == "__main__":
    asyncio.run(main())
