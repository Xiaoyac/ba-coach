# 数据库复核与整改报告

> 历史阶段报告：后续已完成 V2 正式切换。当前结果见 DATABASE_V2_RELEASE_STATUS_20260910.md，字段见 DATABASE_LIVE_V2_20260910.md。下方“未上线”仅适用于早期版本。

日期：2026-09-10。目标库：`ba_coach_260908`。代码版本：`20260910T022417Z`。

## 一、结论与本次调整

截图指出的不是一个问题，而是三类问题混在一起：旧兼容结构仍可见、真实读写缺陷，以及 V2 设计没有完整实施。不能靠改数据库名称、加几个字段或改一份文档就声称全部解决。

只读审计发现生产库有 30 张基表，尚没有 `pa_goals`、`user_module_one_state`、`pa_cycle_progress`、`ba_memory`、`ai_decision_logs`。当前是旧业务表＋应用扩展表。前期“整份 V2”需求不能据此认定已交付；本次也不把局部整改称为完整 V2 迁移。

本次实际代码修复：

1. 支持者读取使用统一策略：完整列表优先；NULL 才回退旧槽位；[] 明确清空，不能复活旧支持者。
2. 旧槽位编辑同步回完整列表，保留第三人及以后成员，保留旧 ENUM 表示不了的自定义关系；冲突输入返回 422，不能悄悄覆盖。
3. 页面 `has_supporter` 由有效列表派生；修正档案列表和模型上下文不一致。
4. 四模块正式步骤注册表、完成规则、Router 提示词与步骤白名单同源。
5. 持久化层拒绝非法转换、缺少必需步骤的跳转；未知模块不再被当成“步骤全部完成”。
6. 只有真实 M1→M2 转换才写 M1 完成标记，不再从其他模块位置倒推已完成 M1。
7. 先提交持久化路由结果，再更新内存模块，避免数据库失败而内存提前跳转。
8. 既有 Router 遥测补入结构化决策版本、完成步骤、关联消息和周期信息，不回填历史证据。
9. DMS 新增三张只读视图，把支持者、M1 完成状态和正式步骤注册表展开；不修改旧基表或复制敏感数据。

文档调整：纠正“supporters 数量不限”的错误，现有 API 实际上限为 10；修正 V2 周期创建时点矛盾；记录用户已确定的多目标及复用 M1 决策；导出线上完整字段快照和现有可生成的 JSON Schema。

## 二、supporter 为什么仍有 1、2？

### 当前真实存储

`profile_extensions.supporters` 是完整列表，例如：

```json
[
  {"relation":"朋友","nickname":"小林","influence":"强"},
  {"relation":"室友","nickname":"小陈","influence":"中"},
  {"relation":"教练","nickname":"小周","influence":null}
]
```

`user_profile.supporter1_* / supporter2_*` 只镜像前两个位置。旧 ENUM 没有“室友”，因此第二槽的 relation 可以是 NULL，但“室友”仍在完整列表中，不能据此断定数据被删掉。当前关系最多 32 字、昵称最多 64 字、影响力为弱/中/强或 NULL、列表最多 10 人。超过限制返回校验错误，不截掉第三人。

### 本次发现并修复的真实缺陷

- 原网页没有扩展行时返回空列表，但 prompt 回退旧槽位，导致网页看不到模型知道的人。
- 原 prompt 把 [] 和 NULL 混用，显式清空后可能回退旧数据。
- 原旧槽位 API 与完整列表可各写各的，第三人或自定义关系有被错误覆盖的风险。
- 同时提交 `supporters=[]` 与 `has_supporter=true`，原流程可能写入自相矛盾状态，现在拒绝。

不直接删除旧列，因为还不能证明所有 DMS 查询、历史导出或其他消费者已迁移。删除前应完成依赖盘点和回滚方案。保留兼容列不代表把它当作完整资料来源。

### DMS 怎么看

