# 管理员混合检索中介

## 2026-10-14：架构与中介节点独立控制（已部署）

管理员聊天控制区新增“是否启用中介节点”。下表适用于已有和新建会话：

| 账号 | 检索架构 | 中介节点默认值 |
| --- | --- | --- |
| 普通用户 | 新架构（混合检索与重排） | 开启 |
| 管理员 | 保留该会话原有的新／老架构选择 | 关闭；主动开启后按会话保存 |

关闭中介只跳过中介模型调用，检索仍运行，检索结果直接供主回复使用；原有记录拒绝规则和上下文加载失败时的材料限制继续生效。开启后，老架构调用原中介，新架构调用快速中介。无材料或命中既有无需知识规则时，依然可以跳过实际模型调用。

- 原 `knowledge_mediator_enabled` / `mediator_enabled` 继续仅表示架构，避免旧数据含义改变。
- 新 API 字段为 `knowledge_mediator_node_enabled`，管理员通过 `PATCH /api/conversations/{session_id}/mediator-node` 的严格布尔 `enabled` 保存。
- 独立新表 `conversation_mediator_node_settings` 不改写历史架构字段。V2 线上关闭了启动建表，部署前需加载线上环境并运行 `backend/scripts/create_mediator_node_settings.py --apply`，该脚本只创建／核验这一张表，不回填用户数据。管理员缺少记录时解析为关闭；普通用户每轮强制新架构＋开启，不能通过请求 metadata 绕过。
- 保存需管理员身份及会话所有权；生成中拒绝切换，保存后从下一轮生效。切换架构不会修改节点开关。
- 主回复事件 metadata 保存 `knowledge_mode`、`knowledge_mediator_node_enabled` 和 `knowledge_mediator`；中介 trace/event 也标记架构及 `node_enabled`。对比实验应按这两个维度分组，并结合请求用量、总耗时与首字耗时；无材料导致的跳过不等于关闭节点。
- 验证：67 项相关后端测试、前端生产构建与类型检查、桌面／手机模拟浏览器测试通过。已部署到 `/opt/bacoach/releases/20261014T111258Z`，保留前版 `/opt/bacoach/releases/20261014T033809Z`。仅合入 13 个本次运行时文件及独立建表脚本，保留线上目标卡复用和账号界面修改。独立验收账号通过公网四组合切换、持久化、普通用户默认及权限验证；正式服务的一轮真实 SSE 回复已持久化，中介跳过与架构标签吻合。公网桌面／手机 UI 使用模拟数据验证通过；这不代表已完成真实用户对照实验。

## 2026-10-08 历史实现

管理员开启“新架构”后的链路改为：混合检索 → 现有重排 → 快速知识中介 → 主回复。
普通用户和老架构的选择规则保持原样。没有召回材料或命中既有拒绝记录规则时，不额外调用中介模型。

## 模型与延迟

- 默认 `KNOWLEDGE_HYBRID_MEDIATOR_MODEL=deepseek-v4.1-flash`，复用现有 compatible endpoint 和凭据，独立于主回复及 Router 的思考开关。
- 默认请求 `thinking.type=disabled`；若配置为现有 Ark Kimi K3，provider 映射为 `reasoning_effort=low`。
- 单次中介上限为 `min(KNOWLEDGE_MEDIATOR_TIMEOUT_SECONDS, 8)` 秒，无 SDK 重试。
- 返回预算沿用现有中介限制 512 token；K3 保留 provider 已有的 reasoning/output 预算适配。
- 超时、截断、无效 JSON 或无效来源均不放行片段，主回复仍可使用对话上下文继续。不会重试中介或自动退回原文直通。
- 8 秒是中介节点的等待上限，不是检索、重排、Router 和主回复的总耗时承诺。

## 调试与兼容

`knowledge_references` 升级至 version 5，新增可选 `mediator_response_content`。
原始返回与供应商独立返回的 `mediator_reasoning_content` 分开存储，显示于“调试详情 → 中介节点使用建议内容”。
无原生思考内容时不伪造思考。无效或被截断的原始结果也可查看，但不会注入主回复。
返回内容不进入中介 trace/log，仅沿现有逐消息知识详情保存、鉴权读取；已有管理员分享版本沿用其明确的调试发布规则，普通分享仍只有正文。

数据库/API 的历史字段名 `mediator_enabled`、`knowledge_mediator_enabled` 及内部 `knowledge_mediator_bypass` 继续兼容旧会话：它们现在选择老/新检索架构，不表示新架构取消中介。无需数据迁移。

## 验证

- 后端 100 项相关测试通过：实际 provider HTTP 参数、完整 graph、普通/流式回复的逐消息保存、所有权、超时取消、拒绝记录、无效引用及老架构回归。
- 前端 TypeScript 检查通过；浏览器测试覆盖桌面/手机、原始结果与原生思考分开显示、文本安全渲染及架构选择。
- 在现有服务器供应商连接上使用合成资料做两次独立试调用：默认模型返回 `deepseek-v4-1-flash`，中介耗时 1495/1601 ms，reasoning_tokens 均为 0，返回结果均通过来源校验。这不是全量生产延迟统计。
- 已按用户后续部署请求上线至 `/opt/bacoach/releases/20261008T091442Z`，前一版本 `/opt/bacoach/releases/20261008T084742Z` 保留以便回滚。以线上为基准仅合入 10 个中介相关源文件变化，Chat.tsx 仅合入架构说明文案，未夹带另一任务的布局修改。
- 发布包构建通过；使用线上数据库实际中介提示词及合成资料试调用约 1760 ms、reasoning_tokens=0，快照含原始返回。公网桌面与手机浏览器调试展示测试通过，后端/前端服务及公网访问正常。
