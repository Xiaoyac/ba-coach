# Conversation sharing deployment — 2026-09-28

## Release identity

- Site: https://bacoach.xyz
- Deployed source commit: `166a574d4f6e4514f4ffdf2e9bf1ea0c75e14f51` (pushed to `main`).
- Release: `/opt/bacoach/releases/20260928T073901Z`.
- Previous release: `/opt/bacoach/releases/20260927T061243Z`.
- Archive SHA-256: `57cbd070939879d2d99db7e96e14fe5d5eea4ab179750c158f5aef83ed8742c1`.
- All 233 packaged source files were compared byte-for-byte against the committed archive after deployment. The release's `RELEASE.json` records source identity and the previous release.
- This deployment record is a later documentation-only commit; it does not change the deployed runtime source.

The sharing implementation was merged with the latest `main` before deployment. The existing Router experiment, native-thinking controls, member single-chat interface, and administrator-only live diagnostics were retained. Sharing is accessible from the chat header for members and administrators, as well as the administrator's conversation menu.

## Database and rollout

- Verified target database: `ba_coach_260908`, MySQL, V2 schema mode.
- Applied only the additive `conversation_shares` table and its indexes. The migration preview, application, and subsequent schema inspection succeeded. Existing conversation/business data was not migrated or rewritten.
- Backend requirements matched the previous release after newline normalization, allowing reuse of its Python environment. The previous environment must remain available.
- Built the new frontend before switching the current release. Next.js production compilation and type validation passed, including the dynamic `/share/[token]` route.
- Switched the current-release symlink and restarted backend, frontend, and the previously active PA reminder worker. All three services were active afterward, their process working directories pointed to the new release, and their restart counters were zero at final verification.
- Backend health, local frontend, and public HTTPS home returned 200.
- Before building, the disk had approximately 1 GB free. Removed only reproducible `.next/cache` directories from 90 old releases, preserving caches for the current and previous releases. No release source, runtime build output outside those cache directories, database, or user data was deleted. Approximately 6.6 GB remained after deployment.

## Validation

Local merged-source checks:

- Complete backend suite: 1,799 passed, two optional skips, no failures or errors across 107 test files, using isolated per-file processes.
- Sharing browser tests: desktop/mobile panels, saved mediator reasoning, missing-data behavior, safe text rendering, credential isolation, owner create/retry/copy/revoke/reopen, and revoked reload.
- Existing member/admin single-chat browser regressions passed.
- TypeScript validation and `git diff --check` passed.

Live production checks used one explicitly marked synthetic conversation, two synthetic messages, one execution event containing synthetic saved panel data, and one share. No account was created, no model was called, and no participant conversation was read or modified.

- The deployed ownership-aware create/revoke handlers were exercised directly with the synthetic owner. Positive owner HTTP/UI authentication was covered by local tests, not by logging into a real production account.
- On public HTTPS, desktop (1280 px) and mobile (390 px) rendered the saved conversation without login, a composer, or edit controls.
- Reply reasoning, Router reasoning, knowledge chunks, mediator guidance, and nested saved mediator reasoning all expanded successfully.
- Browser requests were limited to the public share API, with no authentication headers/cookies or private conversation requests; no browser page errors occurred.
- Public share pages returned `no-store` and `no-referrer` headers. Invalid public share requests returned 404 with no-store/non-indexing headers.
- An unauthenticated share-creation HTTP request returned 401.
- Revocation made the deployed public lookup return 404, and subsequent desktop/mobile page loads showed the unavailable-link state with no synthetic conversation content.
- Removed exactly the synthetic conversation, its two messages, its one execution event, and its one share. All four remaining-row counts were verified as zero.

## Operating boundaries

- Links expose the saved snapshot to anyone holding the link, until revoked. Revocation cannot remove copies already read or saved.
- Historical details that were not originally persisted remain unavailable; opening a share does not regenerate them.
- The deployment still uses the existing single-worker session-lock design.
- Database tokens are hashed. Application/proxy access logs may contain bearer-token URLs; URL redaction was not added in this rollout. The inspected Nginx access log is restricted to `www-data:adm` with mode `0640`; operators must keep application and proxy logs restricted and avoid copying active share URLs into public diagnostics.
- User acceptance with a real completed conversation remains a product check. These tests establish synthetic end-to-end behavior, not the quality or completeness of historical participant traces.

Rollback restores the previous release symlink and restarts the three services, retaining the additive table. Do not delete the previous release or its Python environment while it is used by the new release.
