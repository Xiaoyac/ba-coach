"""Read-only aggregate audit for clinical code compatibility."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import dispose_db, get_engine  # noqa: E402


QUERIES = {
    "risk_expression_type": "SELECT risk_expression_type AS code, COUNT(*) AS n FROM risk_monitoring GROUP BY risk_expression_type ORDER BY risk_expression_type",
    "module_one_approval": "SELECT user_approval_level AS code, COUNT(*) AS n FROM module_one_record GROUP BY user_approval_level ORDER BY user_approval_level",
    "module_two_understanding": "SELECT pa_understanding_level AS code, COUNT(*) AS n FROM module_two_record GROUP BY pa_understanding_level ORDER BY pa_understanding_level",
    "module_two_approval": "SELECT pa_approval_level AS code, COUNT(*) AS n FROM module_two_record GROUP BY pa_approval_level ORDER BY pa_approval_level",
    "module_three_acceptance": "SELECT user_acceptance_level AS code, COUNT(*) AS n FROM module_three_record GROUP BY user_acceptance_level ORDER BY user_acceptance_level",
    "module_four_chain_approval": "SELECT user_chain_approval_level AS code, COUNT(*) AS n FROM module_four_record GROUP BY user_chain_approval_level ORDER BY user_chain_approval_level",
    "module_four_execution": "SELECT execution_result AS code, COUNT(*) AS n FROM module_four_record GROUP BY execution_result ORDER BY execution_result",
    "module_four_review": "SELECT review_decision AS code, COUNT(*) AS n FROM module_four_record GROUP BY review_decision ORDER BY review_decision",
}


async def main() -> None:
    engine = get_engine()
    async with engine.connect() as connection:
        for label, sql in QUERIES.items():
            rows = (await connection.execute(text(sql))).mappings().all()
            print(f"{label}: " + (", ".join(f"{row['code']}={row['n']}" for row in rows) or "empty"))
    await dispose_db()


if __name__ == "__main__":
    asyncio.run(main())
