# BA Coach 当前生产数据库

V2 已上线。生产库：`ba_coach_260908`；版本 `20260910T154355Z`；`DATABASE_SCHEMA_VERSION=v2`。

## 调整摘要

- user_profile 从 29 字段改为 12 字段，支持者拆入 user_supporters，不再依赖两个固定槽位。
- 用户偏好、活动限制、M1 状态、目标、计划版本、周期与步骤分开保存。
- 允许多个目标，每段聊天明确选一个；周期从 planning 阶段创建。
- 新记录由模型提出草稿、网页确认后推进；历史已完成 M1 直接复用，不要求重做，不伪造确认依据。
- 后端档案、聊天、Router、抽取、记忆和删除路径适配；网页新增目标与进度面板。
- 旧重名表改名归档，账号、消息、每日记录、知识库、评测及提示词数据保留。

## 数据库详情

完整实际字段、类型、可空性与切换时行数见 [生产字段快照](DATABASE_LIVE_V2_20260910.md)。发布及验证见 [上线报告](DATABASE_V2_RELEASE_STATUS_20260910.md)。

| 表 | 职责 |
|---|---|
| user_profile | 12 字段基础档案 |
| user_preferences | 偏好与提醒约定 |
| user_supporters | 支持者，一人一行 |
| user_activity_constraints | 活动限制及来源 |
| module_one_record | M1 理解版本 |
| user_module_one_state | 用户级 M1 完成状态 |
| pa_goals | 多目标 |
| module_two_record | 计划版本 |
| module_three_record | 执行契约 |
| pa_cycles | 执行周期 |
| pa_cycle_progress | 周期内步骤 |
| module_four_record | 复盘 |
| ba_memory | 带来源和确认状态的记忆 |
| conversation_runtime_states | 聊天模块、目标、周期 |
| ai_decision_logs | 决策审计 |
| v2_migration_issues | 迁移待核对事项 |

`legacy_20260910t155426z_*` 为归档，旧侧表不再是 V2 业务状态的权威来源。删除聊天保留业务历史，已删消息的证据 ID 仅作历史引用。

## 当前边界

- 支持者 API 上限 10，并非数据库固定两个槽位。
- 历史 2 个周期和 1 张未关联计划保留待核对，不猜测关联。
- 出生年份、性别、职业等可选字段尚无专用网页编辑控件。
- 提醒字段不等于已接入通知调度。
- 自动记忆为未确认的 AI 推断，尚无独立用户记忆管理页。
- 替换目标目前暂停旧目标并重新选择，不自动填写 replaced_by_goal_id。

旧结构见 [历史文档](DATABASE_LEGACY_BEFORE_V2.md)。
