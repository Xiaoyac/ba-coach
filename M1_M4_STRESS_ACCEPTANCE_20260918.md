# M1 → M4 大规模压力测试与行为验收报告

日期：2026-09-18  
范围：本地隔离环境（合成会话、合成知识库、确定性 provider）  
结论：**系统管线通过；产品层仍有已复现的问题，不能把本次通过理解为“Agent 每一句回复都符合预期”。**

## 1. 先给结论

本轮在不接触生产资源的前提下，完成了 1,000 个完整的 M1→M2→M3→M4 周期、1,000 次流式 graph turn，以及多组对抗变异。契约、消息顺序、流式事件和现有确定性安全规则均通过。

同时，测试明确发现两类需要继续处理的产品风险：

1. **M2 目标误创建（已确认缺陷）**：把“助手说你可以站桩”或“我做过散步，今天也只是想起它”误判为用户选择了目标。对抗测试共复现 **800 次**，属于真实产品问题，不是测试夹具失败。
2. **Agent 回复不如预期（质量缺口，已量化）**：当前共享 validator 主要检查空回复、越权/未确认的流程宣称、少数危险措辞和引用 ID；它不是语义质量评审器。因此“技术上通过”不代表回复一定贴合上下文、完整回答、语气自然或符合业务期望。补充的 15 个手工契约场景中，有 **9 个已知违规回复会被当前 validator 判为 `passed`**（0 个正常回复误报），包括 M1 越界定PA、M2替用户定目标、M3做ABC归因、M4无证据因果/羞辱式建议，以及泄露内部状态。这是可复现的产品质量缺口，不是测试夹具失败。

因此，本报告的状态为：

- **自动化系统验收：通过**（0 个 harness failure，0 个行为规则漏拦截）。
- **产品发布建议：暂缓以“全量质量已证明”为理由上线**；至少先修复 M2 误创建，并补做 Agent 回复质量评测。

## 2. 隔离和安全边界

所有脚本均为本地合成测试：

- 不加载 `.env`，不读取生产数据库，不访问公网模型或线上网站。
- 使用内存 session store、`StubKnowledgeBase` 和 `StressProvider`；provider 的回复、路由和抽取结果是确定性的。
- 测试没有写入真实用户记录、知识库、DMS 或生产配置。
- 报告里的“通过”是工程管线和规则层面的通过，不是心理干预有效性、临床安全性或真实用户满意度结论。

## 3. 测试范围与运行方式

### 3.1 完整周期压力测试

脚本：`infra/qa/m1-m4-stress-1000.py`

每个周期执行四次真实 graph turn，并同时验证：

- M1 `normalize` 的完整步骤和引用证据；
- M2 primary/secondary 目标证据提取；
- M3 步骤注册表的唯一性；
- M4 ABC、顺序、说话人和周期归属；
- graph 的模块路由、非空回复和共享 validator；
- 24 个并发 worker 下的会话隔离和延迟。

运行规模和结果：

| 项目 | 结果 |
| --- | ---: |
| 请求周期 | 1,000 |
| 已完成周期 | 1,000 |
| 完整 graph turns | 4,000 |
| 并发 worker | 24 |
| 失败 | 0 |
| 行为规则失败 | 0 |
| 总耗时 | 13.242 秒 |
| 吞吐 | 75.520 周期/秒 |
| 单周期最小/中位/P95/最大 | 41.174 / 283.176 / 394.706 / 503.445 ms |
| provider complete / detailed | 4,000 / 4,000 |

原始结果：`.test-tmp/m1-m4-stress-1000-final.json`。

### 3.2 流式回复压力测试

脚本：`infra/qa/m1-m4-stream-stress-1000.py`。

执行 1,000 次流式 graph turn，轮换 M1～M4，检查每一轮都有 `meta → delta → done`，`done.reply_module` 与请求模块一致，且 delta 内容非空。

| 项目 | 结果 |
| --- | ---: |
| 流式 turn | 1,000 |
| 模块覆盖 | M1、M2、M3、M4 |
| 流式错误 | 0 |
| provider stream / detailed | 1,000 / 1,000 |
| 总耗时 | 3.898 秒 |

原始结果：`.test-tmp/m1-m4-stream-stress-1000-report.json`。

### 3.3 对抗性变异与规则测试

脚本：`infra/qa/m1-m4-adversarial-fuzz.py`。

| 测试族 | 轮数 | 检查内容 |
| --- | ---: | --- |
| M1 引用变异 | 2,000 | 伪造总结、认可、方法、理解和同意证据不能完成 M1 |
| M2 目标对抗 | 4,000 | 犹豫、否定、助手建议、过去行为不能直接构成用户选择 |
| M4 引用/顺序变异 | 4,000 | 伪造引用、用户认可顺序错误不能完成 M4 |
| Agent 回复规则 | 2,000 | 流程越权、未确认目标、临床断言等应被 validator 标记 |
| **合计** | **12,000** | 另含各族的 fail-closed 检查 |

