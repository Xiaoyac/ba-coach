# 对话参考 chunk 与中介思考：实现、验收与上线

## 线上发布结果

- 最终版本：`20260917T105546Z`，网站 `https://bacoach.xyz/`。本地版本同步保留。
- 发布方式：代码更新，`-SkipDatabaseTasks`；不进行数据库迁移、不导入知识库、不上传本地测试数据。使用部署前已通过的 144 项针对性测试与前端构建；没有声称执行完整后端测试套件。
- 后端、前端、PA 推送 worker 均为 active；健康接口正常，未登录访问片段接口返回 401。
- 在正式网站加载的资源上，两套合成浏览器验收脚本均在 1440 / 390 / 360 宽度通过；所有测试 API 被拦截，没有向生产创建测试账号或对话。截图在 `.test-tmp/knowledge-references-online/`。
- 真实模型单次合成问题探测：模型 `deepseek-v4.1-flash`；中介 `completed / guided`，放行 1 个合成片段，返回独立思考 1271 字符；处理总耗时 **9939 ms**，现有超时上限 **12 s**。这是一例功能验证，不是平均延迟或准确率统计。
- 真实模型探测仅读生效中介提示词，不写生产数据库，不打印思考正文。首次运行因应用用户无权读取环境文件而失败；随后由已有服务器管理权限加载生产配置，并核验数据库为 MySQL、V2、启动维护关闭后成功执行。没有修改环境文件权限。

## 后续补充：四项折叠调试面板

- 默认只显示倒三角「调试详情」。展开后显示回复深度思考、路由深度思考、对话参考 chunk、中介节点深度思考四项；手机采用两列排列。
- 已记录且未召回任何知识片段时，参考 chunk 和中介思考两个面板均显示字面值 `null`。旧回复缺失快照时显示 `null` 并附上未记录说明，不把未知历史当成确定的零召回。
- 中介调用在同一次请求中开启原生思考模式，保存服务商返回的独立 `reasoning_content`，并显示模型与中介处理总耗时。总耗时不是纯思考时长；不存在的思考返回 `null`，不以使用建议替代。
- 不增加请求次数，但原生思考可能增加推理 token 和耗时；仍遵守原中介超时上限，超时不追加调用、不放行未经中介确认的片段。
- 思考正文通过独立调试数据通道进入消息快照，不进入普通指标日志、trace 或回复提示词。接口继续校验当前用户对消息的所有权。
- 增加 `backend/tests/test_mediator_reasoning.py`；相关后端测试共 **144 项通过**。前端构建通过，两个浏览器验收脚本在 1440 / 390 / 360 三种宽度均通过，包含折叠入口、四项切换、双 null、权限、流式 ID 补全与思考内容隔离。
- 本次涉及的补充文件：`backend/app/knowledge_mediator.py`、`backend/app/knowledge_references.py`、`backend/app/graph/nodes.py`、`backend/app/providers/{base,deepseek,doubao}.py`、`frontend/components/{ReasoningDetails,KnowledgeReferenceDetails}.tsx`、`frontend/lib/conversations.ts`。
- `backend/scripts/check_mediator_reasoning.py` 使用合成问题及合成片段验证生产模型，仅读取生效中介提示词，不访问真实用户对话、不写数据库、不打印思考正文或密钥。

## 本次调整

- 在两个深度思考入口旁增加「对话参考 chunk」，沿用浅色与黑金主题、原有按钮和折叠面板风格，不改变聊天业务流程。
- 展示本轮原始召回与传给回复模型的两组片段，包括正文、来源、ID、分数，以及检索、中介处理状态。
- 每条回复保存独立快照；后续知识库修改或新一轮检索不会改变旧回复的记录。
- 不追加任何 LLM 调用，不重新检索；点击按钮才请求片段，避免整段历史携带大量正文。

## 实现方式

1. `backend/app/graph/nodes.py` 在安全上下文检查前保留召回列表，在中介处理后保存实际传给模型的列表。快照生成逻辑位于 `backend/app/knowledge_references.py`。
2. `backend/app/conversation_store.py` 利用现有 `AIExecutionEvent.event_metadata`，在保存回复的同一事务中关联 `assistant_message_id`。本次不新增数据库表或列。
3. `backend/app/routes/conversations.py` 增加 `GET /api/conversations/messages/{message_id}/knowledge`：先校验消息属于当前登录用户的现存对话，再读取对应生成事件；返回 `Cache-Control: private, no-store`。
4. `backend/app/schemas.py`、`frontend/lib/api.ts` 为历史消息带上持久化 ID，保证文本不变时也能同步新增 ID；`frontend/lib/conversations.ts` 提供按需读取客户端。
5. `frontend/components/ReasoningDetails.tsx` 增加第三个独立入口，`KnowledgeReferenceDetails.tsx` 处理加载、错误重试、空结果、历史缺失和片段展示。纯文本渲染，不执行片段中的 HTML。

## 阅读边界与成本

- “传给回复模型”仅代表本轮生成请求包含这些材料，不证明回复实际引用，更不代表内容必然正确。检索分数不是准确率或置信度。
- 中介超时、异常、关闭，以及安全上下文加载失败，均维持不放行原始片段的规则。调试快照不会重新进入提示词。
- 若校验器拦截或改写回复，会提示面板展示的是原生成请求的参考材料。
- 旧回复、开场白及未进入该检索流程的回复没有快照时，显示“未记录”，不会伪造历史。未完成/中断回复不保证有快照。
- 不展示完整系统提示词、用户档案或长期记忆；仅保存知识库片段及少量处理状态。
- 代价是生成事件 JSON 存储量增加；原始与放行列表有重合时会重复保存。按需接口多一次读取请求，但不增加模型推理次数。
- 所有用户只能查看自己对话的片段；本次没有新增管理员跨用户查看能力。

## 验收结果

后端命令：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_knowledge_references.py tests/test_knowledge_mediator.py tests/test_conversations.py tests/test_message_timing_0917.py tests/test_graph.py -o addopts='' -q --basetemp=../.test-tmp/chunks-tests-03 --tb=short -p no:cacheprovider
```

结果：135 项通过，1 项现有依赖弃用警告。覆盖成功筛选、拒绝、超时、禁用、异常输出、空召回、意图门控跳过、安全上下文失败、流式与非流式持久化、消息间隔离、重复查看不触发模型/检索、未登录/跨账号/删除后不可访问。

前端：`npm run build` 通过。

浏览器命令：

```powershell
node infra/qa/knowledge-references.cjs
node infra/qa/reasoning-split-0917.cjs
```

两套脚本均在 1440、390、360 像素宽度通过。检查三个面板的切换、键盘操作、延迟加载、重试、超时状态、历史缺失、刷新、流式回复保存后的 ID 补全。原有回复/路由思考分离、复制正文和实时路由行为回归通过。

视觉检查：浅色/黑金主题、按钮换行、正文对比度、长文本换行、面板内部滚动、输入区不被遮挡；无横向溢出或浏览器运行错误。截图位于 `.test-tmp/knowledge-references/`。

浏览器验收使用拦截 API 的合成数据，并非线上真实模型端到端测试。后端测试使用隔离 SQLite 与替身模型。当前工具环境缺少 `js_repl`，使用项目脚本运行 Playwright，没有声称完成交互式技能运行时验收。

## 初次本地验收环境说明

本地预览地址：http://127.0.0.1:3000/ 。前后端已重启加载本次代码。

首次实现只在本地验收，随后按用户要求发布，见开头发布结果。未上传本地测试数据库。本地预览知识库为空，已有历史回复通常会显示未记录；成功召回的展示效果由上述合成验收覆盖。
