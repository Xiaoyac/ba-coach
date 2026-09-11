"""Add read-only DMS projections; no base table alteration, backfill or deletion."""
import argparse
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import Settings
from app.workflow_contract import STEP_REGISTRY, VERSION


def quoted(value):
    return "'" + value.replace("'", "''") + "'"


SUPPORTERS = """
SELECT p.uuid AS user_id, j.position AS supporter_position, j.relation, j.nickname, j.influence,
       'profile_extensions.supporters' AS source
FROM user_profile p JOIN profile_extensions e ON e.profile_uuid=p.uuid
CROSS JOIN JSON_TABLE(IF(JSON_TYPE(e.supporters)='ARRAY', e.supporters, JSON_ARRAY()), '$[*]'
 COLUMNS(position FOR ORDINALITY, relation VARCHAR(32) PATH '$.relation',
 nickname VARCHAR(64) PATH '$.nickname', influence VARCHAR(8) PATH '$.influence')) AS j
UNION ALL
SELECT p.uuid, 1, COALESCE(p.supporter1_relation, '未说明（旧档案）'), p.supporter1_nickname, p.supporter1_influence, 'legacy_slot_1'
FROM user_profile p LEFT JOIN profile_extensions e ON e.profile_uuid=p.uuid
WHERE (e.supporters IS NULL OR JSON_TYPE(e.supporters)='NULL') AND (p.supporter1_relation IS NOT NULL OR p.supporter1_nickname IS NOT NULL)
UNION ALL
SELECT p.uuid, 2, COALESCE(p.supporter2_relation, '未说明（旧档案）'), p.supporter2_nickname, p.supporter2_influence, 'legacy_slot_2'
FROM user_profile p LEFT JOIN profile_extensions e ON e.profile_uuid=p.uuid
WHERE (e.supporters IS NULL OR JSON_TYPE(e.supporters)='NULL') AND (p.supporter2_relation IS NOT NULL OR p.supporter2_nickname IS NOT NULL)
"""

M1 = """
SELECT c.id AS conversation_id, c.session_id, c.subject_id AS user_id,
 CASE WHEN JSON_CONTAINS(p.module_1_steps, JSON_QUOTE('ba_education_completed'))=1 THEN 'router_reported_complete' ELSE 'not_recorded' END AS ba_explanation_status,
 CASE WHEN JSON_CONTAINS(p.module_1_steps, JSON_QUOTE('goal_setting_consent'))=1 THEN 'router_reported_consent' ELSE 'not_recorded' END AS goal_setting_willingness,
 p.module_1_steps AS completed_steps, p.updated_at
FROM conversations c LEFT JOIN conversation_module_progress p ON p.conversation_id=c.id
"""


async def main(args):
    engine=create_async_engine(Settings(_env_file=args.env_file).database_url)
    try:
        if engine.url.get_backend_name()!='mysql' or engine.url.database!=args.expected_database:
            raise ValueError('Wrong database')
        registry=' UNION ALL '.join('SELECT '+', '.join(f'{quoted(v)} AS {k}' for k,v in
            [('module',module),('step_key',key),('label',label),('completion_rule',rule),('registry_version',VERSION)])
            for module,rows in STEP_REGISTRY.items() for key,label,rule in rows)
        views={'v_user_supporters':SUPPORTERS,'v_m1_completion_status':M1,'v_workflow_step_registry':registry}
        async with engine.connect() as conn:
            version=(await conn.execute(text('SELECT VERSION()'))).scalar_one()
            # Require JSON_TABLE support before creating any object.
            await conn.execute(text("SELECT * FROM JSON_TABLE('[1]', '$[*]' COLUMNS(n INT PATH '$')) AS t"))
            existing=set((await conn.execute(text('SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()'))).scalars())
            present=[name for name in views if name in existing]
            if args.apply and present:
                raise ValueError('Existing target view/object requires inspection; refusing overwrite')
            if args.apply:
                for name,query in views.items():
                    await conn.execute(text(f'CREATE SQL SECURITY INVOKER VIEW `{name}` AS '+query))
                await conn.commit()
            print(json.dumps({'database':args.expected_database,'server_version':version,'apply':args.apply,
                'views':list(views),'existing':present,'base_tables_modified':False},ensure_ascii=False))
    finally:
        await engine.dispose()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--env-file',required=True);p.add_argument('--expected-database',required=True)
    p.add_argument('--apply',action='store_true')
    try: asyncio.run(main(p.parse_args()))
    except Exception as e:
        print(json.dumps({'error_type':type(e).__name__,'details':'Redacted. No base tables are modified; inspect target view existence before retry.'}))
        raise SystemExit(1)
