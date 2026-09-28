# 每日记录：补录、修改与历史版本

依据用户提供的两张讨论截图实现：增加日期选择，允许补录过去日期；已有记录可修改，包括记录日期；编辑时带出原内容，后台保留历史版本。

## 使用方式

- 打开「每日记录」，默认今天，可选择过去日期，不能填写未来日期。
- 所选日期已有已完成记录时，显示「打开这一天的记录」，不会用新记录覆盖旧记录。
- 「查看历史」→「修改这份记录」，原来的活动、评分、备注和日期会自动带入；修改后保存。
- 同一账号一天一份记录；修改日期与另一份记录冲突时拒绝覆盖。旧页面的版本落后时返回冲突提示，输入仍保留，用户可重新打开最新记录。
- 管理员「每日记录数据」→选择记录→「查看修改历史」，可逐版查看当时的日期和内容，历史版本只读。
- 旧版总体量表仍是 0–10 分，旧版缺失值仍为空；新版仍是 0–5 分，原有不适用状态可以保留。没有自动换算分数。

## 实现位置

- `backend/app/models.py`：当前记录版本号，以及独立的 `assessment_revisions` 完整快照表。
- `backend/app/schemas.py`：修改请求包含期望版本；旧版和新版评分按原量表校验。
- `backend/app/routes/assessment.py`：过去日期提交、按日期读取、本人记录修改及版本读取。修改通过带版本条件的更新防止旧页面覆盖，旧快照和新快照与记录更新在同一事务中提交。
- `backend/app/routes/admin_assessments.py`：仅管理员可读取历史版本。
- `frontend/components/DailyAssessmentModal.tsx`、`AssessmentHistory.tsx`、`ConversationSidebar.tsx`：日期选择、冲突提示、带入内容编辑，以及入口文案。
- `frontend/components/AdminDailyRecords.tsx`：只读版本选择与查看。
- `frontend/lib/assessment.ts`、`adminAssessments.ts`：对应接口与错误信息。

## 上线前必做

本次仅修改本地代码，未部署，未修改线上数据库。

旧数据库必须在部署新代码前执行 `backend/scripts/migrate_daily_history_0928.py`：先指定实际数据库名进行默认 dry-run，检查后再加 `--apply`。脚本添加 `assessment_entries.revision_no` 和 `assessment_revisions` 表，可重复执行，不改动原日期和评分，不导入测试数据。

历史记录不会批量回填版本；第一次修改旧记录时，在同一事务中保存原始内容作为基线，然后保存新版本。迁移前发生、从未保存过的修改无法追溯。

迁移依赖现有每日记录的 `scale_version` 字段，旧版 0917 迁移须已完成。MySQL 迁移尚未在生产库执行；已通过隔离 SQLite 数据库的 dry-run、错误库名保护、实际迁移和重复执行检查。

## 验证

- 50 项后端相关测试通过，涵盖补录、改日期、历史完整性、版本冲突、账号隔离、管理员权限、旧量表保留和迁移。
- TypeScript 检查及生产构建通过。
- `infra/qa/daily-history-0928.cjs` 使用隔离的浏览器接口数据，在 1440px 和 390px 下验证补录、修改、冲突保留输入、旧版记录以及管理员查看版本；截图已检查，无真实账号或生产数据写入。
