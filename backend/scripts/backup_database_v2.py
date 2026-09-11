"""Create and restore-verify a private, consistent pre-V2 snapshot.

No source writes, service restarts, environment changes, or cutover. The restore
schema is retained for recovery. Only aggregate verification results are printed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys

import pymysql
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings


def digest_rows(rows):
    hashes = sorted(hashlib.sha256(repr(tuple(row)).encode()).digest() for row in rows)
    return hashlib.sha256(b"".join(hashes)).hexdigest()


def identifier(value):
    if not re.fullmatch(r"[a-zA-Z0-9_]+", value):
        raise ValueError("Unsafe identifier")
    return "`" + value + "`"


def main(args):
    url = make_url(Settings(_env_file=args.env_file).database_url)
    if url.database != "ba_coach_260908" or url.get_backend_name() != "mysql":
        raise RuntimeError("Expected ba_coach_260908 MySQL database")
    if url.host != "rm-wz9m099m6barq69osxo.mysql.rds.aliyuncs.com":
        raise RuntimeError("Unexpected database host")
    connection = pymysql.connect(host=url.host, port=url.port or 3306,
        user=url.username, password=url.password, database=url.database,
        charset="utf8mb4", binary_prefix=True, autocommit=True, connect_timeout=15)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT TABLE_NAME,ENGINE FROM information_schema.TABLES "
                           "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME")
            table_info = cursor.fetchall()
            if not table_info or any(engine != "InnoDB" for _, engine in table_info):
                raise RuntimeError("Consistent snapshot requires InnoDB tables")
            tables = [name for name, _ in table_info]
            for name in tables:
                identifier(name)
            for catalog, field in (("VIEWS", "TABLE_SCHEMA"), ("TRIGGERS", "TRIGGER_SCHEMA"),
                                   ("ROUTINES", "ROUTINE_SCHEMA"), ("EVENTS", "EVENT_SCHEMA")):
                cursor.execute(f"SELECT COUNT(*) FROM information_schema.{catalog} WHERE {field}=DATABASE()")
                if cursor.fetchone()[0]:
                    raise RuntimeError("Non-table objects require a separate backup strategy")
            if not args.apply:
                print(json.dumps({"dry_run": True, "source_tables": len(tables),
                                  "source_writes": False, "restore_verification_required": True}))
                return
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            target = "ba_coach_v2_restore_" + stamp.lower()
            backup = Path("/opt/bacoach/backups") / ("before-full-v2-" + stamp)
            backup.mkdir(mode=0o700, parents=True, exist_ok=False)
            os.chmod(backup, 0o700)
            cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            snapshot = []
            fd = os.open(backup / "source.sql", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                def write(value):
                    stream.write(value.encode("utf8", errors="surrogateescape"))
                write("SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS=0;\n")
                for table in tables:
                    cursor.execute(f"SHOW CREATE TABLE {identifier(table)}")
                    ddl = cursor.fetchone()[1]
                    if f"`{url.database}`." in ddl:
                        raise RuntimeError("Cross-schema reference needs manual review")
                    cursor.execute(f"SELECT * FROM {identifier(table)}")
                    rows = cursor.fetchall()
                    columns = ",".join(identifier(col[0]) for col in cursor.description)
                    insert = f"INSERT INTO {identifier(table)} ({columns}) VALUES ({','.join(['%s'] * len(cursor.description))})"
                    write(ddl + ";\n")
                    for row in rows:
                        write(cursor.mogrify(insert, row) + ";\n")
                    snapshot.append((table, ddl, rows))
                write("SET FOREIGN_KEY_CHECKS=1;\n")
                stream.flush()
                os.fsync(stream.fileno())
            connection.commit()
            # Restore the actual on-disk dump, not a second copy of in-memory rows.
            cursor.execute(f"CREATE DATABASE {identifier(target)} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            restore = pymysql.connect(host=url.host, port=url.port or 3306,
                user=url.username, password=url.password, database=target,
                charset="utf8mb4", binary_prefix=True, autocommit=True,
                client_flag=pymysql.constants.CLIENT.MULTI_STATEMENTS, connect_timeout=15)
            try:
                with restore.cursor() as restored:
                    dump_bytes = (backup / "source.sql").read_bytes()
                    restored.execute(dump_bytes)
                    while restored.nextset():
                        pass
                    verified = {}
                    for table, _, original in snapshot:
                        restored.execute(f"SELECT * FROM {identifier(table)}")
                        rows = restored.fetchall()
                        if len(rows) != len(original) or digest_rows(rows) != digest_rows(original):
                            raise RuntimeError("Restored rows differ from source snapshot")
                        verified[table] = len(rows)
                report = {"restore_verified": True, "source": url.database,
                          "restore_database": target, "backup_directory": str(backup),
                          "sha256": hashlib.sha256(dump_bytes).hexdigest(),
                          "verified_rows": verified, "production_changed": False,
                          "warning": "Snapshot only; take a fresh write-paused snapshot at cutover."}
                fd = os.open(backup / "verification.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf8") as stream:
                    json.dump(report, stream, indent=2)
                print(json.dumps(report))
            finally:
                restore.close()
    finally:
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default="/etc/bacoach/backend.env")
    parser.add_argument("--apply", action="store_true")
    try:
        main(parser.parse_args())
    except Exception as exc:
        print(json.dumps({"restore_verified": False, "error_type": type(exc).__name__,
                          "details": "redacted", "production_changed": False}))
        sys.exit(1)
