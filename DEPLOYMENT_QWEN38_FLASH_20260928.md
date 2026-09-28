# Qwen3.8-Flash deployment — 2026-09-28

## Runtime change

Only the two Qwen model identifiers changed: `DEEPSEEK_MODEL` and `DEEPSEEK_ROUTER_MODEL`, from `qwen3.8-max` to `qwen3.8-flash`. This covers the main reply and the existing tasks that use the routing model. The compatibility provider identifier remains `deepseek`.

The running backend's API key and base URL were compared directly before and after deployment and were identical. Protected environment files were compared byte-for-byte: only the two model lines in `backend.env` changed; `workbench-safety.env` and `pa-push.env` did not change. Thinking settings, budgets, sampling, prompts, provider preferences, and database contents were not changed. The profile and administrator workbench now display `Qwen3.8-Flash`; historical message metadata retains its original model name.

## Release

- Deployed source: `1bda2a19b6da6d46dcd5d2e6b0e4476ff8289ce2`, pushed to `main`.
- Release: `/opt/bacoach/releases/20260928T095043Z`.
- Previous release: `/opt/bacoach/releases/20260928T093721Z`.
- Archive SHA-256: `8750b3f0e9c96bbbb0c9c6a3e7f3e49bd78d94014c06499078068124d7500fa9`.
- All 233 packaged source files matched the committed archive; `RELEASE.json` records this verification.
- Protected rollback configuration: `/opt/bacoach/backups/qwen38-flash-20260928T095043Z`, directory mode 0700, files mode 0600. No credentials are committed to Git or printed in this report.

## Checks and limits

- Before switching, the original production API credentials and URL successfully served the candidate Flash model for non-thinking JSON routing, thinking JSON routing, non-streamed replies, and streamed replies. Returned model identifiers were `qwen3.8-flash`; native reasoning remained separate from visible content.
- All 66 targeted configuration/provider-policy tests passed. TypeScript validation and the server production build passed.
- After switching, the running backend environment confirmed both model names are `qwen3.8-flash`. Backend, frontend, and PA worker were active with zero restart counters; public HTTPS was available.
- The served homepage JavaScript contains the Flash label and no longer contains the Max label.
- Two post-deployment runs of the minimal provider smoke script failed strict JSON decoding at the thinking-router step. The failure output did not retain the raw response, so the cause is undetermined; this report does not classify it as a confirmed product routing failure or claim that every probe passed.
- Subsequent isolated and paired thinking/non-thinking routing probes returned valid JSON. The deployed `decide_target_module_with_reasoning` function, using its default Router prompt and a synthetic M1 misunderstanding input, returned `module_1` with no error and no JSON recovery. This is a function-level check, not a production participant conversation or a full M1–M4 evaluation.
- Post-deployment non-streamed replies passed. One streaming probe also failed its strict `OK` assertion without retaining the returned text; the subsequent diagnostic stream returned exactly `OK`, a `stop` finish reason, and a separate reasoning channel. Both that stream and a subsequent thinking-routing probe used the effective settings and existing reasoning policy. These samples demonstrate successful calls alongside intermittent failed assertions, not guaranteed output-format stability. All live checks used synthetic inputs and made no business-database writes.

`infra/deploy/check_qwen38_flash.py` preserves a repeatable synthetic provider check. Runtime parsing, retry behavior, and model parameters were not changed to address the intermittent probe failures. Future failures should be investigated with the saved application trace rather than inferred from these small samples.

Rollback must restore both the previous release symlink and its original `backend.env` from the protected backup, then restart backend, frontend, and PA worker. A code-only rollback would retain the Flash environment override. The follow-up documentation commit is separate from the deployed source commit.
