# BA Coach 数据库与状态机缺陷整改说明

原始整改：2026-08-31 · 文档复核：2026-09-01

## 结论

本次整改覆盖提出的 8 项缺陷。7 张外部业务表保持原样，没有执行 `ALTER TABLE`、`DROP TABLE`，也没有重写线上已有的 2 条风险记录。缺失的会话步骤、PA 周期和 AI 遥测由 4 张应用自有 sidecar 表补齐。

权威状态现在只有一个：每段对话的 `conversation_runtime_states.module`。`user_profile.current_module` 仅保留为外部系统的历史兼容列，不再由对话流程读写；个人档案显示的模块由最新一段对话的运行态即时派生。

## 1. 数字编码统一（P0）

代码已按线上 MySQL 列注释统一，不再使用模型自行想象的编码：

- 所有认可/理解程度：`0=不认可/不理解`、`1=部分认可/理解`、`2=认可/理解`。
- `module_four_record.execution_result`：`1=执行成功`、`2=未执行`、`3=执行受阻`、`4=部分完成`。
- `module_four_record.review_decision`：`1=继续原目标`、`2=更换目标`、`3=调整目标`。
- `risk_monitoring.risk_expression_type`：`1=自伤`、`2=自杀`、`3=自杀未遂`。
- “已有具体自杀计划但尚未实施”按 `2` 保存，计划细节原样放入 `risk_context`；绝不把它写成 `3=未遂`。

提示词定义和写入校验都从同一份 `clinical_fields.py` 读取，所以不会再出现“模型按一套编码输出、数据库按另一套语义保存”。越界值会被拒绝；`execution_result=3/4` 现在能正常落库。

线上只读审计发现 1 行旧制 `module_one_record.user_approval_level=5`。已通过幂等迁移按“旧制完全认可 → 新制认可”修正为 `2`；其余认可字段没有旧制越界值。风险分布保持为 1 行 `type=2`、1 行 NULL，未修改任何风险记录。

涉及文件：

- `backend/app/clinical_fields.py`
- `backend/app/clinical_store.py`
- `backend/tests/test_clinical.py`

## 2. 结构化模块子步骤（P1）

新增 `conversation_module_progress`，一段对话一行，包含：

- `module_1_steps`
- `module_2_steps`
- `module_3_steps`
- `module_4_steps`
- `active_cycle_id`

步骤值不是自由文本，而是服务器白名单中的稳定 key。Router Agent 每轮除了 `target_module`，还必须返回当前模块已完成的 `completed_steps`。服务器会：

1. 丢弃未知步骤；
2. 与已存步骤做单向累加，禁止回退；
3. 只有当前模块全部必需步骤完成，才允许真正跳转；
4. 把已完成步骤注入下一轮主 Agent System Prompt，让主 Agent 从第一个未完成步骤继续。

模块一步骤为：

- `core_problem_example`
- `depression_cycle_formulated`
- `ba_education_completed`
- `goal_setting_consent`

因此“一个具体事例 + 已结合经历完成循环/BA 教育 + 用户同意目标设定”即可进入模块二，不再要求穷尽背景；同时也不会只因用户一句“继续”而越级。

## 3. PA 目标周期（P1）

新增：

- `pa_cycles`：一轮 module 2 → 3 → 4 的目标周期；
- `clinical_record_cycle_links`：把外部 `module_two_record`、`module_three_record`、`module_four_record` 的记录 ID 关联到同一个周期。

没有直接给外部表加 `cycle_id`，因为这些表明确禁止本应用擅自改结构。

生命周期：

1. module 1 → 2 时创建第一轮周期；
2. module 2/3/4 抽取记录时，按 `active_cycle_id` 查找并更新该周期自己的记录；
3. module 4 → 2 时关闭旧周期并创建新周期；
4. module 3/4 读取 PA 卡时只读取当前周期的 module 2 记录；有周期但找不到关联记录时返回空，不会回退到“这个用户最新一条”而串错周期。

`interaction_status.goal_history` 同步保存每轮完整 PA 卡的轻量快照和 `cycle_id`，便于趋势与周期统计。

## 4. 消除双状态来源（P1）

运行时模块只认：

`conversation_runtime_states.module`

不再把每段对话的当前模块写到 `user_profile.current_module`。这样同一账户同时进行两段不同对话时，两者不会互相覆盖。

