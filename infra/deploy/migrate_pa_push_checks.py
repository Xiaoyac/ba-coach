"""Pre-deploy additive push migration using staged schema, without replacing live code.

Stage this beside the proposed backend/app/push_schema.py, then run with the
current production Python/PYTHONPATH/env. Dry-run by default, exact DB required.
"""
import argparse
import asyncio
import importlib.util
from pathlib import Path
from app.config import get_settings
from scripts import migrate_pa_push

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-database',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    spec=importlib.util.spec_from_file_location('proposed_push_schema',Path(__file__).with_name('push_schema.py'))
    proposed=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proposed)
    assert set(proposed.metadata.tables)=={'pa_push_devices','pa_push_deliveries','pa_push_checks'}
    migrate_pa_push.metadata=proposed.metadata
    asyncio.run(migrate_pa_push.migrate(get_settings().database_url,args.expected_database,args.apply))
