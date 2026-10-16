# 移除档案中的沟通方式选项

移除“直接明了 / 温柔引导 / 理性分析 / 轻松幽默”这组用户档案设置。它不再作为界面选项、API 字段、持久化属性或模型输入的一部分。

## 代码改动

| 文件 | 改动及原因 |
| --- | --- |
| `frontend/components/AuthScreen.tsx`、`ProfileModal.tsx` | 删除注册和档案中的选择控件、状态和提交字段。档案中的话题边界单独保留为“话题偏好”。 |
| `frontend/lib/auth.ts`、`profile.ts` | 删除选项枚举和请求/响应类型中的字段。 |
| `backend/app/schemas.py`、`routes/auth.py`、`routes/profile.py` | 注册、档案查询及更新接口不再定义、写入或返回该字段。旧客户端传入的未知字段沿用现有忽略规则；不会保存或生效。共享的注册及档案接口保留。 |
| `backend/app/v2_profile.py`、`models_business.py`、`database_v2_schema.py` | 删除旧库和 V2 的字段映射。其他偏好与提醒仍使用原表，不删除整张表。 |
| `backend/app/clinical_store.py` | 删除旧库的“沟通偏好……按这个风格说话”注入；V2 档案 JSON 因响应 Schema 已移除字段，也不再包含此设置。 |
| `backend/app/context_epochs.py` | 更新上下文状态条目的版本。旧会话下次生成时重建状态基准，避免旧值仍出现在缓存基准中；保留聊天历史、历史摘要与干预进度。重建后仍复用新基准。 |
| `backend/app/graph/state.py` | 更新档案上下文注释，移除已失效的说明。 |
| `infra/contracts/profile_patch.schema.json`、`backend/scripts/rehearse_database_v2.py`、`gen_database_doc.py` | 清理接口契约、迁移输入映射和字段维护说明，防止后续重新引入。 |
| 相关注册、档案、聊天测试及 QA 数据 | 更新过时用例；增加旧请求、旧数据库值及旧上下文缓存不再生效的回归验证。 |

此次不改模块提示词中的一般沟通原则、用户真实聊天内容、身体限制或不愿讨论的话题。历史数据库快照和过往审计日志仍保留原貌，运行时不读取这些文件。

## 已有数据库清理

应用删除映射后，不再读取旧列；旧数据库不需要先删列才能运行新代码。新建数据库不会创建这两个字段。

`backend/scripts/remove_communication_preference_1015.py` 负责物理清理：

- 仅处理旧版 `user_profile.communication_preference` 和 V2 `user_preferences.communication_style`，存在才删除。
- 默认只检查；`--apply --backup /安全路径/新文件.json` 才执行。备份文件权限为 `0600`，禁止覆盖已有文件。
- 先部署新代码并等待旧服务进程退出，再执行清理；旧版本代码仍会查询旧列。
- 回滚时先用同一脚本 `--restore --backup /安全路径/原文件.json` 恢复列和值，再恢复旧代码。
- MySQL DDL 可能自动提交；异常时核对两列的实际状态，不能把事务回滚当作列已恢复。

已于 2026-10-15 部署到生产版本 `20261015T074951Z`。后端、前端及提醒任务均已切换到新版本，旧服务退出后已备份并删除生产库的 `user_preferences.communication_style` 列；旧版 `user_profile.communication_preference` 列原本不存在。备份保存在服务器受限目录，不参与运行时读取。

## 验证

- 原有注册、档案、临床上下文、缓存与 V2 Schema 回归测试通过。
- 新增验证：旧请求无效、旧数据库值不进入模型输入、旧状态基准重建而历史保留、再次请求复用新基准、列迁移只影响指定列且可恢复。
- 前端 TypeScript 检查通过；桌面与手机档案页面检查移除项，并验证话题偏好仍可保存；手机注册页保留身体限制选项。
