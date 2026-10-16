"""Two-phase birth-year migration. Back up privately before each write phase.

prepare: backfill years while old workers can still read the date column.
finish: run only after all old workers drain; preserve newer year edits and drop
        the date column. Defaults to read-only inspection.
"""
from __future__ import annotations
import argparse, asyncio, json, os, sys
from datetime import date, datetime
from pathlib import Path
from sqlalchemy import MetaData, Table, inspect, select, text


def snapshot(conn, backup):
    table = Table('user_profile', MetaData(), autoload_with=conn)
    fields = [x for x in ['uuid','birth_date','birth_year','age','reported_age','age_reported_at'] if x in table.c]
    rows = [dict(r) for r in conn.execute(select(*(table.c[x] for x in fields))).mappings()]
    with os.fdopen(os.open(backup, os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as out:
        json.dump({'version':1,'fields':fields,'rows':rows},out,default=lambda x:x.isoformat(),ensure_ascii=False)
        out.flush();os.fsync(out.fileno())
    return table, rows


def migrate(conn, phase='inspect', backup=None, current_year=None):
    current_year = current_year or date.today().year
    cols = {x['name'] for x in inspect(conn).get_columns('user_profile')}
    if phase == 'inspect':
        return {'birth_date_present':'birth_date' in cols,'birth_year_present':'birth_year' in cols}
    if backup is None:raise ValueError('A fresh --backup path is required')
    table,rows = snapshot(conn,backup)
    if 'birth_year' not in cols:
        conn.execute(text('ALTER TABLE user_profile ADD COLUMN birth_year SMALLINT NULL'))
        table = Table('user_profile', MetaData(), autoload_with=conn)
    changed=0
    for row in rows:
        birthday=row.get('birth_date');year=row.get('birth_year')
        if birthday is not None and (phase=='prepare' or year is None):
            year = birthday.year if isinstance(birthday,(date,datetime)) else date.fromisoformat(str(birthday)).year
        if year is None:continue  # Never invent a birth year from a reported age.
        if type(year) is not int or not 1000<=year<=9999:raise ValueError('Invalid existing birth year')
        values={'birth_year':year}
        age_column='reported_age' if 'reported_age' in table.c else 'age' if 'age' in table.c else None
        if age_column:values[age_column]=current_year-year
        if row.get('birth_year')!=year or (age_column and row.get(age_column)!=values[age_column]):
            conn.execute(table.update().where(table.c.uuid==row['uuid']).values(**values));changed+=1
    if phase=='finish' and 'birth_date' in cols:
        conn.execute(text('ALTER TABLE user_profile DROP COLUMN birth_date'))
        assert 'birth_date' not in {x['name'] for x in inspect(conn).get_columns('user_profile')}
    return {'phase':phase,'rows_checked':len(rows),'rows_updated':changed,'date_column_removed':phase=='finish'}


def restore(conn, backup):
    data=json.loads(Path(backup).read_text());assert data['version']==1
    cols={x['name'] for x in inspect(conn).get_columns('user_profile')}
    if 'birth_date' in cols:raise ValueError('Refusing to overwrite an existing date column')
    conn.execute(text('ALTER TABLE user_profile ADD COLUMN birth_date DATE NULL'))
    table=Table('user_profile',MetaData(),autoload_with=conn)
    for row in data['rows']:
        value=row.get('birth_date')
        if value:
            value=date.fromisoformat(value)
            # Do not overwrite a newer birth-year correction with an old date.
            conn.execute(table.update().where(table.c.uuid==row['uuid'],table.c.birth_year==value.year).values(birth_date=value))
    return {'date_column_restored':True}


async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=['inspect','prepare','finish','restore'],default='inspect')
    parser.add_argument('--backup',type=Path)
    args=parser.parse_args()
    if args.phase!='inspect' and args.backup is None:parser.error('--backup is required')
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
    from app.db import get_engine,dispose_db
    from app.birth_years import today
    try:
        async with get_engine().begin() as conn:
            result=await conn.run_sync(lambda c:restore(c,args.backup) if args.phase=='restore'
                else migrate(c,args.phase,args.backup,current_year=today().year))
        print(json.dumps(result))
    finally:await dispose_db()
if __name__=='__main__':asyncio.run(main())
