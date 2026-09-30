# Model preference visibility and public-share privacy

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
