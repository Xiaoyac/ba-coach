"""Read-only descriptions of record writes; never change workflow decisions."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select

from .database_v2_schema import metadata as schema

logger = logging.getLogger(__name__)


def _result(status: str, reason_code: str, record_id=None) -> dict[str, Any]:
    return {"status": status, "reason_code": reason_code, "record_id": record_id}


async def describe_record_write(db, *, module, data, record_id, state) -> dict[str, Any]:
    """Report what was written, without promoting drafts or guessing failures.

    ``record_id`` is the writer's result, not an inferred success from extracted
    data. A missing M2 binding is an observable M3 prerequisite, not a claim
    that all other source/freshness checks passed. Other absent write results
    remain unspecified until the writer can report its actual return reason.
    """
    if record_id is not None:
        return _result("completed", "record_written", record_id)
    if not data:
        return _result("skipped", "no_extracted_data")

    cycle_id = (state.get("active_cycle_id") if isinstance(state, Mapping)
                else getattr(state, "active_cycle_id", None))
    if module != "module_1" and not cycle_id:
        return _result("skipped", "no_active_cycle")
    if module != "module_3":
        return _result("not_written", "write_not_applied")

    try:
        cycles = schema.tables["pa_cycles"]
        # Use the existing transaction's connection to see its current state.
        # A connection savepoint contains diagnostic SQL errors without an ORM
        # begin_nested() flushing pending user data or rolling back its caller.
        connection = await db.connection()
        async with connection.begin_nested():
            cycle = (await connection.execute(select(
                cycles.c.module_two_record_id, cycles.c.status
            ).where(cycles.c.id == cycle_id))).mappings().one_or_none()
        if (cycle and cycle["status"] in {"planning", "waiting_execution"}
                and not cycle["module_two_record_id"]):
            return _result("blocked", "m2_plan_not_bound")
        return _result("not_written", "write_not_applied")
    except Exception as exc:
        # Diagnostics must not fail the main write or leak SQL parameters into
        # logs. Cancellation still propagates (it is not an Exception).
        logger.warning("record write diagnosis unavailable (%s)", type(exc).__name__)
        return _result("unknown", "diagnostic_unavailable")
