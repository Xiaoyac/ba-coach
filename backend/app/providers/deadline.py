"""Whole-request deadlines on both the production Python 3.10 and newer Python."""

try:
    from asyncio import timeout
except ImportError:  # Python 3.10: installed/declared compatibility backport.
    from async_timeout import timeout

__all__ = ["timeout"]
