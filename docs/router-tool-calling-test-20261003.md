# Router native tool-calling test — 2026-10-03

Native tool calling works on the current K3 endpoint, but this test found no routing-accuracy improvement over the existing JSON Router. I recommend retaining JSON in production for now. Tool calling remains a viable interface change if explicit tool contracts become useful for broader agent work.

## Primary comparison: ordinary-chat request settings

26 synthetic, author-labelled cases, each run twice through each approach: **104 model requests**, 52 per arm. These are regression cases, not a measurement of real-user accuracy.

| Metric | Existing JSON Router | Native tool calling |
| --- | ---: | ---: |
| Expected module selected | 52/52 | 52/52 |
| Usable, complete output | 52/52 | 52/52 |
| Two-field output shape | 52/52 | 52/52 |
| Incorrect module transitions | 0 | 0 |
| Repeat module agreement | 26/26 cases | 26/26 cases |
| Module / knowledge-task mismatch before backend correction | 1 | 1 |
| Median Router latency | 4.894 s | 5.069 s |
| Mean Router latency | 5.246 s | 5.208 s |
| Maximum observed latency | 10.837 s | 8.728 s |
| Input tokens, total for 52 requests | 99,220 | 115,756 |
| Output tokens, total for 52 requests | 4,867 | 5,438 |
| Reported cache-read input tokens | 93,440 | 108,032 |

Tool calling added **16.7% input tokens** in this minimal adaptation. This is not a 16.7% price increase: cached input and output have different billing treatment, and no currency-cost estimate was made. The mean latency difference was about 0.04 seconds, insufficient evidence of a speed advantage. Repeated fixtures warm the prompt cache; the cache counters do not predict production chat hit rates.

Both arms once chose a knowledge task belonging to M1 while correctly staying in M2. The existing backend converted the incompatible task to `general`. Native schema-valid arguments therefore still need semantic validation.

## Endpoint compatibility

The tested endpoint is Ark Coding, model `kimi-k3`.

- Named-function forcing (`tool_choice` containing the function name): HTTP 400 `InvalidParameter` in the tested configuration.
- `tool_choice="required"`, one declared tool, `parallel_tool_calls=false`: accepted.
- `tool_choice="auto"`: also accepted in a capability probe; not used in the comparison.
- `strict=true`: accepted in the request. Acceptance alone does not establish that the provider guarantees constrained decoding; the application still validates the result.

The experimental tool is `submit_routing_decision(target_module, knowledge_task)`. It proposes a decision only. No goal confirmation, goal write, module persistence, or other business action is executed by this probe. It takes one model response and does not introduce a tool-result/model-response loop.

Validation rejects missing or multiple calls, wrong tool names, missing call IDs, truncated responses, extra fields, invalid module/task enums, and ordinary prose masquerading as a tool call. The existing Router then applies its normal interpretation and task scoping.

## Method and scope

- Model: K3 for both arms. The separate DeepSeek Flash knowledge reranker was not involved.
- Ordinary-chat profile: auxiliary `thinking_override=false`, which maps to K3 `reasoning_effort=low`; `temperature=0`, maximum output budget 2,048 tokens, 30-second outer timeout, and no SDK retries.
- Both arms used the same effective admin Router prompt, `router_only` business semantics, history, current input and compact state. Paired input hashes matched throughout.
- The tool arm added only an output-channel instruction and a fixed schema. Existing business rules were not rewritten.
- Randomized request order with seed 1003; maximum two concurrent requests.
- Cases covered M1 readiness versus unresolved education, informal plans versus confirmed current-cycle cards, old-cycle cards, different meanings of “试试”, future intention versus actual execution, pre-execution difficulty, declining recording, goal withdrawal, unfinished versus completed reviews, long history, English feedback, and an embedded instruction to override routing.
- The active admin prompt explicitly allows M3 → M4 for an expressed execution difficulty even before execution. That case was labelled according to the active prompt, not an older source default.
- 84 local tests passed, including a captured-request test confirming both arms use the same member-mode model, effort, temperature and output budget.

An earlier exploratory run omitted the conversation-level override. It also made 104 requests and obtained 52/52 correct modules in both arms. It is retained separately and is **not pooled with the primary comparison**. Its average latency was 5.190 s for JSON and 5.191 s for tools.

Total model requests attempted during this task: 208 paired-test requests plus 3 capability probes, including one rejected request. Reported usage for the 208 completed paired requests was 429,952 input and 20,343 output tokens; capability-probe usage is not included in those totals.

## Production and reproducibility

This was a read-only test. Production sessions and records were not changed, and the Router was not switched to tool calling. Effective prompts were read in a read-only transaction; all conversations were synthetic.

The probe was pinned to `/opt/bacoach/releases/20261002T161537Z`. Another release became current during the work; at the final check, `/opt/bacoach/releases/20261002T163836Z` was active and healthy. Its Router, K3 provider, generation-policy and knowledge-task source hashes matched the tested versions; the ordinary-chat thinking-wrapper behavior was also checked. The effective Router prompt hash remained:

`2fff8259ec91c1dbd7b4d4f181d2b2c631ecd31bef6e98276eaf8733b84a1cbb`

Repository artifacts:

- `infra/qa/router_tool_calling_probe.py`: read-only live comparison runner.
- `infra/qa/fixtures/router_tool_calling_cases.json`: fixed inputs and expected labels.
- `infra/qa/summarize_router_tool_calling.py`: offline aggregation.
- `backend/tests/test_router_tool_calling_probe.py`: parser and request-policy tests.

Raw results, effective prompt snapshot, schema, metadata and summaries are saved outside Git in `诊断导出/Router工具调用-20261003/`, with separate `member-profile/` and `exploratory/` directories.

## Recommendation

Keep the current JSON Router as the production default. The existing path already produced correct, parseable output on every tested case, while tools added schema tokens and did not eliminate cross-field semantic mistakes. If adopted later for interface consistency, use the verified `required` configuration behind a switch, retain backend validation and stale-turn protections, and validate with consented real-conversation replays before a broad rollout. No MCP service is needed for this change.
