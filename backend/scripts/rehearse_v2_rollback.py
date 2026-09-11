"""Test cross-schema atomic rename and its reversal on isolated V2 tables."""
import argparse
from datetime import datetime, timezone
import json
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymysql
from sqlalchemy.engine import make_url
from app.config import Settings
from app.database_v2_schema import metadata
from backup_database_v2 import digest_rows, identifier

parser = argparse.ArgumentParser()
parser.add_argument("--database", required=True)
args = parser.parse_args()
if not re.fullmatch(r"ba_coach_v2_rehearsal_[0-9]{8}t[0-9]{6}z", args.database):
    raise RuntimeError("An isolated rehearsal database is required")
url = make_url(Settings(_env_file="/etc/bacoach/backend.env").database_url)
target = "ba_coach_v2_rename_test_" + datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz")
conn = pymysql.connect(host=url.host, port=url.port or 3306, user=url.username, password=url.password,
                       database=args.database, charset="utf8mb4", binary_prefix=True, autocommit=True)
try:
    with conn.cursor() as cursor:
        before = {}
        for name in metadata.tables:
            cursor.execute(f"SELECT * FROM {identifier(name)}")
            before[name] = digest_rows(cursor.fetchall())
        cursor.execute(f"CREATE DATABASE {identifier(target)} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        def renames(source, destination):
            return "RENAME TABLE " + ", ".join(f"{identifier(source)}.{identifier(n)} TO {identifier(destination)}.{identifier(n)}" for n in metadata.tables)
        cursor.execute(renames(args.database, target))
        cursor.execute(renames(target, args.database))
        for name in metadata.tables:
            cursor.execute(f"SELECT * FROM {identifier(name)}")
            assert before[name] == digest_rows(cursor.fetchall()), "Restored contents differ"
        print(json.dumps({"forward_and_rollback_verified": True, "tables": len(before), "production_changed": False}))
finally:
    conn.close()
