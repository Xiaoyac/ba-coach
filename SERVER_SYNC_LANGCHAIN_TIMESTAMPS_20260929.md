# 服务器 LangChain 与消息时间戳改动同步记录

## 同步来源与范围

- 来源：服务器 `/opt/bacoach/current`，本次检查解析为 `/opt/bacoach/releases/20260929T122040Z`。
- 服务器发布目录没有 `.git`，因此按实际源文件比较，不把它当作一个新的 Git 提交。
- 本地同步前 HEAD：`6bbcdea235cf40ca71bf30442075262b69112524`。
- 比较了 238 个服务器源文件，同步其中 15 个新增或更新文件。
- 本地 `config.py`、`pa_push.py`、部署脚本及未完成的站内提醒文件保持不变。服务器中的 `config.py` 和 `pa_push.py` 与本地 HEAD 一致，无需用旧版本覆盖本地未提交工作。
- 服务器文件使用 CRLF；本地遵照 `.gitattributes` 统一为 LF。逐文件核对去除这一换行差异后，内容与下载的服务器版本一致。
- 本次只同步到本地，没有提交、推送或部署。

## 修改文件与原因

| 文件 | 服务器新增或修改的内容 |
| --- | --- |
| `backend/app/context_pipeline.py`（新增） | 用 LangChain 的角色消息、模板和历史裁剪统一准备主回复及危机回复输入，记录上下文管线统计。 |
| `backend/app/conversation_time.py`（新增） | 提供服务器 UTC 时钟、北京时间显示、时间背景和带时间的历史文本。 |
| `backend/app/conversation_store.py` | 历史读取保留消息时间，保存消息时接收服务器时间，并持久化上下文管线统计。 |
| `backend/app/graph/nodes.py` | 接入上下文管线，为主回复、抽取和摘要提供时间信息，将助手时间传到流式完成事件。 |
| `backend/app/graph/state.py` | 在图状态中携带用户及助手消息时间。 |
| `backend/app/pre_reply_routing.py` | 路由历史保留时间，并将服务器时间背景传给 Router。 |
| `backend/app/router_agent.py` | 接收路由调用的时间背景。 |
| `backend/app/routes/chat.py` | 统一请求接收时间，贯穿数据库、图执行、普通响应和流式元数据。 |
| `backend/app/routes/conversations.py` | 会话加载和恢复保留消息时间。 |
| `backend/app/schemas.py` | 增加消息及响应时间字段，将消息时间规范为 UTC。 |
| `backend/app/session.py` | 使用统一的历史裁剪函数，按实际角色处理溢出窗口。 |
| `backend/requirements.txt` | 显式声明 `langchain-core>=1.6.2,<2`。 |
| `frontend/components/ConversationWorkspace.tsx` | 显示发送中的临时时间，再用服务器时间替换；会话恢复比较时间字段。 |
| `frontend/components/MessageRow.tsx` | 在有正文、有有效时间的消息下显示北京时间。 |
| `frontend/lib/api.ts` | 增加时间字段，接收流式 `done` 事件中的助手时间。 |

另在本地更新了 `backend/tests/test_conversations.py` 的已有测试：原断言尚未包含新增的 `created_at` 字段；现在验证开场消息时间为 UTC，且回复后重新加载会话时该时间不变。这是测试兼容调整，未改动服务器同步来的运行时代码。

## 功能边界

1. LangChain 在这里负责本地消息组装和按消息条数裁剪，没有新增模型调用。模型请求仍通过原来的供应商 SDK 发出。
2. `trim_messages` 的计数器是 `len`，当前仍是消息条数限制，不是整体 token 预算，也没有新增滚动摘要。
3. 主回复和危机回复经过新管线；字段抽取仍将带消息 ID 的历史资料序列化到一条 `user` 消息中。这次同步没有专门修复此前发现的抽取器复制历史、输出截断问题。
4. 模型输入明确区分消息时间与事件发生时间；未知时间标为未知，不从时间经过推断活动已经执行。网页按北京时间显示。
5. 本地 `langchain-core` 为 1.6.5，服务器为 1.6.3，两者均符合服务器新增的依赖范围；本次没有修改依赖安装版本。

## 验证

- 15 个文件已与服务器下载快照逐项比对，只有仓库规定的换行转换。
- 原有 13 个未提交或未跟踪文件已建立哈希保护清单。
- 上下文与时间专项检查通过：角色顺序、重复文本不混淆、本轮输入完整保留、Prompt 中 JSON/花括号不变、历史条数上限、UTC 转北京时间、跨日和未知时间处理。
- 前端 TypeScript 检查通过。
- 后端完整回归首次结果：1870 通过、2 跳过、1 失败。唯一失败是开场消息新增 `created_at` 后旧响应字典断言未更新。
- 更新上述测试断言后，相关 `test_conversations.py` 全部 36 项通过；没有剩余已知测试失败。未重复运行无改动的其余测试。
- `git diff --check` 通过。
- 同步完成后重新核对服务器发布路径与 15 个文件哈希，均未在下载期间改变；再次验证原有 13 个本地文件哈希，均保持不变。

## 备份

同步前文件、未提交补丁、服务器原始文件以及校验清单位于项目旁的：

`../诊断导出/服务器同步备份-20260929T122040Z/`
