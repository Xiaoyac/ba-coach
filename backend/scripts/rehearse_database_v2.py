"""Rehearse V2 conversion in a fresh, isolated database from a verified dump.

Never accepts the production database as a write target. Legacy tables remain
as read-only migration evidence. No service or environment is switched.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid

import pymysql
from sqlalchemy import create_engine, insert, text
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings
try:
    from app.database_v2_schema import metadata
except ModuleNotFoundError:
    from database_v2_schema import metadata


def parse_json(value, default=None):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return value if value is not None else default


def uid(kind, source):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"bacoach:v2:{kind}:{source}"))


def choices(value):
    if isinstance(value, (set, list, tuple)):
        return sorted(value)
    return [v for v in str(value or "").split(",") if v]


def convert(connection, legacy):
    def rows(name):
        name = legacy.get(name, name)
        return list(connection.execute(text(f"SELECT * FROM `{name}`")).mappings())

    def put(name, values):
        connection.execute(insert(metadata.tables[name]), values)

    issues = []

    def issue(table, key, code, *, resolution=None):
        values = {"source_table": table, "source_id": str(key), "issue_code": code,
                  "resolution": resolution, "status": "resolved" if resolution else "pending"}
        put("v2_migration_issues", values)
        issues.append(code)

    extensions = {r["profile_uuid"]: r for r in rows("profile_extensions")}
    users = {r["uuid"]: r for r in rows("user_profile")}
    for key, row in users.items():
        put("user_profile", {"uuid": key, "nickname": row["nickname"],
            "reported_age": row.get("age"),
            "living_status": row["living_status"], "module1_done_flag": bool(row["module1_done_flag"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"]})
        # Do not turn a possibly stale age into a fabricated birth year.
        if row.get("age") is not None:
            issue("user_profile", key, "legacy_age_retained_birth_year_unknown")
        ext = extensions.get(key, {})
        put("user_preferences", {"user_id": key,
            "communication_style": row["communication_preference"],
            "activity_environment": row["activity_environment"], "activity_social": row["activity_social"],
            "activity_atmosphere": row["activity_intensity"],
            "reminder_frequency": row["reminder_frequency"], "reminder_time": ext.get("reminder_time"),
            "reminder_time_slot": row.get("reminder_time_slot"),
            "reminder_start_minute": ext.get("reminder_start_minute"),
            "reminder_end_minute": ext.get("reminder_end_minute")})
        supporters = parse_json(ext.get("supporters"))
        if supporters is None:
            supporters = [{"relation": row.get(f"supporter{i}_relation"),
                           "nickname": row.get(f"supporter{i}_nickname"),
                           "influence": row.get(f"supporter{i}_influence")} for i in (1, 2)
                          if row.get(f"supporter{i}_relation") or row.get(f"supporter{i}_nickname")]
        if not isinstance(supporters, list):
            raise RuntimeError("Supporter source is not an array")
        for i, supporter in enumerate(supporters):
            if not isinstance(supporter, dict):
                raise RuntimeError("Invalid supporter item")
            put("user_supporters", {"id": uid("supporter", f"{key}:{i}"), "user_id": key,
                "position": i, **{field: supporter.get(field) for field in ("relation", "nickname", "influence")}})
        for field, category, kind in (("physical_condition", "身体", "safety_limit"),
                ("behavior_taboo", "行为边界", "user_boundary"), ("content_taboo", "话题边界", "user_boundary")):
            for item in choices(row.get(field)):
                put("user_activity_constraints", {"id": uid("constraint", f"{key}:{field}:{item}"),
                    "user_id": key, "constraint_type": kind, "category": category, "content": item,
                    "source_type": "imported", "confirmation_status": "unconfirmed"})
        # Product decision 2026-09-10: preserve completion without re-questioning
        # the user. This is imported progress, NOT a fabricated confirmation.
        put("user_module_one_state", {"user_id": key, "completed_steps": [],
            "status": "completed" if row["module1_done_flag"] else "in_progress",
            "completion_source": "legacy_imported" if row["module1_done_flag"] else "none",
            "evidence_status": "missing"})
        if row["module1_done_flag"]:
            issue("user_profile", key, "m1_legacy_completion_preserved",
                  resolution="Product approval 2026-09-10: reuse imported completion; no new confirmation required; historical evidence remains missing.")

    versions = {}
    for row in sorted(rows("module_one_record"), key=lambda r: (str(r["created_at"]), r["id"])):
        user = row["user_id"]
        if user not in users:
            issue("module_one_record", row["id"], "owner_missing_archived_only")
            continue
        versions[user] = versions.get(user, 0) + 1
        values = {k: row.get(k) for k in ("id", "user_id", "chief_complaint", "distress_duration",
                  "distress_frequency", "trigger_situation", "coping_behavior", "coping_consequence",
                  "created_at", "updated_at")}
        values.update(version_no=versions[user], record_status="draft",
                      functional_chain_summary=row.get("ai_depression_cycle_summary"))
        for target, source in (("event_experience", "abc_event"),
                              ("attempted_relief_methods", "attempted_relief_methods"),
                              ("exception_positive_scene", "exception_positive_scene")):
            values[target] = parse_json(row.get(source))
        put("module_one_record", values)
        issue("module_one_record", row["id"], "imported_draft_without_confirmation_message")

    # Historical cycles and unlinked cards get DISTINCT draft goals. Combining
    # them by user/latest timestamp would invent relationships never recorded.
    cycle_goals = {}
    cycles_by_conversation = {}
    for row in rows("pa_cycles"):
        user = row["subject_id"]
        if user not in users:
            issue("pa_cycles", row["id"], "owner_missing_archived_only")
            continue
        goal = uid("legacy-cycle-goal", row["id"])
        cycle_goals[row["id"]] = goal
        cycles_by_conversation.setdefault(row["conversation_id"], []).append(row["id"])
        put("pa_goals", {"id": goal, "user_id": user, "title": "历史周期（待核对目标）",
                         "created_from_conversation_id": row["conversation_id"]})
        put("pa_cycles", {"id": row["id"], "goal_id": goal, "ordinal": 1,
            "status": "planning", "started_from_conversation_id": row["conversation_id"],
            "started_at": row["started_at"]})
        put("pa_cycle_progress", {"cycle_id": row["id"], "module_2_steps": [],
                                 "module_3_steps": [], "module_4_steps": []})
        issue("pa_cycles", row["id"], "legacy_cycle_plan_binding_requires_review")
    for row in rows("module_two_record"):
        user = row["user_id"]
        if user not in users:
            issue("module_two_record", row["id"], "owner_missing_archived_only")
            continue
        goal = uid("unlinked-card-goal", row["id"])
        put("pa_goals", {"id": goal, "user_id": user,
                         "title": (row.get("target_activity_content") or "历史计划（待核对）")[:255]})
        put("module_two_record", {"id": row["id"], "goal_id": goal, "version_no": 1,
            "timezone": "Asia/Shanghai", "activity_content": row.get("target_activity_content"),
            "scheduled_start_at": row.get("target_activity_time"),
            "schedule_text": str(row["target_activity_time"]) if row.get("target_activity_time") else None,
            "location": row.get("target_activity_location"),
            "duration_minutes": row.get("target_activity_duration_minutes"),
            "companion": row.get("target_activity_companion"),
            "core_values": [row["core_values"]] if row.get("core_values") else None,
            "core_values_impact": row.get("core_values_impact"),
            "potential_barriers": parse_json(row.get("potential_barriers")),
            "barrier_coping_plan": parse_json(row.get("barrier_coping_plan")),
            "created_at": row["created_at"], "updated_at": row["updated_at"]})
        issue("module_two_record", row["id"], "unlinked_card_imported_as_separate_draft_goal")
    for name in ("module_three_record", "module_four_record"):
        for row in rows(name):
            issue(name, row["id"], "unmapped_record_retained_in_legacy_archive")

    conversations = {r["id"]: r for r in rows("conversations")}
    imported_runtime_ids = set()
    for row in rows("conversation_runtime_states"):
        imported_runtime_ids.add(row["conversation_id"])
        module = row.get("module") or "module_1"
        if module not in {"module_1", "module_2", "module_3", "module_4"}:
            raise RuntimeError("Unexpected legacy runtime module")
        # Keep existing runtime memory intact. No inference that the most recent
        # goal is the goal the user explicitly selected in this conversation.
        put("conversation_runtime_states", {"conversation_id": row["conversation_id"],
            "current_module": module, "memory": parse_json(row["memory"], {}),
            "flow_status": "paused" if module != "module_1" else "active",
            "last_transition_reason": "legacy_import_requires_explicit_goal_selection" if module != "module_1" else None,
            "updated_at": row["updated_at"]})
        if module != "module_1":
            issue("conversation_runtime_states", row["conversation_id"], "explicit_goal_selection_required")
    for conversation_id, conversation in conversations.items():
        if conversation_id in imported_runtime_ids:
            continue
        completed = bool(users.get(conversation["subject_id"], {}).get("module1_done_flag"))
        put("conversation_runtime_states", {"conversation_id": conversation_id,
            "current_module": "module_2" if completed else "module_1", "memory": {},
            "last_transition_reason": "legacy_missing_runtime_initialized"})
    return {code: issues.count(code) for code in sorted(set(issues))}


def main(args):
    source_url = make_url(Settings(_env_file=args.env_file).database_url)
    if source_url.database != "ba_coach_260908":
        raise RuntimeError("Unexpected source database")
    backup = Path(args.backup).resolve()
    if backup.parent != Path("/opt/bacoach/backups") or not backup.name.startswith("before-full-v2-"):
        raise RuntimeError("Expected a verified private pre-V2 backup")
    report = json.loads((backup / "verification.json").read_text())
    dump = (backup / "source.sql").read_bytes()
    if report.get("restore_verified") is not True or hashlib.sha256(dump).hexdigest() != report["sha256"]:
        raise RuntimeError("Backup verification missing or digest mismatch")
    if not args.apply:
        print(json.dumps({"dry_run": True, "v2_tables": len(metadata.tables), "production_changed": False}))
        return
    target = "ba_coach_v2_rehearsal_" + datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz")
    if not re.fullmatch(r"ba_coach_v2_rehearsal_[0-9]{8}t[0-9]{6}z", target):
        raise RuntimeError("Unsafe rehearsal target")
    client = pymysql.connect(host=source_url.host, port=source_url.port or 3306,
        user=source_url.username, password=source_url.password, charset="utf8mb4", autocommit=True,
        client_flag=pymysql.constants.CLIENT.MULTI_STATEMENTS, binary_prefix=True)
    try:
        with client.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{target}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            client.select_db(target)
            cursor.execute(dump)
            while cursor.nextset():
                pass
            cursor.execute("SHOW TABLES")
            present = {row[0] for row in cursor.fetchall()}
            legacy = {name: "legacy_v1_" + name for name in metadata.tables if name in present}
            # A single atomic rename; old cycle foreign keys follow their archived table.
            renames = ", ".join(f"`{a}` TO `{b}`" for a, b in legacy.items())
            cursor.execute("RENAME TABLE " + renames)
        engine = create_engine(source_url.set(drivername="mysql+pymysql", database=target),
                               connect_args={"init_command": "SET time_zone = '+00:00'"})
        try:
            metadata.create_all(engine)
            with engine.begin() as connection:
                issues = convert(connection, legacy)
            with engine.connect() as connection:
                counts = {name: connection.execute(text(f"SELECT COUNT(*) FROM `{name}`")).scalar_one()
                          for name in metadata.tables}
                legacy_completed = connection.execute(text(
                    "SELECT COUNT(*) FROM user_module_one_state WHERE status='completed' "
                    "AND completion_source='legacy_imported' AND evidence_status='missing' "
                    "AND confirmed_formulation_id IS NULL")).scalar_one()
            result = {"rehearsal_database": target, "schema_created": True,
                      "conversion_committed": True, "v2_counts": counts, "migration_issues": issues,
                      "legacy_m1_completion_preserved": legacy_completed,
                      "production_changed": False, "runtime_cutover_ready": False,
                      "note": "Runtime/UI migration and unresolved historical associations still require verification."}
            print(json.dumps(result))
        finally:
            engine.dispose()
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default="/etc/bacoach/backend.env")
    parser.add_argument("--backup", required=True)
    parser.add_argument("--apply", action="store_true")
    try:
        main(parser.parse_args())
    except Exception as exc:
        print(json.dumps({"conversion_committed": False, "error_type": type(exc).__name__, "details": "redacted"}))
        sys.exit(1)