刷新数据库对象，切换到“视图”查找 `v_user_supporters`。一行一个人，包含 user_id、位置、关系、昵称、影响力、来源。需要查某位用户时使用参数化条件，不导出无关用户：

```sql
SELECT * FROM v_user_supporters
WHERE user_id = :user_id
ORDER BY supporter_position;
```

视图使用 SQL SECURITY INVOKER，不绕过底层访问权限。视图用于读取，不支持在这里直接改用户资料；修改走网页/API。

## 三、P0-GAP-01：Step key 与完成条件

当前并非完全没有正式 key，旧代码已有白名单；缺陷在于完整判定规则未成为统一注册表，文档与提示词容易分叉。本次源文件是 `backend/app/workflow_contract.py`，版本 `workflow-20260910-v1`，DMS 对应 `v_workflow_step_registry`。

M1 正式 key：

| key | 完成规则要点 |
|---|---|
| core_problem_example | 至少一个足以理解行为与情绪关联的具体事例，不要求穷尽背景 |
| depression_cycle_formulated | 根据事例形成触发、行为、后果之间的理解，并与用户核对 |
| ba_education_completed | 结合经历解释 BA，且用户表达理解，不是教练单方面说过 |
| goal_setting_consent | 用户明确愿意开始目标设定，不能把模糊附和当同意 |

M2：pa_concept_understood、values_or_intention_explored、activity_selected、pa_card_completed。
M3：recording_explained、recording_plan_agreed、execution_contract_reached。
M4：execution_reviewed、abc_chain_completed、barriers_identified、coping_strategy_selected、review_decision_made。

其余模块的逐项规则在注册表/视图中列出。截图里的 `m1_context_understood`、`M1-T01` 不是当前生产 key，不能直接写入。Router 输出未知 key 仍会被过滤，持久化时再次检查允许转换和必需步骤。

这些是明确判定合同，但判定仍来自 Router LLM；不宣称程序能独立证明用户理解或同意。

## 四、P0-GAP-02：M1 两个结束状态与证据

不建议再增加两个可独立改写的真假标记，与 completed_steps 形成第二份真相。现阶段使用 `v_m1_completion_status` 展开：

- ba_explanation_status：由 ba_education_completed 派生；
- goal_setting_willingness：由 goal_setting_consent 派生。

值采用 `router_reported_complete/router_reported_consent/not_recorded`，刻意不叫“已被独立验证”。每行关联具体 conversation_id、session_id、user_id、完成步骤和更新时间。

新 Router 遥测在 `ai_execution_events.event_metadata.workflow_decision` 保存 schema_version、registry_version、from_module、to_module、completed_steps、decision_message_id、source_cycle_id、active_cycle_id。

**证据边界**：decision_message_id 是该次判断对应的消息位置，不是每个步骤的精确证据片段；evidence_status 明确为 router_inferred_not_individually_verified。旧日志缺这些字段不会被伪造补齐。每步关联准确用户确认消息、撤回和重确认仍属后续完整审计机制，未完成。

## 五、Goal / Cycle 矛盾如何处理

当前旧程序在 M1→M2 时创建 `pa_cycles`，步骤保存在 conversation_module_progress；所以线上现有流程并不是 M4 才创建周期。

V2 讨论稿却同时写“目标卡确认后创建”和“M4 才创建”，并让 M2 进度依赖 cycle_id，确实自相矛盾。已统一目标设计：

1. 进入 M2 开始新一轮讨论时，先有草稿目标、planning 周期及进度行。
2. planning 的 module_two_record_id 可以为空。
3. 目标卡确认后，在已有周期绑定该目标的确认版本，不重复创建周期。
4. 进入 waiting_execution/reviewing/completed 前，必须存在同用户、同目标的有效确认版本。
5. M4 只读取已有周期复盘，不补建过去的 M2/M3 历史。

这是**目标设计修正**，不是生产已迁移。生产旧 status=active 不与 V2 planning 强行混用，也不把旧周期伪装成已有目标版本关联。

