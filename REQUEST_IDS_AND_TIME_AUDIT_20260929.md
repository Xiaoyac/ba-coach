# 请求 ID 显示与时间、缓存检查记录

日期：2026-09-29。范围：本地修改与验证；本轮没有提交、推送或部署。

## 网页如何定位百炼日志

管理员聊天界面：展开某条助手回复的「调试详情」→「模型请求 ID」。

- 分别显示主回复、Router、知识中介的请求 ID，支持复制和刷新。
- 首次调用与恢复调用分别列出。字段抽取、摘要等其他调用，仅在已有明确的消息关联时展示，不能按时间接近猜测它属于哪一轮。
- 普通用户聊天界面的调试入口保持隐藏。查询接口仍校验账号、会话和助手消息所有权。
- 新创建的分享会保存这些 ID；分享页只读该快照，不访问原私有会话接口。旧分享没有的 ID 不会自动补填。
- 上游没有返回、旧数据没有保存时显示「未记录」，不生成或推算请求 ID。

已修复流式采集缺陷：OpenAI SDK 的流对象没有原代码读取的 `_request_id` 属性；现在从真实 HTTP 响应头 `x-request-id` 获取，并在正文返回前保留，防止中途断流丢失。非流式继续使用 SDK 的响应头映射字段。正文中的 `chatcmpl-*` 响应 ID 不冒充 HTTP 请求 ID。

百炼控制台支持按 Request ID 精确筛选日志；是否能查到某次调用，还取决于对应服务的日志配置与保留情况。[百炼推理日志说明](https://help.aliyun.com/zh/model-studio/model-telemetry)

## 本次请求 ID 功能修改文件

| 文件 | 修改原因 |
| --- | --- |
| `backend/app/providers/deepseek.py` | 正确读取流式响应头 ID；保留非流式、HTTP 错误和断流时已取得的 ID。 |
| `backend/app/knowledge_mediator.py` | 将中介调用的请求 ID 带入诊断指标。 |
| `backend/app/graph/nodes.py` | 中介事件关联本轮用户消息；保存主回复首次、恢复及异常请求的 ID。 |
| `backend/app/reply_recovery.py` | 恢复调用失败时仍保留上游返回的 ID。 |
| `backend/app/request_diagnostics.py`（新增） | 按明确消息关联查询和筛选请求 ID；只输出阶段、ID、provider、model 四个字段。 |
| `backend/app/routes/conversations.py` | 增加只读的消息请求 ID 接口，校验所有权。 |
| `backend/app/routes/shares.py`、`backend/app/share_schemas.py` | 将本分享内的请求 ID 显式纳入快照白名单。 |
| `frontend/components/ModelRequestDetails.tsx`（新增） | 请求 ID 面板、复制、刷新、错误重试和空值说明。 |
| `frontend/components/ReasoningDetails.tsx`、`frontend/components/MessageRow.tsx` | 在现有调试详情中接入新面板。 |
| `frontend/lib/conversations.ts` | 新接口的前端类型与读取方法。 |
| `frontend/lib/shares.ts`、`frontend/components/SharedConversationView.tsx` | 分享页使用快照中已保存的 ID，不回源查询。 |
| `backend/tests/test_provider_request_ids_0929.py`（新增） | 用真实 SDK、离线模拟 HTTP 响应验证请求 ID，而非只测试自制流对象。 |
| `backend/tests/test_request_diagnostics_0929.py`（新增） | 验证权限、精确轮次归属、恢复请求、新旧分享和批量查询。 |
| `backend/tests/test_turn_request_ids_0929.py`（新增） | 验证流式生成到数据库再到网页接口的完整 ID 传递及断流保留。 |
| `infra/qa/request-ids-0929.cjs`（新增） | 电脑、手机上的管理员、普通用户、新旧分享 UI 验收。使用合成数据，无线上模型请求。 |

没有新增数据库列或迁移；没有改变 API key、模型 URL、模型名称或调用重试次数。

## 时间与缓存检查结论

线上只读检查时，运行目录为 `/opt/bacoach/releases/20260929T122040Z`。

1. 原实现确实每轮把精确到秒的当前时间和历史时间表加入 system。主回复还需把独立时间表与历史消息对应起来。
2. 抽查该版本上线后的 40 条主回复执行记录，40 条都有动态 system 时间。部分同会话调用仍有约 1.1 万至 1.28 万字符的相同 system 前缀，因此不能据此断言缓存命中率为零。更早的业务状态、检索和中介内容变化也可能缩短相同前缀。
3. 这批应用记录没有保存模型缓存命中 token 数。用户已确认「零命中」是猜测，并非控制台实测结果。网页原有「缓存命中率」属于知识检索缓存，和百炼模型上下文缓存不同。
4. 本地已补充读取上游 `usage.prompt_tokens_details.cached_tokens`，记录为执行事件 metadata 的 `cache_read_input_tokens`。缺失就不填写；真实的 0 才保存为 0。不根据缺失值计算命中率。没有开启显式缓存或修改计费估算逻辑。

百炼隐式缓存基于公共输入前缀，是否命中由服务决定，不能承诺每次命中。[百炼上下文缓存说明](https://help.aliyun.com/zh/model-studio/context-cache)

本轮最初的时间整理将稳定的时间规则保留在 system，把真实时间绑定到对应消息，保持 role、顺序、原始正文和证据索引；M1 抽取也保留独立 `created_at`。没有缩短现有 80 条消息窗口。相关改动涉及 `conversation_time.py`、`context_pipeline.py`、`schemas.py`、`generation_policy.py`、`pre_reply_routing.py`、`router_agent.py`、`graph/nodes.py`、`m1_contract.py`，缓存指标另涉及 `providers/deepseek.py` 和 `ai_telemetry.py`。

随后，用户确认另一个任务正在把消息统一为 `<message><datetime>…</datetime><content>…</content></message>` 格式。本任务保留了该任务的改动，没有回滚或继续重写其格式。最终消息格式及相应测试应由该任务完成对齐。

时间标签是给模型的语义线索，不会强制模型理解正确，也不能替代消息顺序、事实修订处理或实际多轮对话验收。本轮没有用真实模型重新回放错位对话，不能宣布所有错位已经解决。

## 验证与边界

- 最初时间整理与缓存指标版本：后端全量 **1919 passed，2 skipped**。
- 请求 ID 功能及相关回归：**186 passed**。
- 前端类型检查通过；**8 个浏览器场景通过**，覆盖电脑与手机的管理员、普通用户、新分享、旧分享，检查复制、加载失败重试、访问隔离和无横向溢出。
- 增加请求 ID 后的一次全量运行：**1932 passed，20 failed，2 skipped**。这次运行与另一任务修改 XML 格式重叠；失败涉及旧格式断言及被移除的 `prepare_context(current_time=...)` 参数。没有通过回滚另一任务或跳过测试将其写成全量通过。
- 未调用线上模型做验收；未修改生产数据库；未提交、推送、部署。缓存实际命中率、真实模型的对话效果需要上线后的观测。
- 本地原有 PA 站内提醒等未完工作已保留，未纳入本次请求 ID 功能改写。

验收产物：`.test-tmp/request-ids-0929/` 下的电脑/手机界面截图。测试日志分别为 `/tmp/ba-request-id-focused-final-0929.log`、`/tmp/ba-request-id-ui-0929.log`、`/tmp/ba-datetime-request-id-final-0929.log`。
