"""Add only the empty chat-reminder receipts table; dry-run unless --apply."""
import argparse
import asyncio

from app.chat_reminder_schema import metadata
from app.config import get_settings
from scripts import migrate_pa_push


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-database', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    # Reuse the existing target/schema checks, but never create push tables.
    migrate_pa_push.metadata = metadata
    asyncio.run(migrate_pa_push.migrate(get_settings().database_url, args.expected_database, args.apply))