## 六、其他截图问题逐项结论

| 问题 | 本次处理 | 尚未完成 |
|---|---|---|
| Step/Task registry | 代码、Router、白名单、DMS 注册表同源 | 每步独立证据校验与人工撤回 |
| record_status / confirmation_status / Step 不变量 | 明确：结构完整≠用户确认；不得相互自动推断；持久化增加当前流程前置条件校验 | V2 确认版本、跨表事务与状态机尚未实施 |
| JSON 只有类型 | 导出真实 Supporter、ProfileUpdate、AssessmentSubmission、四模块步骤 JSON Schema | phase_a/b/c、事件、频率、障碍、记忆等全部 V2 JSON 仍待最终合同与实现，不能称已全覆盖 |
| enum 机器值 | 现有 DB enum 见完整快照；现有 0–2 认可、执行 1–4、复盘 1–3、风险 1–3 保持语义，不重编码旧数据 | V2 新增结束目标等枚举尚未部署 |
| 有限状态修复 | 不做自动撤回已完成步骤，不私自改历史结论；数据库失败不提前推进内存 | 修复权限、字段白名单、原因、前后值、证据及审批协议需要团队确认 |
| 多 active goal | 用户已明确允许多个，每段聊天明确选一个；已修正文档不再写“未决” | pa_goals 与网页目标选择器尚未实现 |
| 新聊天复用 M1 | 已记录用户决定，revisit_needed 不自动回 M1 | 用户级确认版本及复用入口尚未实现 |
| Memory 去重/替换 | 不按类别只取一条，不把文本相似直接当同一事实；最终规则建议如下 | ba_memory 目标表及完整写入/失效/检索生命周期尚未实施 |
| 删除和保留 | 本轮不新增删除、TTL 或临床档案级联删除；继续原有聊天删除行为 | 保留期、研究退出、审计访问、源消息删除策略需团队确认 |
| 文档未展开现有表 | 已输出 30 张基表的逐字段类型及可空性，包括每日记录、活动、风险和知识库 | 完整约束还应以 SHOW CREATE TABLE / DMS 为准 |

### V2 状态不变量（待实施，不是假装现有字段）

- draft 可以缺字段，不能被当成用户确认；confirmed 必须同时通过字段、关系归属、确认来源校验。
- superseded 记录保持历史含义，修正创建新版本，不原地篡改过去周期使用的版本。
- 完成步骤是流程判断，record_status 是记录生命周期，confirmation_status 是用户确认；三者不能互相等同。
- goal、cycle、record、conversation 的归属必须一致，不能只校验 ID 存在。
- 历史信息不足时保留 unknown/not_recorded，不从当前模块位置反推过去所有条件已完成。

### Memory 合同建议（待实施）

memory_key 使用服务端生成、每条逻辑事实稳定的 UUID，不等于 memory_type，不由 LLM 自造编号。完全相同来源和规范化内容可幂等去重；相似但不同的事实只产生候选，不能自动合并。修正要关联旧记录、来源及确认状态，在事务中标记被替代项失效，检索只取有效确认项。删除来源消息后不能声称证据仍完整，也不能未经规则决定连带删临床事实。

## 七、验证、发布与未完成范围

本次基表不 ALTER、不 DROP、不批量回填业务数据；DMS 只新增查询视图，生产原记录保留。新增用例检查第三位支持者、自定义关系、清空语义、混合字段冲突、未知模块、缺步骤跳转。最终测试和线上核验结果见报告末尾发布记录。

这里没有拿到截图引用的 `DATABASE-final.md` 或其最新版模块提示词，因此本次对齐的是实际代码、本机 V2 讨论稿与截图可见问题，不能声称逐行修复了未提供的文件。请补充原文件后继续完成 V2 详细合同和迁移验收。

附件：`DATABASE_LIVE_SCHEMA_20260910.md`；`infra/contracts/*.schema.json`；修正后的 `DATABASE_SCHEMA_V2_PROPOSAL.md`。
