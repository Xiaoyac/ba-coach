"""Explicit, write-paused V2 production cutover with pre-opening rollback.

Build and test the release first. Stops public ingress before any mutation,
takes a fresh restored/verified backup, converts a new isolated candidate,
compares all original rows, atomically swaps V2 tables, then verifies the app.
No original table or backup is dropped. Cannot overwrite an existing V2.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.request import urlopen
import pymysql
from sqlalchemy.engine import make_url
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.database_v2_schema import metadata
from backup_database_v2 import digest_rows, identifier

ENV = Path("/etc/bacoach/backend.env")
CURRENT = Path("/opt/bacoach/current")
SOURCE = "ba_coach_260908"


def run_json(command, cwd):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("A migration/verification step failed; production ingress remains protected")
    return json.loads(result.stdout.strip().splitlines()[-1])


def private_write(path, data):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf8") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def link_current(target):
    temporary = Path("/opt/bacoach/.v2-current-pending")
    if temporary.exists() or temporary.is_symlink():
        raise RuntimeError("An unresolved cutover symlink exists")
    temporary.symlink_to(target)
    os.replace(temporary, CURRENT)


def health(url):
    for _ in range(40):
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(1)
    raise RuntimeError("Local service health check failed")


def main(args):
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", args.release_id):
        raise RuntimeError("Invalid release ID")
    release = Path("/opt/bacoach/releases") / args.release_id
    previous = CURRENT.resolve()
    if previous.parent != Path("/opt/bacoach/releases") or not (release / "frontend/.next/BUILD_ID").is_file():
        raise RuntimeError("Expected a prepared release and valid previous release")
    config = Settings(_env_file=ENV)
    url = make_url(config.database_url)
    if url.database != SOURCE or url.host != "rm-wz9m099m6barq69osxo.mysql.rds.aliyuncs.com" or config.database_schema_version != "legacy":
        raise RuntimeError("Expected the verified legacy production configuration")
    connection = pymysql.connect(host=url.host, port=url.port or 3306, user=url.username,
        password=url.password, database=SOURCE, charset="utf8mb4", binary_prefix=True, autocommit=True)
    swapped = False
    paused = False
    reopened = False
    original_env = ENV.read_text()
    rollback_sql = None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW COLUMNS FROM user_profile LIKE 'supporter1_nickname'")
            if not cursor.fetchone():
                raise RuntimeError("Source is not the expected legacy profile")
            cursor.execute("SHOW TABLES")
            source_tables = {row[0] for row in cursor.fetchall()}
            names = list(metadata.tables)
            if set(names) - source_tables != {"user_preferences", "user_supporters", "user_activity_constraints",
                    "user_module_one_state", "pa_goals", "pa_cycle_progress", "ba_memory", "ai_decision_logs", "v2_migration_issues"}:
                raise RuntimeError("Unexpected source schema; refusing a partial/repeated cutover")
            if not args.apply:
                print(json.dumps({"dry_run": True, "release": str(release), "source_tables": len(source_tables),
                                  "replacement_tables": len(names), "maintenance_required": True}))
                return
            subprocess.run(["systemctl", "stop", "nginx"], check=True)
            paused = True
            subprocess.run(["systemctl", "stop", "bacoach-frontend", "bacoach-backend"], check=True)
            python = str(release / "backend/.venv/bin/python")
            cwd = release / "backend"
            backup = run_json([python, "scripts/backup_database_v2.py", "--apply"], cwd)
            if not backup.get("restore_verified"):
                raise RuntimeError("Snapshot restoration did not verify")
            backup_dir = Path(backup["backup_directory"])
            private_write(backup_dir / "backend.env.before", original_env)
            rehearsal = run_json([python, "scripts/rehearse_database_v2.py", "--backup", str(backup_dir), "--apply"], cwd)
            candidate = rehearsal["rehearsal_database"]
            if not re.fullmatch(r"ba_coach_v2_rehearsal_[0-9]{8}t[0-9]{6}z", candidate):
                raise RuntimeError("Unsafe candidate")
            # Every legacy row in the candidate came from the ACTUAL on-disk
            # backup. Detect external writes since the snapshot before swapping.
            for table in sorted(source_tables):
                archived = "legacy_v1_" + table if table in names else table
                cursor.execute(f"SELECT * FROM {identifier(SOURCE)}.{identifier(table)}")
                original = cursor.fetchall()
                cursor.execute(f"SELECT * FROM {identifier(candidate)}.{identifier(archived)}")
                if digest_rows(original) != digest_rows(cursor.fetchall()):
                    raise RuntimeError("Source changed after backup; refusing cutover")
            forward, reverse, archives = [], [], {}
            for name in names:
                if name in source_tables:
                    archive = "legacy_" + stamp.lower() + "_" + name
                    archives[name] = archive
                    forward.append(f"{identifier(SOURCE)}.{identifier(name)} TO {identifier(SOURCE)}.{identifier(archive)}")
                forward.append(f"{identifier(candidate)}.{identifier(name)} TO {identifier(SOURCE)}.{identifier(name)}")
                reverse.append(f"{identifier(SOURCE)}.{identifier(name)} TO {identifier(candidate)}.{identifier(name)}")
            for name, archive in archives.items():
                reverse.append(f"{identifier(SOURCE)}.{identifier(archive)} TO {identifier(SOURCE)}.{identifier(name)}")
            rollback_sql = "RENAME TABLE " + ", ".join(reverse)
            private_write(backup_dir / "rollback.sql", rollback_sql + ";\n")
            private_write(backup_dir / "cutover-plan.json", json.dumps({"previous": str(previous), "release": str(release),
                "candidate": candidate, "archives": archives}, indent=2))
            cursor.execute("RENAME TABLE " + ", ".join(forward))
            swapped = True
            new_env = re.sub(r"(?m)^DATABASE_SCHEMA_VERSION=.*\n?", "", original_env).rstrip() + "\nDATABASE_SCHEMA_VERSION=v2\n"
            pending_env = ENV.with_name("backend.env.v2-pending")
            private_write(pending_env, new_env)
            os.replace(pending_env, ENV)
            link_current(release)
            subprocess.run(["systemctl", "start", "bacoach-backend"], check=True)
            health("http://127.0.0.1:8000/health")
            verification = run_json([python, "scripts/verify_v2_live.py"], cwd)
            if not verification.get("verified"):
                raise RuntimeError("V2 read verification failed")
            subprocess.run(["systemctl", "start", "bacoach-frontend"], check=True)
            health("http://127.0.0.1:3000/")
            # Once ingress opens, NEVER automatically revert data that may
            # already include new user writes. Any later rollback is a new task.
            subprocess.run(["systemctl", "start", "nginx"], check=True)
            reopened = True
            result = {"production_v2_enabled": True, "release": str(release), "backup_directory": str(backup_dir),
                      "legacy_tables_retained": archives, "verification": verification}
            private_write(backup_dir / "release-result.json", json.dumps(result, indent=2))
            print(json.dumps(result))
    except BaseException:
        if paused and not reopened:
            subprocess.run(["systemctl", "stop", "bacoach-backend", "bacoach-frontend"], check=False)
            if swapped:
                with connection.cursor() as cursor:
                    cursor.execute(rollback_sql)
                restore_env = ENV.with_name("backend.env.v2-rollback")
                private_write(restore_env, original_env)
                os.replace(restore_env, ENV)
                link_current(previous)
            subprocess.run(["systemctl", "start", "bacoach-backend", "bacoach-frontend", "nginx"], check=True)
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--apply", action="store_true")
    try:
        main(parser.parse_args())
    except Exception as exc:
        print(json.dumps({"cutover_failed": True, "error_type": type(exc).__name__, "details": "redacted"}))
        sys.exit(1)
