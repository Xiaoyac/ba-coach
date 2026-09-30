# Verbatim opening deployment — 2026-09-28

- Deployed code: `23e2c22aed0b35b15f32512c8dda8011597f2249`, pushed to `main`.
- Release: `/opt/bacoach/releases/20260928T093721Z`.
- Previous release: `/opt/bacoach/releases/20260928T092504Z`.
- Archive SHA-256: `3a7d6c4bac2d71517a2061dcca80dbbf80536ef021487592192af1412c18b41c`.
- All 233 deployed source files matched the committed archive. Release metadata is stored in the server release's `RELEASE.json`.

## Change

The server-owned opening uses the user's supplied five paragraphs verbatim, preserving punctuation and paragraph breaks. Both regular and returning-user creation paths use the same text; initial module selection is unchanged. Administrator sandboxes retain their intentional empty start. Existing conversation messages and published snapshots are not rewritten.

- `backend/app/opening.py`: replace the canonical text.
- `backend/app/conversation_store.py`: remove the alternate returning-user copy and recognize existing reserved-position openings during legacy backfill, preventing a second opening after copy changes.
- `backend/app/prompts.py`: remove a stale commented copy; the opening is not injected as another system instruction.
- `backend/tests/test_admin_fresh_conversation.py` and `backend/tests/test_conversations.py`: verify unified text without changing module reuse, and preserve historical openings without duplication.
- `frontend/tests/member-single-chat.cjs`: align the browser fixture and selectors with the exact text.

## Verification

The supplied opening is 232 characters including paragraph separators; UTF-8 SHA-256 is `ba4c3dfdc426666d0833e937f928fc3b02b612f906ab7a36a69e1cbe93b9e129`.

All 51 targeted backend tests passed (36 conversation tests, six administrator fresh-conversation tests, nine member current-conversation tests). Production frontend build and type checks passed. The code-only deployment runner performed no migration, backfill, or knowledge import. Backend, frontend, and PA reminder services were active with zero restart counters; health and public HTTPS returned 200.

On the production database, both regular and explicit-fresh creation helpers saved text identical to the supplied opening. Synthetic rows were created within an outer rollback transaction; absence of all test conversation, message, and runtime rows was checked afterward. No model calls or participant-message edits were made.

Browser verification uses the deployed frontend with synthetic API fixtures containing the exact opening. Desktop and mobile render checks and the existing member/admin regression suite passed. The documentation commit is separate from the deployed source commit.
