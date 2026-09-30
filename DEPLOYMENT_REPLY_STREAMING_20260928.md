# Reply streaming restoration — 2026-09-28

## Behavior

Ordinary M1–M4 replies are emitted as content arrives. The post-generation answer validator records findings and an original classification in trace data, but does not buffer, replace, or hold the reply or its business progression. Existing business confirmation and database consistency checks remain separate from reply diagnostics.

The `ANSWER_VALIDATOR_ENABLED` setting now controls diagnostics, not display interception. V2 conversations no longer force full-answer buffering. Reasoning remains a separate channel, released after channel normalization. Technical provider failures and malformed protocol handling remain; no-answer recovery does not overwrite text already streamed. A partial provider failure retains the exact visible text in history.

Two additional blockers were confirmed during deployment verification:

- Production Python 3.10 did not propagate the implicit LangGraph writer context. Explicit writer/config injection with the public config-context helper now carries events through the graph. In the installed LangGraph version, even the injected writer internally reads the config, so binding that config is necessary. This is consistent with the documented [Python-version limitation](https://reference.langchain.com/python/langgraph/config/get_stream_writer).
- The actual model returned a JSON object containing `chat_reply`. The old decoder waited for the complete object. The decoder now streams the string field incrementally, including split escaped quotes, newlines and UTF-16 surrogate pairs; wrapper fields are not displayed. Other unrecognized structured envelopes retain their existing final parsing behavior.

## Changed files

- `backend/app/graph/nodes.py`: immediate visible deltas; diagnostics-only validation; consistent partial-response persistence; explicit graph stream writer/config; incremental `chat_reply` decoding.
- `backend/app/config.py`, `backend/.env.example`: describe the diagnostics-only meaning of the existing setting.
- `backend/scripts/check_reply_streaming.py`: repeatable synthetic and optional real-model graph checks, with production cancellation wrapper and isolated in-memory history. No business database writes.
- `backend/tests/test_live_reply_streaming.py`: first-delta-before-EOF handshake, V2 path, absent implicit context, injected config isolation, partial failures, JSON strings and split escapes.
- Existing answer-validator, recovery, workflow, Router-only and quality tests: retain finding assertions while replacing obsolete expectations of hidden/replaced replies.

## Verification

- 453 targeted backend tests passed.
- A deterministic server-side test blocks provider completion until the graph consumer acknowledges the first delta. This exposed the Python 3.10 issue and passed with the fix.
- A real-model candidate check using production credentials/settings and synthetic input received 25 visible chunks: first delta at 6,985 ms, provider EOF at 8,806 ms. Delivered text matched isolated history. This is a single graph-level sample, not a typical latency estimate or a browser participant session.
- The API proxy has buffering and gzip disabled. Frontend SSE handling already appends incoming deltas.
- Router, retrieval and provider thinking still precede visible content. This change removes full-answer buffering; it does not promise an immediate first token.

## Release

- Deployed source commit: `197d72f53091d1c31d5101e80ef2ca7d1497dc00`, pushed to `main`.
- Running release: `/opt/bacoach/releases/20260928T113537Z`.
- Archive SHA-256: `733fd95d6b526e1d4c11e656163224cdfa207cef13207dcbc65f653ce90b7fcc`.
- All 234 packaged files matched the committed archive. The running backend process uses this release directory; `RELEASE.json` records the verification.
- Backend, frontend and PA worker are active with zero restart counters. Production frontend build/type checks passed; public HTTPS homepage returned 200.
- Final-release synthetic handshake passed. A fresh real-model check through the production cancellation wrapper received 11 visible chunks, first delta at 3,891 ms and provider EOF at 4,647 ms. Streamed text exactly matched isolated history; no business database writes were made.
- All three protected environment files were hash-compared before and after deployment and remained unchanged. Model names, credentials/URL, prompts and database contents were not changed; no migrations/backfills ran.
- An intermediate release (`20260928T112542Z`, commit `1785eb4`) removed validator buffering but failed the production streaming handshake. It was superseded after resolving both Python 3.10 writer propagation and JSON-envelope buffering. Do not use it as evidence of successful streaming.
- To roll back to the configuration in service before this task, restore `/opt/bacoach/releases/20260928T095043Z` as `current` and restart backend, frontend and PA worker. This task requires no environment rollback.

This deployment report is a separate documentation commit; the runtime source commit is the one specified above.