`user_profile.module1_done_flag` 仍保留，因为它表达的是用户级、单向的事实：“此人曾经通过模块一”，不是某一段对话现在在哪。个人档案与 `/api/auth/me` 的 `current_module` 从最新对话运行态派生；尚未建立任何对话时才显示注册初始值“开场”。

## 5. interaction_status 接入（P2）

每次成功持久化一轮对话后，现在会更新：

- `last_active_at`
- `consecutive_inactive_days`（活跃时归零）
- `total_interaction_count`
- `avg_weekly_interaction_frequency`
- `easy_stuck_modules`（各模块累计停留轮数）
- `longest_stay_module`

完成 module 4 → 2 的完整闭环时更新：

- `full_m2_m3_m4_cycle_count`
- `has_entered_closure_or_transition`

PA 卡完成时更新：

- `goal_history`

以下字段没有凭空填充：情绪趋势、躯体/认知下降信号、自我教练信心等。它们需要明确的量表或经过验证的推导规则；在没有数据依据时保持 NULL，比生成“看起来完整”的临床判断更安全。

## 6. 统一 AI 执行遥测（P2）

新增 `ai_execution_events`，统一记录：

- 阶段：`main_generation`、`risk_gate`、`module_router`、`clinical_extraction`、`module_summarizer`；
- provider、model；
- 总延迟；
- input/output/reasoning tokens；
- provider request ID、finish reason、error code；
- prompt 指纹版本；
- 估算成本、价格表版本；
- 阶段附加 metadata。

原 `conversation_messages` 上的主回复延迟字段继续保留，便于直接展示单条消息；`ai_execution_events` 是跨阶段统一审计表。

价格不硬编码，因为供应商价格会变、代理端点也可能采用不同计费。部署者通过：

```env
LLM_PRICE_PER_MILLION_JSON={"模型ID":{"input":每百万输入token美元价,"output":每百万输出token美元价,"reasoning":每百万思考token美元价}}
```

配置。未匹配的模型成本保持 NULL，不伪造为 0；每条记录保存配置哈希 `pricing_version`，以后改价仍可区分历史估算依据。

遥测写入失败只记日志，不会改变危机分流、主回复或模块跳转结果。

## 7. 删除对话的完整性

删除前会等待该对话正在运行的后台 Router Agent 完成，再取得对话锁。随后显式删除应用自有的：

- 对话消息；
- 运行态；
- 子步骤进度；
- PA 周期及周期链接；
- AI 遥测；
- 对话主记录；
- 内存中的会话。

显式删除是为了让没有开启 SQLite 外键级联的本地环境也行为一致。外部临床业务记录按用户归档，仍然保留；删除聊天记录不会偷偷删除临床档案。

## 8. 新增表与部署

新增应用自有表：

1. `conversation_module_progress`
2. `pa_cycles`
3. `clinical_record_cycle_links`
4. `ai_execution_events`

应用正常启动时 `Base.metadata.create_all()` 会幂等创建缺失表。也可单独执行：

```powershell
cd D:\心理学项目\backend
.\.venv\Scripts\python.exe scripts\create_workflow_sidecars.py
```

该脚本的目标表名单是固定白名单，只执行 `CREATE TABLE IF NOT EXISTS` 等价操作；不会触碰 7 张外部业务表。

## 9. 验证

- 当次完整后端测试套件全部通过；测试数量会随功能继续增加，不在本文档固化一个容易过期的数字。
- 新增测试覆盖：0–2 认可量表、execution 1–4、风险 1–3、步骤持久化、周期关联读取、双状态隔离、interaction_status 更新、主模型成功及失败遥测。
- `python -m compileall app scripts tests` 通过。
- 线上既有风险记录未修改、未删除。

## 10. 仍需业务方确认的分析规则

这次没有擅自定义以下临床/统计逻辑：

- 何种指标构成 `overall_emotion_trend`；
- 何种连续变化构成行为/躯体/认知下降信号；
- `self_coaching_confidence_level` 的量表范围与采集时机；
- “卡住”的告警阈值（当前只保存各模块轮数，尚未主动告警）。

建议后续先形成一页数据字典（输入来源、公式、阈值、缺失值语义），再启用这些字段；否则它们会变成不可审计的模型主观判断。