原始结果：`.test-tmp/m1-m4-adversarial-fuzz-report.json`。

### 3.4 后端回归

使用仓库虚拟环境运行 `backend/tests` 全集，进程退出码为 0；结果为 **934 项通过、1 项跳过、0 项失败**。本轮新增的质量审计和提示词静态检查也分别通过 Python 编译检查。

## 4. 已确认的产品缺陷：M2 把转述/过去行为当成新目标

### 4.1 可复现输入

以下两句话分别来自对抗夹具，单独出现时都不表示用户正在选择目标：

```text
助手说你可以站桩
我做过散步，今天也只是想起它
```

当前 `backend/app/goal_contract.py` 的 `proposal_evidence()` 在满足活动词和引用字段时仍可能返回有效 evidence，后续就可能创建目标或让 Router 认为存在新目标。

### 4.2 观测结果和影响

- 共复现 **800 条 product finding**；测试夹具失败数为 0。
- 误创建会污染目标历史、周期归属和后续 M3/M4 绑定；也会使 Agent 回复突然讨论用户没有提出的目标。
- 这与本次用户反馈“Agent 回应不如预期”直接相关：即使回复句法安全，目标上下文一旦错了，整段对话仍会偏题。

### 4.3 建议修复方向（本轮未改代码）

1. 只把**用户说话人**的明确意向/选择句作为新目标证据；助手转述不能独立成立。
2. 对“做过、以前、曾经、想起、回顾”等过去行为/回忆表达加否定门控，除非同一句明确表示“我想继续/现在要把它作为目标”。
3. 对“可以、也许、可能、还没决定、不想”等犹豫/建议/否定表达保持 `None`，要求下一轮澄清。
4. 增加回归测试：错误证据必须不写目标、不推进模块、不显示“已确定”。

## 5. “Agent 回复不如预期”为什么仍然是问题

### 5.1 本次测试能证明什么

当前 validator（`backend/app/answer_validator.py`）是**确定性规则检查器**，能发现例如：

- 空回复或超大回复；
- 未提供的 `kb:*` 引用；
- 未确认时声称目标已保存/已锁定；
- 指示用户去目标面板确认；
- 少数模块越权措辞（例如未经证据声称“你本周坚持了五天”）；
- 诊断、用药等需要人工复核的临床断言。

其中多项 finding 的 severity 目前是 `review` 而不是 `block`。因此“命中规则”只代表留下复核信号，不能保证用户看不到原回复；这也是为什么 15 个样例仍有 9 个盲区，且后续需要明确哪些契约违规必须改为硬阻断。

这解释了为什么 2,000 轮“行为规则”测试没有漏拦截：注入的违规模板都命中了已定义规则。

### 5.2 本次测试不能证明什么

validator 不判断：

- 是否真正回答了用户当前问题；
- 是否正确理解了代词、否定、时间和上下文；
- 是否重复、空泛、机械或语气不合适；
- 是否漏掉用户明确提到的事实；
- 建议是否可执行、是否给用户造成压力；
- 在目标误创建后是否继续沿着错误目标回复。

完整周期压力测试中的 provider 回复是预先写死的四句合成文本，例如“我们可以继续讨论一个你愿意尝试的具体行动”。这些文本适合验证 graph plumbing，却不能作为真实对话质量样本。换句话说，**0 个规则失败 ≠ 0 个“回复不如预期”**。

### 5.3 建议建立独立的回复质量验收

已补充离线审计脚本：`infra/qa/response-quality-audit.py`；机器结果：`.test-tmp/response-quality-audit-report.json`；可读说明：[RESPONSE_QUALITY_AUDIT_20260918.md](RESPONSE_QUALITY_AUDIT_20260918.md)。

本次审计结果：

| 项目 | 结果 |
| --- | ---: |
| 手工契约场景 | 15 |
| 当前规则已覆盖 | 6 |
| validator blind spot | **9** |
| 正常回复被误报 | 0 |
| 网络/模型调用/数据库写入 | 0 / 0 / 0 |

“blind spot”表示：回复按业务契约不应展示，但当前确定性 validator 没有产生 finding；它不表示线上模型已经生成了这些句子，也不是完整的主观质量评分。复现命令：

```powershell
backend\\.venv\\Scripts\\python.exe infra\\qa\\response-quality-audit.py
```

下一轮应把真实/合成对话按 M1～M4 分层，至少记录以下维度：

