# 中介超时误报与预算修复

## 上线结果

- 版本 `20260917T113924Z` 已发布到 https://bacoach.xyz/ 。
- 发布后读取的生效配置：20 秒、low、V2、启动数据库维护关闭。
- 后端、前端、PA 推送服务均 active，健康接口正常；已核对核心文件哈希与本地一致。
- 发布后再次在实际 Python 3.10.12 上触发异步超时，正确分类为 `timeout`，放行数 0。
- 发布后真实模型合成探测：`deepseek-v4.1-flash`，6.600 秒完成，放行 1 个合成片段，独立思考返回 774 字符；无生产数据写入。这是单次探测，不是延迟保证。
- 正式站点资源上的模拟浏览器验收覆盖 1440 / 390 / 360 宽度，包含新增错误原因中文说明、空结果、独立面板和流式同步。API 均被测试脚本拦截，没有创建生产测试对话。
- 本次使用代码发布模式，未执行数据库迁移、知识库导入或本地测试数据上传。

## 调整内容

1. 同时捕获 `asyncio.TimeoutError` 和内置 `TimeoutError`，兼容生产 Python 3.10 与本地较新版本。此前真实异步超时在生产被归类为“输出异常或服务错误”。
2. 只给中介原生思考指定 `reasoning_effort=low`；正文回复与后台 Router 的推理设置不变。独立思考仍保存到消息快照。
3. 中介默认等待上限由 12 秒改为 20 秒；仍只有一次有上限的调用。中介使用低推理参数的请求关闭 SDK 自动重试，避免请求在截止时间内反复重试。
4. 输入 JSON 使用紧凑编码，不删除或裁剪已组装的安全事实、来源和确认状态。超长上下文仍拒绝处理，不通过删掉安全信息来适配预算。
5. 提示词要求专注适用性与安全判断，建议正文优先控制在 200 字、必要注意事项通常不超过 3 项，避免代写教练回复或反复复述上下文。最终正文仍须符合原 JSON 契约。
6. 增加空输出、截断、无效 JSON、字段不合契约、引用校验失败、上下文过长的独立原因与前端中文说明。

## 不改变的安全边界

- 超时、异常、输出截断、格式错误、引用不存在的 ID，均不放行任何原始片段。
- 不修改模型无法确认的用户事实；思考文本不进入主回复提示词或普通服务日志。
- 没有片段时，参考 chunk 和中介思考仍显示 `null`。
- 历史失败消息不回写、不重新检索；新规则对部署后的请求生效。
- 本次不迁移数据库，也不上传或导入本地测试资料。

## 验证

### Python 3.10 实际运行时

将候选模块单独载入服务器 Python 3.10.12，使用实际 `asyncio.wait_for` 与慢速替身触发超时，而不是手动抛出某个异常类。结果正确记录 `timeout`，放行数为 0。

### 新旧方式对比

使用生产模型和生效中介提示词，但问题、历史、知识片段全部为合成内容；不访问真实用户对话，不写数据库。旧版和候选版交替运行，关闭 SDK 重试，以避免重试干扰比较。

| 场景 | 旧版（12 秒、原推理设置） | 新版（20 秒上限、低推理与简洁输出） |
| --- | --- | --- |
| 短问题、2 个片段 | 12.012 秒，超时且误报，放行 0 段 | 7.546 秒，完成，放行 2 段，返回独立思考 |
| 带四条合成历史、2 个片段 | 12.009 秒，超时且误报，放行 0 段 | 7.555 秒，完成，放行 2 段，返回独立思考 |

这只是两个样本的功能与延迟对比，不是质量评测、平均延迟承诺或“100% 稳定”的证据。无法把改进单独归因于某一个参数，后续仍需监测超时率与筛选正确性。

### 自动测试

相关后端 **153 项通过**，1 项依赖弃用警告；前端 `npm run build` 通过。

覆盖真实计时器超时与取消、无追加调用、低推理仅影响指定请求、安全事实保留、输出截断拒绝放行、空输出、错误 JSON、错误字段与证据引用，以及既有聊天/消息权限/模型错误处理回归。

命令：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_mediator_reasoning.py tests/test_knowledge_mediator.py tests/test_knowledge_references.py tests/test_conversations.py tests/test_graph.py tests/test_doubao_errors_0917.py -o addopts='' -q --basetemp=../.test-tmp/mediator-timeout-fix-02 --tb=short -p no:cacheprovider
```

## 权衡与监测

- 20 秒是最大等待容错，并不意味着每轮都等 20 秒；极端慢请求可能比此前多等待最多约 8 秒。
- 低推理强度可能降低复杂材料判断的充分性，不能只监测“放行率”；应同时抽查误放行与误拒绝。
- 建议跟踪 `knowledge_mediator` 的 `reason`、`duration_ms`、`input_chars`、`raw_chunk_count`、`approved_chunk_count`，分开统计 `timeout`、格式问题与正常的 `no_applicable_evidence`。
- 可以通过 `KNOWLEDGE_MEDIATOR_TIMEOUT_SECONDS`、`KNOWLEDGE_MEDIATOR_REASONING_EFFORT` 调整，修改后重启后端。当前默认分别为 20 和 low。

## 修改文件

后端：`app/knowledge_mediator.py`、`app/config.py`、`app/providers/{base,deepseek,doubao}.py`、`.env.example`、`tests/test_mediator_reasoning.py`。

前端：`components/KnowledgeReferenceDetails.tsx`（错误原因中文说明）。

验证脚本：`infra/deploy/verify_mediator_timeout_fix.py`（合成、只读探测）。
