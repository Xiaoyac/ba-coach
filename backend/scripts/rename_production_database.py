"""Scoped, recoverable database-name migration for BA Coach.

Run on the production host as root. Dry-run by default. Never drops a schema.
Pauses the backend for a consistent copy and verifies every row before cutover.
Sensitive backups remain on the server with owner-only permissions.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback
from urllib.request import urlopen

import pymysql
from sqlalchemy.engine import make_url

sys.path.insert(0, ".")
from app.config import Settings

SOURCE = "ba_coach_260803"
TARGET = "ba_coach_260908"
ENV = Path("/etc/bacoach/backend.env")
SERVICE = "bacoach-backend.service"


def digest_rows(rows):
    hashes = [hashlib.sha256(repr(tuple(row)).encode("utf-8")).digest() for row in rows]
    return hashlib.sha256(b"".join(sorted(hashes))).hexdigest()


def main(apply):
    url = make_url(Settings(_env_file=ENV).database_url)
    if url.database != SOURCE or url.host != "rm-wz9m099m6barq69osxo.mysql.rds.aliyuncs.com":
        raise RuntimeError("Source does not match the verified production database")
    connection = pymysql.connect(host=url.host, port=url.port or 3306,
        user=url.username, password=url.password, database=SOURCE, charset="utf8mb4",
        autocommit=True, connect_timeout=15, binary_prefix=True)
    backup = None
    paused = False
    switched = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (TARGET,))
            if cursor.fetchone()[0]:
                raise RuntimeError("Target already exists; refusing to overwrite")
            for catalog, schema_field in (("VIEWS", "TABLE_SCHEMA"), ("TRIGGERS", "TRIGGER_SCHEMA"),
                                          ("ROUTINES", "ROUTINE_SCHEMA"), ("EVENTS", "EVENT_SCHEMA")):
                cursor.execute(f"SELECT COUNT(*) FROM information_schema.{catalog} WHERE {schema_field}=%s", (SOURCE,))
                if cursor.fetchone()[0]:
                    raise RuntimeError("Non-table objects require an explicit migration")
            cursor.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s "
                           "AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME", (SOURCE,))
            tables = [row[0] for row in cursor.fetchall()]
            if not tables or any(not re.fullmatch(r"[a-z0-9_]+", table) for table in tables):
                raise RuntimeError("Unexpected source table set")
            cursor.execute("SELECT DEFAULT_CHARACTER_SET_NAME,DEFAULT_COLLATION_NAME "
                           "FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (SOURCE,))
            charset, collation = cursor.fetchone()
            if not all(re.fullmatch(r"[a-zA-Z0-9_]+", value) for value in (charset, collation)):
                raise RuntimeError("Unexpected schema encoding")
            original_env = ENV.read_text(encoding="utf-8")
            pattern = r"(?m)^DATABASE_URL\s*=.*$"
            if len(re.findall(pattern, original_env)) != 1:
                raise RuntimeError("Expected exactly one DATABASE_URL entry")
            replacement = "DATABASE_URL=" + url.set(database=TARGET).render_as_string(hide_password=False)
            new_env = re.sub(pattern, lambda _: replacement, original_env)
            if not apply:
                print(json.dumps({"dry_run": True, "source": SOURCE, "target": TARGET,
                                  "tables": len(tables), "backend_pause_required": True}))
                return

            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = Path("/opt/bacoach/backups") / ("database-rename-" + stamp)
            backup.mkdir(parents=True, mode=0o700, exist_ok=False)
            os.chmod(backup, 0o700)
            shutil.copy2(ENV, backup / "backend.env.before")
            os.chmod(backup / "backend.env.before", 0o600)
            subprocess.run(["systemctl", "stop", SERVICE], check=True)
            paused = True
            if subprocess.run(["systemctl", "is-active", "--quiet", SERVICE]).returncode == 0:
                raise RuntimeError("Backend still running")

            cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            snapshot = []
            fd = os.open(backup / "source.sql", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                # PyMySQL represents arbitrary BINARY bytes with surrogateescape.
                # Preserve them in the SQL dump; never reinterpret them as UTF-8 text.
                def write_sql(value):
                    stream.write(value.encode("utf-8", errors="surrogateescape"))
                write_sql("SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS=0;\n")
                for table in tables:
                    cursor.execute(f"SHOW CREATE TABLE `{table}`")
                    ddl = cursor.fetchone()[1]
                    if f"`{SOURCE}`." in ddl:
                        raise RuntimeError("Explicit cross-schema reference requires review")
                    cursor.execute(f"SELECT * FROM `{table}`")
                    rows = cursor.fetchall()
                    names = [column[0] for column in cursor.description]
                    columns = ",".join("`" + name.replace("`", "``") + "`" for name in names)
                    insert = f"INSERT INTO `{table}` ({columns}) VALUES ({','.join(['%s'] * len(names))})"
                    write_sql(ddl + ";\n")
                    for row in rows:
                        write_sql(cursor.mogrify(insert, row) + ";\n")
                    snapshot.append((table, ddl, insert, rows))
                write_sql("SET FOREIGN_KEY_CHECKS=1;\n")
                stream.flush()
                os.fsync(stream.fileno())
            connection.commit()

            cursor.execute(f"CREATE DATABASE `{TARGET}` CHARACTER SET {charset} COLLATE {collation}")
            connection.select_db(TARGET)
            cursor.execute("SET FOREIGN_KEY_CHECKS=0")
            try:
                for _, ddl, _, _ in snapshot:
                    cursor.execute(ddl)
                connection.begin()
                for _, _, insert, rows in snapshot:
                    if rows:
                        cursor.executemany(insert, rows)
                connection.commit()
            finally:
                cursor.execute("SET FOREIGN_KEY_CHECKS=1")
            verified = {}
            for table, _, _, rows in snapshot:
                cursor.execute(f"SELECT * FROM `{table}`")
                target_rows = cursor.fetchall()
                if len(target_rows) != len(rows) or digest_rows(target_rows) != digest_rows(rows):
                    raise RuntimeError("Target verification failed")
                # Detect external writers not stopped with the application.
                cursor.execute(f"SELECT * FROM `{SOURCE}`.`{table}`")
                if digest_rows(cursor.fetchall()) != digest_rows(rows):
                    raise RuntimeError("Source changed during copy; refusing cutover")
                verified[table] = len(rows)
            report = {"source": SOURCE, "target": TARGET, "verified_rows": verified,
                      "backup_sql_sha256": hashlib.sha256((backup / "source.sql").read_bytes()).hexdigest()}
            (backup / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            ENV.write_text(new_env, encoding="utf-8")
            switched = True
            subprocess.run(["systemctl", "start", SERVICE], check=True)
            for _ in range(30):
                try:
                    with urlopen("http://127.0.0.1:8000/health", timeout=2) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(1)
            else:
                raise RuntimeError("Backend failed health check")
            # Frontend has service dependency coupling to backend on this host.
            subprocess.run(["systemctl", "start", "bacoach-frontend.service"], check=True)
            for _ in range(30):
                try:
                    with urlopen("https://bacoach.xyz/", timeout=3) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(1)
            else:
                raise RuntimeError("Public frontend failed health check")
            paused = False
            print(json.dumps({"completed": True, "source_retained": True,
                              "backup_directory": str(backup), **report}))
    except BaseException:
        if switched and backup:
            subprocess.run(["systemctl", "stop", SERVICE], check=False)
            shutil.copy2(backup / "backend.env.before", ENV)
        if paused or switched:
            subprocess.run(["systemctl", "start", SERVICE, "bacoach-frontend.service"], check=False)
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        main(args.apply)
    except Exception as error:
        print(json.dumps({"completed": False, "error_type": type(error).__name__,
                          "detail": str(error) if isinstance(error, RuntimeError) else "redacted",
                          "trace": [{"file": Path(f.filename).name, "line": f.lineno}
                                    for f in traceback.extract_tb(error.__traceback__)]}))
        sys.exit(1)
