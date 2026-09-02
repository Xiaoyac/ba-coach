"""Idempotently convert only legacy 0–5 approval values outside live 0–2.

Valid 0/1/2 values are untouched.  Legacy 3 maps to partial (1), while 4/5
map to accepted/understood (2).  This script never edits risk or execution
codes, whose meanings cannot be safely inferred by arithmetic.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import dispose_db, get_engine  # noqa: E402


TARGETS = (
    ("module_one_record", "user_approval_level"),
    ("module_two_record", "pa_understanding_level"),
    ("module_two_record", "pa_approval_level"),
    ("module_three_record", "user_acceptance_level"),
    ("module_four_record", "user_chain_approval_level"),
)


async def main() -> None:
    engine = get_engine()
    async with engine.begin() as connection:
        for table, column in TARGETS:
            result = await connection.execute(
                text(
                    f"UPDATE {table} SET {column} = CASE "
                    f"WHEN {column} = 3 THEN 1 WHEN {column} IN (4, 5) THEN 2 "
                    f"ELSE {column} END WHERE {column} BETWEEN 3 AND 5"
                )
            )
            print(f"{table}.{column}: updated={result.rowcount}")
    await dispose_db()


if __name__ == "__main__":
    asyncio.run(main())
