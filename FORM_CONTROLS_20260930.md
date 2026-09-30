# Birthday and invitation controls — 2026-09-30

## Changes

- Shared BirthdayPicker replaces browser date calendars in registration, profile editing and the mandatory birthday dialog. Separate year/month/day buttons open themed inline grids; year navigation moves 20 years at a time. No default birthday is supplied.
- Shanghai calendar date determines the existing 10–120 age limits. Invalid month/day combinations are cleared after a change; partial dates cannot be submitted as profile edits. Leap years and the oldest eligible partial year are handled.
- Picker keyboard focus follows each stage; Escape closes only the picker, with focus returned to its trigger. Pointer outside / keyboard focus outside closes the panel.
- Invitation generation quantity uses four visible options (1, 5, 10, 20), with a clear selected state and disabled controls while submitting. Existing invitation security and lifetime rules are unchanged.

## Validation

- `npm run typecheck`, `npm run build`, `git diff --check` passed.
- Local browser: registration starts empty, month/day initially disabled; explicit selection advances stages; invalid January 31 → February and leap February 29 → ordinary year clear the day. Incomplete profile changes show a validation error without saving.
- Escape leaves the profile modal open. Year pagination remains open at its disabled navigation boundary.
- On 2026-09-30, 2016-09-30 is accepted as age 10; October–December 2016 are disabled. 1905-10-01 is accepted as age 120; January–September 1905 are disabled.
- Local admin selected 5 invitations and generated exactly 5. Synthetic local database only; no production invitations or accounts created.
- Desktop and 390×844 screenshots checked, including light invitation manager and dark birthday picker. No horizontal document overflow. Evidence: output/form-qa/.

## Release

- Target: https://bacoach.xyz, release 20260930T034109Z.
- Previous: /opt/bacoach/releases/20260930T031955Z (preserves invitation enforcement on rollback).
- Deployment package contains 249 source files. Reuses existing Python environment and settings; no database/schema or dependency changes.
- To provide build space, removed only regenerable frontend/node_modules directories from our inactive releases 20260930T022500Z and 20260930T024423Z. Their sources and build output remain; redeploying either requires npm ci. Current and immediate previous releases, all databases, and shared Python environments are preserved.
- Post-deploy: all 249 source hashes match; backend, frontend and push services are active; internal health and public homepage passed. Missing invitation returns 422; anonymous list/generation returns 401. Final in-app browser production navigation timed out twice, so screenshots document local browser QA; production UI was not visually rechecked. Viewport override was reset before these timeouts.
