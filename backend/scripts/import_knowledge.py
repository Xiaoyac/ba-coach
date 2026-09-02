"""Import Markdown files into BA Coach's shared knowledge base.

Usage:
    python scripts/import_knowledge.py BA path/to/chapters.md [more.md ...]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import dispose_db, get_sessionmaker, init_db  # noqa: E402
from app.knowledge_store import KNOWLEDGE_CATEGORY_MODULES, import_knowledge_source  # noqa: E402


async def run(category: str, paths: list[Path]) -> None:
    await init_db()
    try:
        async with get_sessionmaker()() as db:
            for path in paths:
                markdown = path.read_text(encoding="utf-8-sig")
                result = await import_knowledge_source(
                    db,
                    name=path.name,
                    category=category,
                    markdown=markdown,
                    updated_by="server-import",
                )
                print(
                    f"{path.name}: {result.chunk_count} chunks "
                    f"({'unchanged' if result.unchanged else 'imported'})"
                )
            await db.commit()
    finally:
        await dispose_db()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("category", choices=KNOWLEDGE_CATEGORY_MODULES)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    missing = [str(path) for path in args.paths if not path.is_file()]
    if missing:
        parser.error(f"files not found: {', '.join(missing)}")
    asyncio.run(run(args.category, args.paths))


if __name__ == "__main__":
    main()
