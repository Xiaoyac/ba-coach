# Conversation sharing — first version

The chat header's **分享当前对话** action (and the administrator's conversation-menu **分享** action) creates a read-only snapshot link instead of downloading Markdown. The owner can create, copy, and preview a link. Share history and revocation controls are not part of the current interface. The dialog does not load or display a history of shares. Anyone holding an active link can read that snapshot without signing in.

## What is shared

- The selected conversation's title and messages as saved when the link is created.
- Existing reply reasoning, router reasoning, model names, and recorded timings.
- Each message's saved knowledge references and mediator details, including recorded mediator reasoning.

The shared view reuses the live chat's message rendering and four expandable detail panels. It has no composer, account sidebar, live updates, or editing controls. Missing historical details stay missing; viewing a share never runs a model or retrieves new knowledge.

Snapshots do not include the owner's account identifiers, other conversations, full prompts, or raw execution-event metadata. Message identifiers in the public payload are local to the snapshot. Do not interpret those identifiers as private message IDs.

## Lifecycle and access

- Creation requires the conversation owner's authentication.
- Link creation rejects an unfinished turn or pending background processing. Retry after processing finishes. If the last user message has no saved reply because generation was stopped or failed, complete a subsequent turn before sharing.
- Original conversation edits and new messages do not change an existing snapshot. Create a new link to share a later version.
- Access uses a random token; the database stores its hash. The complete link is returned at creation, so copy it then. There is no history-list or revocation endpoint. Closing the dialog does not invalidate the link.
- Links previously revoked by the old version remain unavailable. The legacy database tombstone is retained only for compatibility; the application no longer writes new revocations.
- Deleting the source conversation invalidates its shares.
- Public responses are not cached; the share page is excluded from indexing and suppresses referrer disclosure.

## Deployment and verification

Deployed to `https://bacoach.xyz` on 2026-09-28, including the production share-table migration and synthetic desktop/mobile acceptance. See [DEPLOYMENT_SHARING_20260928.md](DEPLOYMENT_SHARING_20260928.md) for the exact source commit, release, verification, and remaining boundaries. Real participant acceptance is separate from these synthetic checks.

The subsequent removal of **已有分享** and revocation is a local change until separately deployed; the original deployment record describes the previous interface and endpoints.

V2 disables automatic startup DDL. Before deploying this feature, an operator must add the new app-owned `conversation_shares` table to the intended database. From `backend`, using the deployment's Python environment and existing `DATABASE_URL`:

```sh
PYTHONPATH=. python scripts/create_conversation_shares.py --expected-database YOUR_DATABASE_NAME
PYTHONPATH=. python scripts/create_conversation_shares.py --expected-database YOUR_DATABASE_NAME --apply
```

The first command prints the plan without changing the database. `--expected-database` must exactly match the database name (or SQLite file path) in the configured URL. The second adds only the share table and its indexes; it does not rewrite existing conversations or business tables. Existing compatible tables are accepted; incompatible tables cause an error. Fresh local installations with startup maintenance enabled create this table through normal app initialization.

Processing guards use the application's existing single-worker session locks. This change does not add multi-worker synchronization. The four panel datasets are copied from persisted messages and the associated `main_generation` reference snapshot. Link tokens are bearer credentials: protect/redact `/share/` and `/api/shares/` paths in deployment access logs. The database stores only a token hash, but web-server logs are configured separately.

## Files changed and why