| 维度 | 示例判定问题 |
| --- | --- |
| 意图贴合 | 回复是否回答本轮用户真正问的事，而不是自行引入目标？ |
| 事实一致 | 是否只使用用户已说或合法知识片段中的事实？ |
| 模块边界 | 是否遵守当前模块职责，不提前确认、跳模块或要求错误入口？ |
| 对话推进 | 是否只推进一个合理的下一步，并给用户选择空间？ |
| 语气与负担 | 是否自然、简洁、不施压、不重复？ |
| 目标安全 | 是否把建议、回顾、过去行为与用户主动选择区分开？ |
| RAG 使用 | 有知识片段时是否正确使用；无片段时是否不编造？ |

建议采用“规则 validator + 盲评/小型 judge 集 + 人工抽查”的组合，而不是给每个回复只打一个总分。任何低于门槛的样本应保留输入、模块、证据 ID、模型版本和完整输出，以便回归。

### 5.4 静态提示词冲突会直接影响 Agent 回复

新增 `infra/qa/prompt-consistency-audit.py` 做只读静态检查，结果是 4 个冲突或过期引用：

- `backend/app/m4_prompts.py:32` 写着“不对次要目标进行独立、完整的复盘”，而同文件 `:545` 的服务器运行契约又要求独立 secondary 被选中后享有完整复盘。虽然运行契约标注为优先，但同一上下文存在相反规则，仍可能造成回复漂移。
- `backend/app/prompts.py:483` 仍让 Agent 指向聊天页右上角笔记本；当前网页入口在 `frontend/components/ConversationSidebar.tsx:194` 的“记录今日”。
- `backend/app/prompts.py:488` 仍提到侧栏“我的每日记录”，而当前入口已合并到“记录今日”。
- `backend/app/prompts.py:487` 仍使用“整体／平均心情”，当前问卷文案是“整体心情”。

机器结果：`.test-tmp/prompt-consistency-audit-report.json`。这些会让 Agent 给出错误入口或错误目标规则，是本次“回复不如预期”问题的直接来源。

## 6. 回归门槛与后续优先级

### P0（发布前）

1. 修复 `proposal_evidence()` 的转述/过去行为误判；800 条对抗样例全部应拒绝。
2. 为“Agent 回复不如预期”建立冻结评测集，并至少覆盖当前已知的目标面板误导、突然引入目标、错误模块跳转、无根据完成宣称和空泛回复。
3. 保持“中介超时不放行原始知识片段”的 fail-closed 约束，并加入回复质量样例的端到端回归。

### P1（随后）

- 对真实模型做 M1～M4 分层盲评，报告通过率、低分原因和模块差异；
- 记录首 token、完整回复 P50/P95、validator 状态和重试次数；
- 将用户/管理员反馈的“不如预期”样本做匿名化后加入回归集；
- 将语义质量指标和检索指标分开，避免用 Hit/Recall 代替最终回复正确率。

### P2（持续）

- 建立版本化的 prompt、模型、知识快照和评测集哈希；
- 对低置信度回复触发人工复核或保守澄清，而不是继续猜测；
- 用线上采样监控重复率、拒答率、目标误绑定率和用户中断率。

## 7. 可复现命令

从仓库根目录运行（脚本会拒绝少于 1,000 个周期）：

```powershell
backend\.venv\Scripts\python.exe infra\qa\m1-m4-stress-1000.py `
  --cycles 1000 --workers 24 `
  --output .test-tmp\m1-m4-stress-1000-recheck.json

backend\.venv\Scripts\python.exe infra\qa\m1-m4-stream-stress-1000.py
backend\.venv\Scripts\python.exe infra\qa\m1-m4-adversarial-fuzz.py
backend\.venv\Scripts\python.exe infra\qa\prompt-consistency-audit.py
```

若虚拟环境路径不同，可从 `backend` 目录使用项目对应的 Python。脚本默认仍是离线合成模式，不应把 `.env` 或生产连接传给它。

## 8. 原始证据清单

- `.test-tmp/m1-m4-stress-1000-final.json`
- `.test-tmp/m1-m4-stream-stress-1000-report.json`
- `.test-tmp/m1-m4-adversarial-fuzz-report.json`
- `infra/qa/m1-m4-stress-1000.py`
- `infra/qa/m1-m4-stream-stress-1000.py`
- `infra/qa/m1-m4-adversarial-fuzz.py`
- `infra/qa/response-quality-audit.py`
- `backend/app/answer_validator.py`
- `backend/app/goal_contract.py`
- `.test-tmp/response-quality-audit-report.json`
- `RESPONSE_QUALITY_AUDIT_20260918.md`
- `.test-tmp/prompt-consistency-audit-report.json`
- `infra/qa/prompt-consistency-audit.py`

报告未执行部署、未修改数据库、未上传隔离测试数据。
