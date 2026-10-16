# Model preference visibility and public-share privacy

## Existing admin links — 2026-10-01

- Follow-up authorization explicitly includes already-created administrator shares. `backend/scripts/upgrade_admin_share_snapshots.py` upgrades only active admin-owned version 1 snapshots, preserving their token digests, title, timestamps, original visible transcript and message boundary. Ordinary-user shares and revoked links are excluded.
- The script previews by default, matches saved messages against the owned source conversation, rejects unmatched assistant messages before publication, and requires a new mode-0600 rollback backup before applying. Re-running skips already-upgraded snapshots.
- Both the main checkout and the production-compatible candidate passed 16 focused tests, including unchanged member/revoked links, unchanged existing URLs, later-turn exclusion, idempotency and refusal of mismatched historical messages. Production dry-run matched all 2,543 messages across 28 administrator links; 2 member links are outside the migration.
- This is a data backfill using the already-deployed version 2 share UI/API; no frontend build or release-directory change is required. Original snapshots are backed up only on the server with restricted permissions.
- Applied and verified: all 28 old admin links now use version 2; all 2,543 original messages matched, token digests and visible transcripts stayed unchanged, and both ordinary-user shares stayed byte-for-byte unchanged. Anonymous HTTPS checks passed for 2 historical admin links and both member links. Backend, frontend and PA push remain healthy. Result: `../诊断导出/管理员旧分享升级-20261001/result.json`.
- Also corrected the current venv's Python entry symlink and `pyvenv.cfg` home to `/usr/bin/python3.10` and `/usr/bin`, removing the stale link through the deleted release. `pip check`, uvicorn launch, backend/PA-worker restart and health checks passed.

## Deployed update — 2026-10-01

- Newly created shares follow the creator's persisted account role. Admin shares use snapshot version 2 and freeze all five existing debug panels: reply/router reasoning, models and timings, knowledge chunks, mediator details, and per-node request records. Member shares retain the version 1 transcript-only allowlist.
- Anonymous viewers read the saved snapshot. Knowledge and request panels never fetch private conversation endpoints using snapshot-local message IDs. Missing historical details remain unavailable.
- Existing version 1 links retain their original publication scope, including historical snapshots that contain extra fields. Admins must create a new link to publish debug details. Viewer login and later role changes do not rewrite a published snapshot.
- Validation: 30 backend tests passed, TypeScript passed, and desktop/mobile browser checks passed for all five admin panels, member/legacy filtering, absent details, frozen snapshots, role changes, spoofed request flags and unauthenticated viewing.
- Deployed as `20261001T022504Z`, based on the live `20261001T014514Z` source with only seven share-related files changed. Preserved production reply interruption status, quick-reply behavior and existing request-record schema. The rebased candidate passed 14 backend tests, TypeScript and desktop/mobile checks; server production build and all 267 packaged source hashes passed. Public HTTPS, share contracts and authorization smoke checks passed.
- At the user's request, removed all 147 previous release directories after making current Python and frontend dependencies independent and verifying services. Only the active release remains; approximately 47.93 GiB is free. No database migration or Git push. Detailed manifest, candidate source and result are saved under `../诊断导出/管理员分享部署-20261001/`. The September 30 deployment below is historical.

## September 30 baseline

## Behavior

- Profile model preferences appear only when the authenticated backend grants `can_manage_models`. Member GET/PATCH profile responses omit provider preference and availability; attempts to PATCH the preference, including null or mixed edits, return 403 before any write. The same gate wraps legacy and V2 profile paths.
- Admins retain model preference controls. Ordinary profile fields and existing saved provider choices are unchanged.
- Public shares contain only snapshot title/date and message-local ID, role, visible content and timestamp. Creation explicitly projects these fields without reading telemetry, request records or knowledge references.
- Existing stored snapshots pass through the same reduced schema on every read. Thinking, routing, model names, prompts, request records, knowledge/mediator data, runtime timing, profile metadata and unknown future fields are not serialized.
- Assistant think/thinking blocks, including unfinished blocks, are stripped without promoting thought text into the answer. Recognized tool protocol envelopes are not published. Authored user prose is preserved.
- The share view also projects only public fields and disables diagnostic rendering. Share instructions now describe the limited content. Personal information deliberately written in the visible conversation is still part of its transcript; this feature does not claim semantic anonymization.

## Validation

- 57 tests passed across share privacy, profile and birthday suites; 2 model-selection chat regressions passed. Initial Windows temporary-directory permission issue was resolved with an isolated workspace basetemp.
- Tests cover admin/member creation, old snapshots with sensitive/unknown fields, stored new snapshots, embedded and unfinished thoughts, no reasoning promotion, member model-field omission and forbidden mixed writes, both profile backends, normal profile edits and admin model preference persistence.
- Browser local QA: admin sees model controls; member has zero model-preference headings; generated share has normal text/time/copy controls and no diagnostic entry. Screenshots: output/privacy-qa/share.png and member-profile.png. Synthetic local accounts only, no production account/role/share creation.
- Production frontend build and TypeScript checks passed. The separately deployed RequestRecordDetails change was preserved and typechecked after the build; the final package is built again on the server.

## Deployment

- Target release 20260930T042754Z, based on current production 20260930T042017Z. Preserved the independent production time-context safeguards and request-record display changes.
- No database migration, deletion of shares or modification of conversations is required. Old snapshots remain private in storage and are filtered at the public response boundary.
- Only regenerable node_modules from inactive releases 20260930T031955Z and 20260930T034109Z were removed to provide build space. Sources, database, shared Python environment, current release and immediate predecessor remain intact.
- Caution for rollback: earlier backend releases expose full share details. Preserve this privacy filter when rolling back, or temporarily disable public share access.
- Post-deploy verified: all 249 source hashes match; backend, frontend and push services are active; internal health/public homepage succeed. Live OpenAPI exposes exactly four SharedMessage fields and the profile permission flag; unauthenticated profile access is rejected. Invitation registration and admin protections still pass their smoke checks.