| File | Reason |
| --- | --- |
| `backend/app/models.py` | Add immutable snapshot storage, token hash and conversation deletion cascade; retain legacy invalidation markers to avoid republishing old links. |
| `backend/app/main.py` | Register the owner-management and public-read routes. |
| `backend/app/graph/nodes.py` | Expose a nonblocking pending-processing check used only when publishing a share. |
| `backend/app/routes/shares.py` | Implement owned creation, stable panel capture, and anonymous reads; remove history-list and revocation endpoints. |
| `backend/app/share_schemas.py` | Define an explicit public allowlist separate from private conversation schemas. |
| `backend/scripts/create_conversation_shares.py` | Provide an additive, dry-run-by-default migration for existing deployments. |
| `backend/tests/test_conversation_shares.py` | Test ownership, immutable contents, removed endpoints, legacy invalidation/deletion, pending guards, missing data, and migration behavior. |
| `frontend/components/ConversationWorkspace.tsx` | Replace Markdown download with the share dialog and pass the selected conversation's generation status. |
| `frontend/components/ConversationShareModal.tsx` | Offer creation, copy, and preview; remove history, revocation handlers, list loading, retry, and associated state. |
| `frontend/lib/shares.ts` | Provide typed create/public-read calls without history-list or revocation wrappers; public reads omit viewer credentials. |
| `frontend/app/share/[token]/page.tsx` | Add a dynamic public route with non-indexing and referrer metadata. |
| `frontend/components/SharedConversationView.tsx` | Render a read-only snapshot, access/error states, and access rechecks on tab return. |
| `frontend/components/MessageRow.tsx` | Extract the existing message display for identical rendering in live and shared conversations. |
| `frontend/components/Chat.tsx` | Reuse message rendering, retain administrator-only live diagnostics, and expose sharing in the member chat header. |
| `frontend/components/ReasoningDetails.tsx` | Pass snapshot detail data through the existing four-panel UI. |
| `frontend/components/KnowledgeReferenceDetails.tsx` | Render supplied reference data without private fetches; expose saved mediator reasoning in a nested disclosure. |
| `frontend/next.config.mjs` | Add no-store, no-referrer, and non-indexing headers to share pages. |
| `frontend/tests/share-browser.cjs` | Exercise desktop/mobile panel interactions, credential isolation, unavailable snapshots and owner create/copy workflows without history or revocation controls. |
| `infra/deploy/server-deploy-sharing.sh` | Build an isolated release, apply only the share-table migration, and restore the previous release if cutover fails. |
| `CONVERSATION_SHARING.md` | Document behavior, lifecycle, deployment, verification, and the reasons for each changed file. |

## Local validation

- After removing share history and revocation: all 13 sharing backend tests, TypeScript validation, and the updated desktop/mobile sharing browser checks passed.
- Backend: 59 passing tests across conversation sharing, existing conversation operations, and knowledge-reference persistence; includes 13 new sharing/migration cases. Tests use isolated databases and stub models.
- After merging the current `main`: the complete backend suite passed in isolated per-file processes (1,799 passed, two optional skips across 107 test files). Sharing browser checks and existing member/admin single-chat browser regressions also passed against the merged source.
- Frontend: TypeScript typecheck and optimized Next.js production build pass; `/share/[token]` is emitted as a dynamic route.
- Browser: desktop (1280 px) and mobile (390 px) share-page tests pass; all four panels and saved mediator reasoning open, no private APIs or viewer credentials are used, missing details stay unavailable, invalidated links fail on reload, and unsafe markup renders as text.
- Owner-dialog browser coverage includes unfinished-turn rejection, retry, creation, manual-copy fallback, absence of revocation controls, and reopening without a history section or list request. The updated test uses synthetic API responses.
- `git diff --check` passes. Production deployment and migration results are recorded separately in the deployment record linked above.

Reproduce backend checks from `backend`:

```sh
../.venv/bin/python -m pytest tests/test_conversation_shares.py tests/test_conversations.py tests/test_knowledge_references.py
```

Browser checks require the existing Playwright tooling and an installed Chrome browser. With the frontend running on port 3108:

```sh
SHARE_QA_URL=http://127.0.0.1:3108 node tests/share-browser.cjs
```

Use `PLAYWRIGHT_CHANNEL` to select another installed supported browser. The browser test uses synthetic API fixtures, not production data.

## Scoped production release runner

`infra/deploy/server-deploy-sharing.sh` builds a separate release, checks that the backend requirements match the current release before reusing its Python environment, and runs only the additive sharing migration. It does not run legacy migrations, backfills, or knowledge imports. Protected configuration remains in `/etc/bacoach`; the runner does not include or copy secrets into the release.

After uploading the production source archive and runner, run on the deployment server as root, using the actual release ID and independently verified database name:

```sh
bash /path/to/server-deploy-sharing.sh \
  YYYYMMDDTHHMMSSZ /tmp/bacoach-YYYYMMDDTHHMMSSZ.tar.gz ACTUAL_DATABASE_NAME
```

The runner checks mediator configuration without generating a reply, previews and applies the share-table migration, verifies its resulting schema, and only then switches `/opt/bacoach/current` and restarts the services. It restores the previous code and the reminder worker's prior active state if cutover fails. The additive share table remains on rollback so existing snapshots are not deleted. The previous release and its Python environment must remain available for rollback and environment reuse.

This runner does not replace release acceptance: verify public HTTPS, anonymous share access, all saved detail panels, owner-only creation, and unavailable-link handling after deployment. Protect bearer-token paths in actual proxy and application access logs before sharing real conversations. The commands above document the procedure; their presence is not evidence that a deployment has been performed.
