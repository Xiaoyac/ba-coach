"""Run the V2 API smoke suite ONLY against a named rehearsal database."""
import argparse
import asyncio
import sys
import re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from sqlalchemy.engine import make_url

parser = argparse.ArgumentParser()
parser.add_argument("--database", required=True)
parser.add_argument("--env-file", default="/etc/bacoach/backend.env")
args = parser.parse_args()
if not re.fullmatch(r"ba_coach_v2_rehearsal_[0-9]{8}t[0-9]{6}z", args.database):
    raise RuntimeError("Only a rehearsal database may be tested")
source = make_url(Settings(_env_file=args.env_file).database_url)
if source.database != "ba_coach_260908":
    raise RuntimeError("Unexpected source configuration")
test_url = source.set(database=args.database).render_as_string(hide_password=False)
from smoke_v2_application import main
asyncio.run(main(test_url))
