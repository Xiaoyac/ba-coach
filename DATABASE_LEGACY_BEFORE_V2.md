# 历史数据库字段总览（V2 切换前）

> 历史资料，不代表当前生产。当前入口见 DATABASE.md。

库：`ba_coach_260803`（阿里云 RDS MySQL）
共 **27 张表 / 287 个字段**，行数为 2026-09-01 的实际值。

本文档的字段表是**从线上库 `information_schema` 直接导出**生成的，不是手抄的，因此不会和真实结构不一致。
只有「说明」和「写入来源」两列是人工补充的。

---

## 一、先看这个：两类表，性质完全不同

| | 应用自有表（20 张） | 业务表（7 张） |
|---|---|---|
| 谁建的 | 本应用，启动时 `create_all` 自动建 | **另一个系统**，早已存在 |
| 能不能改结构 | 能 | **不能**——本应用永远不对它们发 DDL |
| 在代码里 | `app/models.py`（`Base`） | `app/models_business.py`（`BizBase`，从不进 `create_all`） |
| 装什么 | 账号、登录会话、对话记录、每日记录 | 用户档案、四个模块的临床记录、风险、遥测 |

这个分界是硬的：`BizBase` 是一个**独立的** DeclarativeBase，永远不会被传给 `create_all()`，
所以本应用不可能创建、修改或删除那 7 张业务表中的任何一张。

**业务表一个外键都没有**（`information_schema` 查证过）。`user_id` / `uuid` 只是普通的索引列，
靠应用自己 join。代码里也就没有为它们编造 `ForeignKey`。

---

## 二、这些表是怎么串起来的

一个标识符贯穿全部临床数据：**`user_profile.uuid`**。

```
                    user_accounts
                   （用户名 / 密码）
                          │
                          │ profile_uuid
                          ▼
                  user_profile.uuid  ◄─────────── 一切的连接点
                          │
      ┌───────────────────┼────────────────────┬─────────────────┐
      │ user_id           │ profile_uuid       │ subject_id      │
      ▼                   ▼                    ▼                 ▼
 module_one_record   profile_extensions   conversations   assessment_entries
 module_two_record                              │                 │
 module_three_record                            │ FK              │ FK
 module_four_record                             ▼                 ▼
 risk_monitoring                    conversation_messages   activity_logs
 interaction_status
```

同一个值，在不同表里叫三个名字，这是最容易踩的坑：

| 列名 | 出现在 | 是什么 |
|---|---|---|
| `uuid` | `user_profile` | 本体 |
| `user_id` | 6 张业务记录表 | 同一个值 |
| `subject_id` | `conversations`、`assessment_entries` | 同一个值 |
| `profile_uuid` | `user_accounts`、`profile_extensions` | 同一个值 |

叫 `subject_id` 而不是 `user_id` 是有意的历史痕迹：那时还没有账号系统，这个 id 是浏览器自己生成的、
**未经认证**的。现在它由登录令牌解析而来，已经是可信的了。

应用自有表内的主要数据库外键如下；跨到 7 张业务表时则只靠 UUID 逻辑关联，不伪造外键：

- `user_accounts → account_handles / account_settings / account_emails / account_email_tokens / auth_sessions / issue_reports`
- `knowledge_sources → knowledge_chunks`
- `conversations → conversation_messages / conversation_runtime_states / conversation_module_progress / pa_cycles`
- `pa_cycles → clinical_record_cycle_links`
- `assessment_entries → activity_logs`
- `ai_execution_events` 指向对话和 assistant 消息，但数据库使用 `SET NULL` 保留审计；应用的“删除对话”流程会主动清理相关遥测

标为级联删除的父行删除后，子行会一起删除；但 `profile_uuid` / `subject_id` / `user_id`
跨业务表时没有数据库级级联，必须由应用按业务规则处理。

---

## 三、读表格的约定

- **空** 列：`○` = 允许 NULL，`●` = NOT NULL
- **写入来源**：这个值实际由谁填。标 **从未写入** 的字段**结构上存在，但代码里没有任何地方写它**
- 「投影」= 该值的完整版本存在 `profile_extensions`，这里只是同步过来的一份更粗的副本

---

## 四、应用自有表

### `user_accounts` — 登录账号

当前 **12** 行

> 业务表里没有任何用户名／密码字段——那套 schema 是给「身份从别处来」的系统设计的。所以凭据放在这张应用自有的表里，用 `profile_uuid` 指向 `user_profile.uuid`。

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `id` | `int` | ● | — | 主键，自增 | 自动 |
| 2 | `username` | `varchar(64)` | ● | — | 登录名，唯一。存的是小写规范化后的值，所以 Nanan 和 nanan 不会变成两个账号 | 注册 / 登录 |
| 3 | `password_hash` | `varchar(255)` | ● | — | Argon2id 的 PHC 字符串——算法、代价参数、每个密码独立的盐全都编码在这一串里，所以没有单独的盐字段 | 注册 / 登录 |
| 4 | `profile_uuid` | `varchar(36)` | ● | — | 指向 `user_profile.uuid`。**这是账号与全部临床数据之间唯一的连接** | 注册 / 登录 |
| 5 | `created_at` | `datetime` | ● | `now()` | 注册时间 | 自动 |
| 6 | `last_login_at` | `datetime` | ○ | — | 最近一次登录时间 | 注册 / 登录 |

索引：`ix_account_username`（username）、`profile_uuid`（唯一，profile_uuid）、`username`（唯一，username）


### `account_handles` — 用户展示昵称与标签

当前 **12** 行

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `account_id` | `int` | ● | — | → `user_accounts.id`，一对一主键；账号删除时级联删除 | 注册 / 我的档案页 |
| 2 | `base_username` | `varchar(58)` | ● | — | 公开展示昵称；字段名是早期迁移遗留，并不是登录账号名 | 注册 / 我的档案页 |
| 3 | `normalized_base` | `varchar(58)` | ● | — | 展示昵称的规范化值，用于不区分大小写地判重与检索 | 注册 / 我的档案页 |
| 4 | `tag` | `varchar(5)` | ● | — | 5 位数字标签；与 `normalized_base` 组合唯一，形成“昵称#12345” | 注册 / 我的档案页 |
| 5 | `created_at` | `datetime` | ● | `now()` | 展示身份创建时间 | 自动 |

索引：`ix_account_handle_base`（normalized_base）、`uq_account_handle_base_tag`（唯一，normalized_base+tag）


### `account_settings` — 账号角色与模型偏好

当前 **12** 行

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `account_id` | `int` | ● | — | → `user_accounts.id`，一对一主键；账号删除时级联删除 | 注册 / 我的档案页 / 管理员 |
| 2 | `preferred_provider` | `varchar(16)` | ● | `deepseek` | 主回复模型供应商：`deepseek` 或 `doubao` | 注册 / 我的档案页 / 管理员 |
| 3 | `role` | `varchar(16)` | ● | `user` | 账号角色：`user` 或 `admin`；管理员功能以此鉴权 | 注册 / 我的档案页 / 管理员 |
| 4 | `updated_at` | `datetime` | ● | `now()` | 模型偏好或角色最后更新时间 | 自动 |

### `account_emails` — 账号邮箱

当前 **7** 行

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `account_id` | `int` | ● | — | → `user_accounts.id`，一对一主键；账号删除时级联删除 | 注册 / 邮箱验证 |
| 2 | `email` | `varchar(320)` | ● | — | 用户提交的邮箱地址，用于展示与发送验证/重置邮件 | 注册 / 邮箱验证 |
| 3 | `normalized_email` | `varchar(320)` | ● | — | 规范化后的邮箱，唯一；用于登录恢复与重复检查 | 注册 / 邮箱验证 |
| 4 | `verified_at` | `datetime` | ○ | — | 邮箱验证完成时间；NULL 表示尚未验证 | 注册 / 邮箱验证 |
| 5 | `created_at` | `datetime` | ● | `now()` | 邮箱记录创建时间 | 自动 |
| 6 | `updated_at` | `datetime` | ● | `now()` | 邮箱地址或验证状态最后更新时间 | 自动 |

索引：`ix_account_email_normalized`（normalized_email）、`normalized_email`（唯一，normalized_email）


### `account_email_tokens` — 邮箱验证/重置令牌

当前 **0** 行

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `id` | `int` | ● | — | 主键，自增 | 自动 |
| 2 | `account_id` | `int` | ● | — | → `user_accounts.id`；账号删除时级联删除 | 验证邮箱 / 重置密码 |
| 3 | `purpose` | `varchar(24)` | ● | — | 令牌用途：`verify_email` 或 `password_reset` | 验证邮箱 / 重置密码 |
| 4 | `token_hash` | `varchar(64)` | ● | — | 一次性令牌的 SHA-256，唯一；数据库不保存可直接使用的明文令牌 | 验证邮箱 / 重置密码 |
| 5 | `created_at` | `datetime` | ● | `now()` | 令牌签发时间 | 自动 |
| 6 | `expires_at` | `datetime` | ● | — | 令牌失效时间 | 验证邮箱 / 重置密码 |
| 7 | `consumed_at` | `datetime` | ○ | — | 令牌首次成功使用时间；非 NULL 后不可再次使用 | 验证邮箱 / 重置密码 |

索引：`ix_email_token_account_purpose`（account_id+purpose）、`ix_email_token_hash`（token_hash）、`token_hash`（唯一，token_hash）


### `auth_sessions` — 登录会话（令牌）

当前 **23** 行

> **只存令牌的 SHA-256，不存令牌本身。**做成数据库行而不是 JWT，是为了让登出能真正吊销：删掉这一行会话立刻失效，而自包含的签名令牌在过期前做不到这点。

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `id` | `int` | ● | — | 主键，自增 | 自动 |
| 2 | `account_id` | `int` | ● | — | → `user_accounts.id`，外键，账号删除时级联删除 | 登录时建，登出或过期时删 |
| 3 | `token_hash` | `varchar(64)` | ● | — | 登录令牌的 SHA-256，唯一。**明文令牌只存在于用户浏览器** | 登录时建，登出或过期时删 |
| 4 | `created_at` | `datetime` | ● | `now()` | 登录时间 | 自动 |
| 5 | `expires_at` | `datetime` | ● | — | 过期时刻。请求时发现已过期即当场删行 | 登录时建，登出或过期时删 |

索引：`account_id`（account_id）、`ix_auth_session_token`（token_hash）、`token_hash`（唯一，token_hash）


### `profile_extensions` — 档案扩展（业务表装不下的答案）

当前 **0** 行

> `user_profile` 的支持者只有 2 个固定槽位且关系是 6 选 1，存不了第三个人或「室友」；后端也保留了精确提醒窗口的兼容能力，而业务表只能表示 6 个固定 ENUM 时段。当前网页的提醒时段已恢复使用 ENUM，因此 minute 字段通常为 NULL；支持者完整列表仍存在这里。业务表不能改，所以扩展答案存在本表，并**向下投影**回业务表的旧字段——别的系统读 `user_profile` 仍能看到一个更粗但不过期的答案。

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `id` | `int` | ● | — | 主键，自增 | 自动 |
| 2 | `profile_uuid` | `varchar(36)` | ● | — | → `user_profile.uuid`（无数据库外键，业务表自己也一个外键都没有） ·唯一 | 我的档案页 |
| 3 | `reminder_start_minute` | `int` | ○ | — | 提醒时段起点，距 0 点的分钟数（0–1439）。用整数而非 TIME，是为了「没填」就是 NULL，且排序、判重叠不用挂日期 | 兼容 API（当前网页不写） |
| 4 | `reminder_end_minute` | `int` | ○ | — | 提醒时段终点，同上 | 兼容 API（当前网页不写） |
| 5 | `supporters` | `json` | ○ | — | `[{relation, nickname, influence}, …]`，当前 API 最多 10 人（不是 2 人，也不是无限）；关系可自填。NULL 表示未迁移，[] 表示用户明确清空；网页与 prompt 使用统一读取策略 | 我的档案页 |
| 6 | `created_at` | `datetime` | ● | `now()` | 创建时间 | 自动 |
| 7 | `updated_at` | `datetime` | ● | `now()` | 更新时间 | 自动 |

索引：`ix_profile_extension_uuid`（profile_uuid）、`profile_uuid`（唯一，profile_uuid）


### `prompt_overrides` — 管理员提示词覆盖

当前 **6** 行

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `id` | `int` | ● | — | 主键，自增 | 自动 |
| 2 | `prompt_key` | `varchar(32)` | ● | — | 被覆盖的系统提示词键，系统范围内唯一 | 管理员提示词页 |
| 3 | `content` | `longtext` | ● | — | 管理员保存的完整提示词正文；不存在该行时回退到代码默认值 | 管理员提示词页 |
| 4 | `updated_by` | `varchar(64)` | ● | — | 最后修改者的审计标识 | 管理员提示词页 |
| 5 | `updated_at` | `datetime` | ● | `now()` | 最后修改时间 | 自动 |

索引：`ix_prompt_overrides_prompt_key`（唯一，prompt_key）


### `knowledge_sources` — 知识库来源

当前 **10** 行

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `id` | `int` | ● | — | 知识文献主键，自增 | 自动 |
| 2 | `name` | `varchar(255)` | ● | — | 来源文件/文献名称，唯一；重复导入会更新同一来源 | 知识库导入脚本 |
| 3 | `category` | `varchar(8)` | ● | — | 知识分类：`BA`、`PA`、`BCT` 或 `MI`，决定可供哪些模块检索 | 知识库导入脚本 |
| 4 | `content_hash` | `varchar(64)` | ● | — | 原始规范化内容的 SHA-256，用于幂等导入和变更识别 | 知识库导入脚本 |
| 5 | `updated_by` | `varchar(64)` | ● | — | 导入者或导入脚本的审计标识 | 知识库导入脚本 |
| 6 | `created_at` | `datetime` | ● | `now()` | 首次导入时间 | 自动 |
| 7 | `updated_at` | `datetime` | ● | `now()` | 最近一次内容更新或重导入时间 | 自动 |

索引：`ix_knowledge_sources_category`（category）、`name`（唯一，name）


### `knowledge_chunks` — 知识库切片

当前 **1331** 行

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `id` | `int` | ● | — | 知识切片主键，自增 | 自动 |
| 2 | `source_id` | `int` | ● | — | → `knowledge_sources.id`；来源删除时级联删除全部切片 | 知识库导入脚本 |
| 3 | `ordinal` | `int` | ● | — | 切片在原文中的顺序；与 source_id 组合唯一 | 知识库导入脚本 |
| 4 | `heading` | `varchar(512)` | ● | — | 切片所属标题，用于检索结果解释与上下文定位 | 知识库导入脚本 |
| 5 | `content` | `longtext` | ● | — | 实际供关键词检索并注入 Agent Prompt 的文本内容 | 知识库导入脚本 |

索引：`ix_knowledge_chunk_source`（source_id）、`uq_knowledge_chunk_order`（唯一，source_id+ordinal）


### `issue_reports` — 用户问题报告

当前 **3** 行

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `id` | `int` | ● | — | 问题报告主键，自增 | 自动 |
| 2 | `account_id` | `int` | ● | — | → `user_accounts.id`；报告所属登录账号 | 问题反馈入口 |
| 3 | `subject_id` | `varchar(64)` | ● | — | = `user_profile.uuid`，便于与对话和临床记录交叉排查 | 问题反馈入口 |
| 4 | `description` | `text` | ● | — | 用户填写的问题描述 | 问题反馈入口 |
| 5 | `status` | `varchar(16)` | ● | `open` | 处理状态：`open` 或 `resolved` | 问题反馈入口 |
| 6 | `screenshot` | `mediumblob` | ○ | — | 可选页面截图二进制；受管理员接口保护，不以 base64 存储 | 问题反馈入口 |
| 7 | `screenshot_mime` | `varchar(32)` | ○ | — | 截图 MIME 类型，如 `image/png`；无截图时为 NULL | 问题反馈入口 |
| 8 | `page_url` | `varchar(2048)` | ● | — | 提交问题时所在页面 URL | 问题反馈入口 |
| 9 | `session_id` | `varchar(64)` | ○ | — | 可选的相关对话 session_id | 问题反馈入口 |
| 10 | `last_error` | `text` | ○ | — | 客户端当时捕获到的最近错误文本 | 问题反馈入口 |
| 11 | `user_agent` | `varchar(512)` | ● | — | 浏览器 User-Agent，用于判断设备和兼容性 | 问题反馈入口 |
| 12 | `viewport_width` | `int` | ○ | — | 提交时浏览器视口宽度（像素） | 问题反馈入口 |
| 13 | `viewport_height` | `int` | ○ | — | 提交时浏览器视口高度（像素） | 问题反馈入口 |
| 14 | `client_online` | `tinyint(1)` | ○ | — | 浏览器当时报告的网络在线状态 | 问题反馈入口 |
| 15 | `created_at` | `datetime` | ● | `now()` | 问题提交时间 | 自动 |
| 16 | `resolved_at` | `datetime` | ○ | — | 管理员标记解决的时间；未解决时为 NULL | 问题反馈入口 |

索引：`ix_issue_report_account_created`（account_id+created_at）、`ix_issue_report_status_created`（status+created_at）


### `conversations` — 对话（侧栏一行一条）

当前 **29** 行

> 和 `SessionStore`（内存里、给模型读的实时上下文）是两回事：那个会按 token 预算裁剪、重启即丢，两条性质都不适合「浏览历史对话」。这张表永不裁剪。

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `id` | `int` | ● | — | 主键，自增 | 自动 |
| 2 | `subject_id` | `varchar(64)` | ● | — | = `user_profile.uuid` | 首轮建行时写 |
| 3 | `session_id` | `varchar(64)` | ● | — | 唯一。与 LangGraph 的会话 id 是**同一个值**——前端因此只需要一个标识符：既是「继续哪段会话」也是「侧栏哪一行」 | 首轮建行时写 |
| 4 | `title` | `varchar(80)` | ● | — | 取自首条用户消息，之后可重命名。后续轮次不会覆盖已改的标题 | 首轮自动生成，之后由侧栏「重命名」改 |
| 5 | `created_at` | `datetime` | ● | `now()` | 创建时间 | 自动 |
| 6 | `updated_at` | `datetime` | ● | `now()` | 每轮**显式**更新（不靠 onupdate——只追加子表消息不会碰到本行，值会悄悄变旧，而侧栏正是按这列排序） | 每轮对话自动写 |
| 7 | `pinned` | `tinyint(1)` | ● | `0` | 置顶。置顶的排在最前，与时间无关 | 侧栏「置顶」 |
| 8 | `revision` | `int` | ● | `0` | 对话持久化修订号；新增消息或后台补写路由思考都会递增，供多设备增量同步 | 每轮对话自动写 |

索引：`ix_conversation_subject_pinned_updated`（subject_id+pinned+updated_at）、`session_id`（唯一，session_id）


### `conversation_messages` — 对话消息（完整逐字记录）

当前 **561** 行

> 只追加，不修改、不裁剪。

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `id` | `int` | ● | — | 主键，自增 | 自动 |
| 2 | `conversation_id` | `int` | ● | — | → `conversations.id`，外键，级联删除 | 每轮对话自动写 |
| 3 | `position` | `int` | ● | — | 显示顺序 | 每轮对话自动写 |
| 4 | `role` | `varchar(16)` | ● | — | `user` 或 `assistant` | 每轮对话自动写 |
| 5 | `content` | `text` | ● | — | 消息正文 | 每轮对话自动写 |
| 6 | `reasoning_content` | `longtext` | ○ | — | 主回复模型的深度思考内容；与用户可见回答分栏保存 | 每轮对话自动写 |
| 7 | `model_name` | `varchar(128)` | ○ | — | 生成该条 assistant 消息的实际模型 ID；用户消息通常为 NULL | 每轮对话自动写 |
| 8 | `routing_reasoning_content` | `longtext` | ○ | — | 回复完成后，模块跳转模型补写的判断过程 | 每轮对话自动写 |
| 9 | `router_model_name` | `varchar(128)` | ○ | — | 执行本轮模块跳转判断的模型 ID；固定走 DeepSeek 路由配置 | 每轮对话自动写 |
| 10 | `risk_gate_duration_ms` | `int` | ○ | — | 本轮风险闸门耗时（毫秒） | 每轮对话自动写 |
| 11 | `time_to_first_reasoning_token_ms` | `int` | ○ | — | 请求发出到首个深度思考 token 的耗时（毫秒） | 每轮对话自动写 |
| 12 | `time_to_first_content_token_ms` | `int` | ○ | — | 请求发出到首个可见回复 token 的耗时（毫秒） | 每轮对话自动写 |
| 13 | `main_generation_duration_ms` | `int` | ○ | — | 主回复模型完整生成耗时（毫秒） | 每轮对话自动写 |
| 14 | `router_duration_ms` | `int` | ○ | — | 后台模块跳转判断耗时（毫秒） | 每轮对话自动写 |
| 15 | `input_tokens` | `int` | ○ | — | 主回复输入 token；供应商未返回时为 NULL | 每轮对话自动写 |
| 16 | `output_tokens` | `int` | ○ | — | 主回复可见输出 token；供应商未返回时为 NULL | 每轮对话自动写 |
| 17 | `reasoning_tokens` | `int` | ○ | — | 主回复思考 token；供应商未返回时为 NULL | 每轮对话自动写 |
| 18 | `provider_request_id` | `varchar(128)` | ○ | — | 模型供应商请求 ID，用于日志关联和工单排查 | 每轮对话自动写 |
| 19 | `finish_reason` | `varchar(32)` | ○ | — | 模型停止原因，如 `stop`、`length`；供应商未返回时为 NULL | 每轮对话自动写 |
| 20 | `error_code` | `varchar(64)` | ○ | — | 本轮统一错误码；成功时为 NULL | 每轮对话自动写 |
| 21 | `prompt_version` | `varchar(64)` | ○ | — | 实际组装 System Prompt 的 SHA-256 短指纹，不保存敏感提示词正文 | 每轮对话自动写 |
| 22 | `created_at` | `datetime` | ● | `now()` | 写入时间 | 自动 |

索引：`ix_conversation_message_conv`（conversation_id）


### `conversation_runtime_states` — 每段对话的权威运行状态

当前 **27** 行

> **每段对话当前模块的唯一权威来源。**同一账号的不同对话互不覆盖。

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `conversation_id` | `int` | ● | — | → conversations.id；一段对话一行 ·主键 | 每轮及后台路由自动写 |
| 2 | `module` | `varchar(16)` | ○ | — | 该对话下一轮应进入的权威 module_N | 每轮及后台路由自动写 |
| 3 | `memory` | `json` | ● | — | LangGraph 的紧凑、可恢复记忆；不存完整逐字稿 | 每轮及后台路由自动写 |
| 4 | `updated_at` | `datetime` | ● | `now()` | 后台路由最后更新时间 | 自动 |

### `conversation_module_progress` — 每段对话的结构化模块步骤

当前 **0** 行

> 只保存服务器白名单步骤 key；步骤单向累加，供主 Agent 与 Router 共同使用。

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `conversation_id` | `int` | ● | — | → conversations.id；一段对话一行 ·主键 | 后台 Router Agent |
| 2 | `module_1_steps` | `json` | ● | — | 模块一已完成步骤 key 数组，只可累加 | 后台 Router Agent |
| 3 | `module_2_steps` | `json` | ● | — | 模块二已完成步骤 key 数组，只可累加；新 PA 周期会清空 | 后台 Router Agent |
| 4 | `module_3_steps` | `json` | ● | — | 模块三已完成步骤 key 数组，只可累加；新 PA 周期会清空 | 后台 Router Agent |
| 5 | `module_4_steps` | `json` | ● | — | 模块四已完成步骤 key 数组，只可累加；新 PA 周期会清空 | 后台 Router Agent |
| 6 | `active_cycle_id` | `varchar(36)` | ○ | — | 当前 PA 周期 → pa_cycles.id | 后台 Router Agent |
| 7 | `updated_at` | `datetime` | ● | `now()` | 步骤或周期最后更新时间 | 自动 |

### `pa_cycles` — PA 目标周期

当前 **0** 行

> 一行代表一轮 module 2 → 3 → 4 的 PA 目标生命周期。

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `id` | `varchar(36)` | ● | — | PA 周期 UUID ·主键 | 自动 |
| 2 | `conversation_id` | `int` | ● | — | → conversations.id | 模块跳转时自动写 |
| 3 | `subject_id` | `varchar(64)` | ● | — | = user_profile.uuid | 模块跳转时自动写 |
| 4 | `ordinal` | `int` | ● | — | 该对话内第几轮 PA 目标，从 1 开始 | 模块跳转时自动写 |
| 5 | `status` | `varchar(16)` | ● | `active` | active 或 completed | 模块跳转时自动写 |
| 6 | `started_at` | `datetime` | ● | `now()` | 进入该轮模块二的时间 | 模块跳转时自动写 |
| 7 | `closed_at` | `datetime` | ○ | — | 模块四完成并返回模块二的时间 | 模块跳转时自动写 |

索引：`ix_pa_cycle_subject_status`（subject_id+status）、`uq_pa_cycle_ordinal`（唯一，conversation_id+ordinal）


### `clinical_record_cycle_links` — 业务记录与 PA 周期关联

当前 **0** 行

> 外部业务表禁止 ALTER，因此用本表补上缺失的 cycle_id 关联。

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `id` | `int` | ● | — | 主键，自增 | 自动 |
| 2 | `cycle_id` | `varchar(36)` | ● | — | → pa_cycles.id | 模块临床抽取时自动写 |
| 3 | `module` | `varchar(16)` | ● | — | module_2 / module_3 / module_4 | 模块临床抽取时自动写 |
| 4 | `record_id` | `varchar(36)` | ● | — | 对应外部模块记录表的 UUID；不对外部表声明 FK | 模块临床抽取时自动写 |
| 5 | `created_at` | `datetime` | ● | `now()` | 建立关联的时间 | 自动 |

索引：`ix_clinical_cycle_module`（cycle_id+module）、`uq_clinical_record_cycle`（唯一，module+record_id）、`uq_cycle_module_record`（唯一，cycle_id+module）


### `ai_execution_events` — 统一 AI 执行遥测

当前 **0** 行

> 跨主回复、风险闸门、路由、临床抽取与 Memos 总结的统一遥测。

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `id` | `int` | ● | — | 主键，自增 | 自动 |
| 2 | `conversation_id` | `int` | ○ | — | → conversations.id；删除对话时一并删除 | 每次 LLM 调用自动写 |
| 3 | `assistant_message_id` | `int` | ○ | — | → conversation_messages.id；对应可见回复 | 每次 LLM 调用自动写 |
| 4 | `subject_id` | `varchar(64)` | ○ | — | = user_profile.uuid | 每次 LLM 调用自动写 |
| 5 | `session_id` | `varchar(64)` | ○ | — | 对话 session_id，便于跨表检索 | 每次 LLM 调用自动写 |
| 6 | `stage` | `varchar(32)` | ● | — | main_generation / risk_gate / module_router / clinical_extraction / module_summarizer | 每次 LLM 调用自动写 |
| 7 | `provider` | `varchar(32)` | ○ | — | deepseek / doubao / claude | 每次 LLM 调用自动写 |
| 8 | `model_name` | `varchar(128)` | ○ | — | 供应商实际返回或配置的模型 ID | 每次 LLM 调用自动写 |
| 9 | `duration_ms` | `int` | ○ | — | 该 LLM 阶段总延迟（毫秒） | 每次 LLM 调用自动写 |
| 10 | `input_tokens` | `int` | ○ | — | 输入 token；供应商未返回时为 NULL | 每次 LLM 调用自动写 |
| 11 | `output_tokens` | `int` | ○ | — | 输出 token；供应商未返回时为 NULL | 每次 LLM 调用自动写 |
| 12 | `reasoning_tokens` | `int` | ○ | — | 深度思考 token；供应商未返回时为 NULL | 每次 LLM 调用自动写 |
| 13 | `provider_request_id` | `varchar(128)` | ○ | — | 供应商请求 ID，便于工单追踪 | 每次 LLM 调用自动写 |
| 14 | `finish_reason` | `varchar(32)` | ○ | — | 供应商完成原因 | 每次 LLM 调用自动写 |
| 15 | `error_code` | `varchar(128)` | ○ | — | 统一错误码；成功为 NULL | 每次 LLM 调用自动写 |
| 16 | `prompt_version` | `varchar(64)` | ○ | — | 实际提示词的 SHA-256 短指纹 | 每次 LLM 调用自动写 |
| 17 | `estimated_cost_usd` | `decimal(14,8)` | ○ | — | 按部署价格配置估算的美元成本；无匹配价格为 NULL | 每次 LLM 调用自动写 |
| 18 | `pricing_version` | `varchar(64)` | ○ | — | 价格 JSON 的 SHA-256 短指纹 | 每次 LLM 调用自动写 |
| 19 | `event_metadata` | `json` | ○ | — | 该阶段额外的非敏感结构化指标 | 每次 LLM 调用自动写 |
| 20 | `created_at` | `datetime` | ● | `now()` | 记录时间 | 自动 |

索引：`assistant_message_id`（assistant_message_id）、`conversation_id`（conversation_id）、`ix_ai_event_session_stage`（session_id+stage+created_at）、`ix_ai_event_subject_created`（subject_id+created_at）


### `assessment_entries` — 每日记录（一人一天一行）

当前 **1** 行

> `recorded_on` 是**用户本地日期**，不是 UTC——「我今天记了吗」问的是用户在看的那个墙上时钟。解析所用的时区一并存下，便于日后时区变更时审计。

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `id` | `int` | ● | — | 主键，自增 | 自动 |
| 2 | `subject_id` | `varchar(64)` | ● | — | = `user_profile.uuid` | 每日记录弹窗 |
| 3 | `recorded_on` | `date` | ● | — | 用户**本地**日期 | 每日记录弹窗 |
| 4 | `timezone` | `varchar(64)` | ● | — | 解析出上面那个日期时所用的 IANA 时区名，如 `Asia/Shanghai` | 每日记录弹窗 |
| 5 | `status` | `varchar(16)` | ● | — | `completed`（有完整评分）或 `skipped`（问过了但用户跳过——正是这个值让弹窗当天不再反复弹） | 每日记录弹窗 |
| 6 | `completion_rate` | `int` | ○ | — | 完成度 0–10。skipped 时为 NULL | 每日记录弹窗 |
| 7 | `activity_level` | `int` | ○ | — | 活动量 0–10 | 每日记录弹窗 |
| 8 | `social_connection` | `int` | ○ | — | 社交连接 0–10 | 每日记录弹窗 |
| 9 | `approach_vs_avoidance` | `int` | ○ | — | 趋近 vs 回避 0–10 | 每日记录弹窗 |
| 10 | `overall_mood` | `int` | ○ | — | 整体心情 0–10 | 每日记录弹窗 |
| 11 | `reflection_note` | `text` | ○ | — | 当天的反思备注 | 每日记录弹窗 |
| 12 | `created_at` | `datetime` | ● | `now()` | 创建时间 | 自动 |
| 13 | `updated_at` | `datetime` | ● | `now()` | 更新时间 | 自动 |

索引：`ix_entry_subject_date`（subject_id+recorded_on）、`uq_entry_subject_date`（唯一，subject_id+recorded_on）


### `activity_logs` — 每日记录里的单条活动

当前 **1** 行

> `time_slot` 是自由文本而非时间列：人们会写「早上」「午饭后」，强行解析等于拒绝诚实的答案。

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `id` | `int` | ● | — | 主键，自增 | 自动 |
| 2 | `entry_id` | `int` | ● | — | → `assessment_entries.id`，外键，级联删除 | 每日记录弹窗 |
| 3 | `position` | `int` | ● | — | 卡片显示顺序 | 每日记录弹窗 |
| 4 | `time_slot` | `varchar(64)` | ● | — | 时间段，**自由文本**（「早上」「午饭后」「09:00」都行） | 每日记录弹窗 |
| 5 | `activity` | `varchar(500)` | ● | — | 做了什么 | 每日记录弹窗 |
| 6 | `emotion` | `int` | ● | — | 情绪 0–5 | 每日记录弹窗 |
| 7 | `achievement` | `int` | ● | — | 成就感 0–5 | 每日记录弹窗 |
| 8 | `connection` | `int` | ● | — | 连接感 0–5 | 每日记录弹窗 |
| 9 | `enjoyment` | `int` | ● | — | 愉悦感 0–5 | 每日记录弹窗 |
| 10 | `importance` | `int` | ● | — | 重要性 0–5 | 每日记录弹窗 |
| 11 | `note` | `text` | ○ | — | 备注 | 每日记录弹窗 |

索引：`ix_activity_entry`（entry_id）


---

## 五、业务表（外部系统所有，本应用只读写内容、不碰结构）

### `user_profile` — 用户档案

当前 **19** 行 · 表注释「用户档案表」

> `current_module` 是外部 schema 的历史兼容列，不再作为对话运行态读写。每段对话的权威模块在 `conversation_runtime_states.module`。

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `id` | `bigint unsigned` | ● | — | 用户ID（主键，自增） | 自动 |
| 2 | `uuid` | `char(36)` | ● | — | 用户唯一标识，应用生成uuid4/7 | 注册时生成 |
| 3 | `nickname` | `varchar(64)` | ○ | — | 用户昵称 | 注册页 / 我的档案页 |
| 4 | `age` | `tinyint unsigned` | ○ | — | 年龄 | 注册页 / 我的档案页 |
| 5 | `living_status` | `enum（4 选 1）` | ○ | — | 居住状态 | 注册页 / 我的档案页 |
| 6 | `has_supporter` | `tinyint(1)` | ● | `0` | 是否有支持者 0否 1是 | 我的档案页（由支持者列表推出） |
| 7 | `supporter1_relation` | `enum（6 选 1）` | ○ | — | 支持者1关系 | 我的档案页（投影） |
| 8 | `supporter1_nickname` | `varchar(64)` | ○ | — | 支持者1昵称 | 我的档案页（投影） |
| 9 | `supporter1_influence` | `enum（3 选 1）` | ○ | — | 支持者1影响力 | 我的档案页（投影） |
| 10 | `supporter2_relation` | `enum（6 选 1）` | ○ | — | 支持者2关系 | 我的档案页（投影） |
| 11 | `supporter2_nickname` | `varchar(64)` | ○ | — | 支持者2昵称 | 我的档案页（投影） |
| 12 | `supporter2_influence` | `enum（3 选 1）` | ○ | — | 支持者2影响力 | 我的档案页（投影） |
| 13 | `risk_level` | `enum（3 选 1）` | ○ | — | 风险等级 —— ⚠️ 应用从未读写 | **从未写入** |
| 14 | `current_module` | `enum（5 选 1）` | ○ | — | 当前模块 | 历史兼容（不再作为运行态写入） |
| 15 | `module_completion_flag` | `binary(4)` | ○ | — | 模块完成标记，按位存储 —— ⚠️ 应用从未读写 | **从未写入** |
| 16 | `module_task_completion` | `json` | ○ | — | 模块任务完成标记 —— ⚠️ 应用从未读写 | **从未写入** |
| 17 | `module1_done_flag` | `tinyint(1)` | ● | `0` | 全局不可逆标记：是否已完成模块一 0否 1是，置1后不可回退 | 程序（路由器） |
| 18 | `communication_preference` | `enum（4 选 1）` | ○ | — | 沟通偏好 | 注册页 / 我的档案页 |
| 19 | `reminder_frequency` | `enum（6 选 1）` | ○ | — | 行动提醒频率 | 我的档案页 |
| 20 | `reminder_time_slot` | `enum（6 选 1）` | ○ | — | 行动提醒时间段 | 我的档案页（投影） |
| 21 | `physical_condition` | `set（多选）` | ○ | — | 身体状况，可多选 | 注册页 / 我的档案页 |
| 22 | `behavior_taboo` | `set（多选）` | ○ | — | 行为禁忌，可多选 | 注册页 / 我的档案页 |
| 23 | `content_taboo` | `set（多选）` | ○ | — | 内容禁忌，可多选 | 我的档案页 |
| 24 | `expression_style` | `enum（4 选 1）` | ○ | — | 表达风格偏好 —— ⚠️ 应用从未读写 | **从未写入** |
| 25 | `activity_environment` | `enum（3 选 1）` | ○ | — | 活动偏好-环境 | 我的档案页 |
| 26 | `activity_social` | `enum（4 选 1）` | ○ | — | 活动偏好-社交 | 我的档案页 |
| 27 | `activity_intensity` | `enum（3 选 1）` | ○ | — | 活动偏好-强度 | 我的档案页 |
| 28 | `created_at` | `datetime` | ● | `CURRENT_TIMESTAMP` | 创建时间 | 自动 |
| 29 | `updated_at` | `datetime` | ● | `CURRENT_TIMESTAMP` | 更新时间 | 自动 |

<details>
<summary>可选值（enum / set 的完整取值）</summary>

- **`living_status`** — `独居` · `和家人` · `和朋友` · `和恋人`
- **`supporter1_relation`** — `父母` · `恋人` · `子女` · `朋友` · `兄弟姐妹` · `同事`
- **`supporter1_influence`** — `弱` · `中` · `强`
- **`supporter2_relation`** — `父母` · `恋人` · `子女` · `朋友` · `兄弟姐妹` · `同事`
- **`supporter2_influence`** — `弱` · `中` · `强`
- **`risk_level`** — `低` · `中` · `高`
- **`current_module`** — `开场` · `模块一` · `模块二` · `模块三` · `模块四`
- **`communication_preference`** — `直接明了` · `温柔引导` · `理性分析` · `轻松幽默`
- **`reminder_frequency`** — `每天一次` · `隔天一次` · `每三天一次` · `每周一次` · `仅在我主动找你时提醒` · `暂时不需要提醒`
- **`reminder_time_slot`** — `早晨7-9` · `上午9-12` · `中午12-14` · `下午14-18` · `傍晚18-21` · `晚上21-23`
- **`physical_condition`** — `膝关节损伤` · `腰背酸痛` · `慢性疼痛` · `易疲劳` · `睡眠障碍` · `偏头痛` · `哮喘` · `眩晕` · `鼻炎` · `术后恢复期`
- **`behavior_taboo`** — `不能剧烈运动` · `不能久站` · `不能晒太阳` · `怕吵闹` · `怕人多` · `怕拥挤闭塞的地方` · `不坐公共交通`
- **`content_taboo`** — `不谈工作` · `不谈学习` · `不谈家庭` · `不谈身材外貌` · `不谈感情` · `不谈未来计划` · `不喜欢被比较` · `反感正能量说教` · `不喜欢被经常催促`
- **`expression_style`** — `理性` · `情绪化` · `回避` · `混合`
- **`activity_environment`** — `室内` · `户外` · `都可以`
- **`activity_social`** — `独自` · `一对一` · `群体` · `都可以`
- **`activity_intensity`** — `安静` · `热闹` · `都可以`

</details>


索引：`uk_uuid`（唯一，uuid）


### `module_one_record` — 模块一记录

当前 **2** 行 · 表注释「模块一记录表」

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `id` | `char(36)` | ● | — | ID（主键），应用生成uuid4/7 | 自动 |
| 2 | `created_at` | `datetime` | ● | `CURRENT_TIMESTAMP` | 创建时间 | 自动 |
| 3 | `updated_at` | `datetime` | ● | `CURRENT_TIMESTAMP` | 更新时间 | 自动 |
| 4 | `user_id` | `char(36)` | ● | — | 用户ID/UUID | 模块切换时后台抽取 |
| 5 | `chief_complaint` | `text` | ○ | — | 主诉 | 模块切换时后台抽取 |
| 6 | `distress_duration` | `varchar(64)` | ○ | — | 困扰持续时间，例如：最近两周、三个月、半年以上 | 模块切换时后台抽取 |
| 7 | `distress_frequency` | `varchar(64)` | ○ | — | 困扰频率，例如：每天、每周几次、偶尔、间歇性 | 模块切换时后台抽取 |
| 8 | `trigger_situation` | `text` | ○ | — | 事件触发情境 | 模块切换时后台抽取 |
| 9 | `abc_event` | `json` | ○ | — | 用户完整单一困扰事件（ABC）：A触发事件，B想法/信念，C情绪和行为后果 | 模块切换时后台抽取 |
| 10 | `coping_behavior` | `text` | ○ | — | 用户应对行为 | 模块切换时后台抽取 |
| 11 | `coping_consequence` | `text` | ○ | — | 后果：用户应对行为带来的影响或结果 | 模块切换时后台抽取 |
| 12 | `ai_depression_cycle_summary` | `text` | ○ | — | AI总结的抑郁循环 | 模块切换时后台抽取 |
| 13 | `user_approval_level` | `tinyint` | ○ | — | 用户对该循环的认可程度：0不认可，1部分认可，2认可 | 模块切换时后台抽取 |
| 14 | `attempted_relief_methods` | `json` | ○ | — | 用户尝试过的缓解方式 | 模块切换时后台抽取 |
| 15 | `exception_positive_scene` | `json` | ○ | — | 例外的心情变好的场景 | 模块切换时后台抽取 |

索引：`idx_module_one_updated_at`（updated_at）、`idx_module_one_user_id`（user_id）


### `module_two_record` — 模块二记录（PA 目标卡片在这里产生）

当前 **1** 行 · 表注释「模块二记录表」

> PA 目标卡片**只在这里生成一次**，不会往后复制。模块三谈的是「怎么回报」，模块四是事后 ABC 复盘，两者都只是*引用*这张卡。所以「当前目标有没有 PA 卡片」必须经 `clinical_record_cycle_links` 找到当前周期的模块二记录，再看 `has_target_card_generated`；禁止退回成按用户取最新一行。

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `id` | `char(36)` | ● | — | ID（主键），应用生成uuid4/7 | 自动 |
| 2 | `created_at` | `datetime` | ● | `CURRENT_TIMESTAMP` | 创建时间 | 自动 |
| 3 | `updated_at` | `datetime` | ● | `CURRENT_TIMESTAMP` | 更新时间 | 自动 |
| 4 | `user_id` | `char(36)` | ● | — | 用户ID/UUID | 模块切换时后台抽取 |
| 5 | `pa_understanding_level` | `tinyint` | ○ | — | 用户对PA的理解程度：0不理解，1部分理解，2理解 | 模块切换时后台抽取 |
| 6 | `pa_approval_level` | `tinyint` | ○ | — | 用户对PA的认可程度：0不认可，1部分认可，2认可 | 模块切换时后台抽取 |
| 7 | `core_values` | `text` | ○ | — | 用户的核心价值观 | 模块切换时后台抽取 |
| 8 | `core_values_impact` | `text` | ○ | — | 核心价值观的影响/感受 | 模块切换时后台抽取 |
| 9 | `target_activity_content` | `varchar(255)` | ○ | — | 目标身体活动内容 | 模块切换时后台抽取 |
| 10 | `target_activity_time` | `datetime` | ○ | — | 目标身体活动执行时间 | 模块切换时后台抽取 |
| 11 | `target_activity_location` | `varchar(255)` | ○ | — | 目标身体活动执行地点 | 模块切换时后台抽取 |
| 12 | `target_activity_duration_minutes` | `int` | ○ | — | 目标身体活动执行的单次时长，单位：分钟 | 模块切换时后台抽取 |
| 13 | `target_activity_companion` | `varchar(32)` | ○ | — | 目标身体活动执行人/陪伴人：独自、家人、朋友 | 模块切换时后台抽取 |
| 14 | `potential_barriers` | `json` | ○ | — | 潜在障碍 | 模块切换时后台抽取 |
| 15 | `barrier_coping_plan` | `json` | ○ | — | 障碍应对方案 | 模块切换时后台抽取 |
| 16 | `has_target_card_generated` | `tinyint` | ● | `0` | 目标卡片是否生成：0否，1是 | 模块切换时后台抽取 |

索引：`idx_module_two_target_activity_time`（target_activity_time）、`idx_module_two_updated_at`（updated_at）、`idx_module_two_user_id`（user_id）


### `module_three_record` — 模块三记录

当前 **0** 行 · 表注释「模块三记录表」

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `id` | `char(36)` | ● | — | ID（主键），应用生成uuid4/7 | 自动 |
| 2 | `created_at` | `datetime` | ● | `CURRENT_TIMESTAMP` | 创建时间 | 自动 |
| 3 | `updated_at` | `datetime` | ● | `CURRENT_TIMESTAMP` | 更新时间 | 自动 |
| 4 | `user_id` | `char(36)` | ● | — | 用户ID/UUID | 模块切换时后台抽取 |
| 5 | `ai_record_requirement` | `text` | ○ | — | AI介绍的记录要求 | 模块切换时后台抽取 |
| 6 | `user_acceptance_level` | `tinyint` | ○ | — | 用户对记录的接受程度：0不接受，1部分接受，2接受 | 模块切换时后台抽取 |
| 7 | `user_acceptance_feeling` | `text` | ○ | — | 用户对记录的感受/补充说明 | 模块切换时后台抽取 |
| 8 | `negotiated_record_plan` | `text` | ○ | — | 协商之后的记录方案 | 模块切换时后台抽取 |
| 9 | `has_contract_reached` | `tinyint` | ● | `0` | 是否达成记录与执行契约：0否，1是 | 模块切换时后台抽取 |
| 10 | `difficulty_feedback_mechanism` | `text` | ○ | — | 困难反馈机制说明 | 模块切换时后台抽取 |

索引：`idx_module_three_updated_at`（updated_at）、`idx_module_three_user_id`（user_id）


### `module_four_record` — 模块四记录（ABC 复盘）

当前 **0** 行 · 表注释「模块四记录表」

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `id` | `char(36)` | ● | — | ID（主键），应用生成uuid4/7 | 自动 |
| 2 | `created_at` | `datetime` | ● | `CURRENT_TIMESTAMP` | 创建时间 | 自动 |
| 3 | `updated_at` | `datetime` | ● | `CURRENT_TIMESTAMP` | 更新时间 | 自动 |
| 4 | `user_id` | `char(36)` | ● | — | 用户ID/UUID | 模块切换时后台抽取 |
| 5 | `execution_result` | `tinyint` | ○ | — | 执行结果：1成功，2未执行，3执行受阻，4部分完成 | 模块切换时后台抽取 |
| 6 | `phase_a` | `json` | ○ | — | A：执行前情境、情绪、自动想法 | 模块切换时后台抽取 |
| 7 | `phase_b` | `json` | ○ | — | B：实际行为、行为持续时间、行为是否完整执行、是否有回避/反刍行为 | 模块切换时后台抽取 |
| 8 | `phase_c` | `json` | ○ | — | C：行为中和行为后的情绪状态、身体感受、想法变化、短期影响、长期影响 | 模块切换时后台抽取 |
| 9 | `ai_abc_chain_summary` | `text` | ○ | — | AI总结的ABC链条 | 模块切换时后台抽取 |
| 10 | `user_chain_approval_level` | `tinyint` | ○ | — | 用户对该链条的认同程度：0不认同，1部分认同，2认同 | 模块切换时后台抽取 |
| 11 | `core_difficulty_type` | `varchar(128)` | ○ | — | 核心困难类型 | 模块切换时后台抽取 |
| 12 | `difficulty_description` | `text` | ○ | — | 困难描述 | 模块切换时后台抽取 |
| 13 | `ba_reeducation_content` | `text` | ○ | — | BA再教育内容 | 模块切换时后台抽取 |
| 14 | `next_coping_strategy` | `text` | ○ | — | 协商的下一次应对策略 | 模块切换时后台抽取 |
| 15 | `review_decision` | `tinyint` | ○ | — | 复盘后决策：1继续原目标，2更换目标，3调整目标 | 模块切换时后台抽取 |
| 16 | `review_summary` | `text` | ○ | — | 总结：本次PA执行情况、ABC分析结果、用户行为规律、行为执行带来的收获、遇到的困难、可继续沿用的有效策略等 | 模块切换时后台抽取 |

索引：`idx_module_four_execution_result`（execution_result）、`idx_module_four_review_decision`（review_decision）、`idx_module_four_updated_at`（updated_at）、`idx_module_four_user_id`（user_id）


### `risk_monitoring` — 风险监控

当前 **2** 行 · 表注释「风险监控表」

> **只有判定有风险时才写行**，没风险的轮次什么都不写——所以这张表为空是正常的好消息，不是功能没接上。风险检查每轮都跑，与当前在哪个模块无关。

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `id` | `char(36)` | ● | — | ID（主键），应用生成uuid4/7 | 自动 |
| 2 | `created_at` | `datetime` | ● | `CURRENT_TIMESTAMP` | 创建时间 | 自动 |
| 3 | `updated_at` | `datetime` | ● | `CURRENT_TIMESTAMP` | 更新时间 | 自动 |
| 4 | `user_id` | `char(36)` | ● | — | 用户ID/UUID | 每轮风险闸门（仅有风险时） |
| 5 | `risk_status` | `tinyint` | ● | `0` | 风险监控：0无，1有 | 每轮风险闸门（仅有风险时） |
| 6 | `risk_expression_type` | `tinyint` | ○ | — | 风险表达具体内容：1自伤，2自杀，3未遂 | 每轮风险闸门（仅有风险时） |
| 7 | `risk_expression_time` | `datetime` | ○ | — | 风险表达时间 | 每轮风险闸门（仅有风险时） |
| 8 | `risk_context` | `json` | ○ | — | 风险表达时的上下文：用户在说什么/处在哪个模块等 | 每轮风险闸门（仅有风险时） |
| 9 | `user_reaction_to_risk` | `varchar(255)` | ○ | — | 用户面对风险表达的反应：是否要求暂停干预/换话题等 | 每轮风险闸门（仅有风险时） |
| 10 | `ai_intervention_record` | `json` | ○ | — | AI风险干预记录 —— ⚠️ 应用从未读写 | 每轮风险闸门（仅有风险时） |

索引：`idx_risk_expression_time`（risk_expression_time）、`idx_risk_status_time`（risk_status+risk_expression_time）、`idx_user_id`（user_id）


### `interaction_status` — 互动情况（遥测）

当前 **0** 行 · 表注释「互动情况表」

> 用户级互动分析表。应用现在写入活跃时间、总轮数、周频率、模块停留、完整周期数与目标历史；没有数据依据的临床趋势字段仍保持 NULL。

| # | 字段 | 类型 | 空 | 默认 | 说明 | 写入来源 |
|--:|---|---|:-:|---|---|---|
| 1 | `id` | `char(36)` | ● | — | ID（主键），应用生成uuid4/7 | 自动 |
| 2 | `created_at` | `datetime` | ● | `CURRENT_TIMESTAMP` | 创建时间 | 自动 |
| 3 | `updated_at` | `datetime` | ● | `CURRENT_TIMESTAMP` | 更新时间 | 自动 |
| 4 | `user_id` | `char(36)` | ● | — | 用户ID/UUID ·唯一 | 每轮对话 / 模块周期后台更新 |
| 5 | `last_active_at` | `datetime` | ○ | — | 用户最近一次活跃时间 | 每轮对话 / 模块周期后台更新 |
| 6 | `consecutive_inactive_days` | `int` | ● | `0` | 用户连续无记录/无交互累计天数 | 每轮对话 / 模块周期后台更新 |
| 7 | `total_interaction_count` | `int` | ● | `0` | 用户历史总交互次数 | 每轮对话 / 模块周期后台更新 |
| 8 | `avg_weekly_interaction_frequency` | `decimal(8,2)` | ○ | — | 用户平均每周交互频率 | 每轮对话 / 模块周期后台更新 |
| 9 | `good_response_behaviors` | `json` | ○ | — | 响应良好行为：微小调整、略带挑战等 | 每轮对话 / 模块周期后台更新 |
| 10 | `easy_stuck_modules` | `json` | ○ | — | 容易停滞的模块 | 每轮对话 / 模块周期后台更新 |
| 11 | `decline_behavior_signals` | `json` | ○ | — | 状态下滑的行为信号：开始不洗头、推迟回微信、赖床到下午、一天只吃一顿饭等 | 每轮对话 / 模块周期后台更新 |
| 12 | `decline_somatic_signals` | `json` | ○ | — | 状态下滑的躯体信号：脑雾、偏头疼、手无力等 | 每轮对话 / 模块周期后台更新 |
| 13 | `decline_cognitive_signals` | `json` | ○ | — | 状态下滑的认知信号：开始觉得自己没用、没价值等 | 每轮对话 / 模块周期后台更新 |
| 14 | `full_m2_m3_m4_cycle_count` | `int` | ● | `0` | 完整“模块二—三—四”循环次数 | 每轮对话 / 模块周期后台更新 |
| 15 | `longest_stay_module` | `varchar(64)` | ○ | — | 停留时间最长的模块 | 每轮对话 / 模块周期后台更新 |
| 16 | `goal_history` | `json` | ○ | — | 完整的目标历史：目标及对应结果 | 每轮对话 / 模块周期后台更新 |
| 17 | `behavior_activation_level_change` | `json` | ○ | — | 行为激活水平变化：活动量、时间、频率、类型的变化 | 每轮对话 / 模块周期后台更新 |
| 18 | `overall_emotion_trend` | `varchar(255)` | ○ | — | 总体情绪状态趋势：改善、平稳、下降、波动等 | 每轮对话 / 模块周期后台更新 |
| 19 | `has_entered_closure_or_transition` | `tinyint` | ● | `0` | 是否已经进入结案/过渡阶段：0否，1是 | 每轮对话 / 模块周期后台更新 |
| 20 | `self_coaching_confidence_level` | `tinyint` | ○ | — | 用户对成为自己的教练的信心程度：0没信心，1部分有信心，2有信心 | 每轮对话 / 模块周期后台更新 |
| 21 | `reaction_to_ai_coach_ending` | `text` | ○ | — | 用户对结束或减少AI教练引导的情绪反应 | 每轮对话 / 模块周期后台更新 |

索引：`idx_closure_or_transition`（has_entered_closure_or_transition）、`idx_consecutive_inactive_days`（consecutive_inactive_days）、`idx_last_active_at`（last_active_at）、`uk_user_id`（唯一，user_id）


---

## 六、几件排查问题时会救命的事

**1. 每段对话的权威模块在 `conversation_runtime_states.module`。**
`user_profile.current_module` 只剩外部 schema 兼容用途，不再由对话流程读写；
`module1_done_flag` 仍是用户级单向棘轮：0 → 1，永不回退。

**2. `risk_monitoring` 是空的属于正常。** 只有判定出风险的轮次才写行；没风险不写。
空表 = 没人触发过风险，不等于功能没接上。

**3. 模块记录表每轮周期追加一行，不是每人一行。** 模块二、三、四必须通过
`clinical_record_cycle_links` 按当前 `pa_cycles.id` 读取，禁止只按用户取最新行。

**4. PA 目标卡片只在 `module_two_record` 生成一次**，不往后复制。
「当前周期有卡片了吗」= 沿当前 cycle 的关联找到 module 2 记录，再看 `has_target_card_generated`。

**5. 删除一段对话不会删掉临床记录。** 那些记录挂在**人**身上（`user_id`），不挂在对话上——
它们没有任何指向 `conversations` 的列。这是设计如此，不是 bug。

**6. `interaction_status` 已接入。** 活跃时间、轮数、周频率、模块停留、完整周期数和目标历史会自动写；
没有经过业务确认的临床趋势字段仍保持 NULL。

**7. 下列字段结构上存在，但应用从未读也从未写：**

| 字段 | 说明 |
|---|---|
| `user_profile.risk_level` | 风险等级。风险信息实际写在 `risk_monitoring` |
| `user_profile.expression_style` | 表达风格。有意不开放——这是**对**一个人的评估，不是 ta **自己**的陈述 |
| `user_profile.module_completion_flag` | 按位存储的模块完成标记 |
| `user_profile.module_task_completion` | 模块任务完成标记 |
| `risk_monitoring.ai_intervention_record` | AI 干预记录 |

**8. 密码和令牌都不是明文。** `password_hash` 是 Argon2id，`token_hash` 是 SHA-256。
拿到数据库导出也拿不到任何一个可用的登录态。

---

## 七、数字编码口径（已对齐）

代码提示词与写入校验均以业务表列注释为权威：认可/理解程度 0–2；
`execution_result` 1–4；`review_decision` 1–3；`risk_expression_type` 1–3。
`risk_expression_type=3` 只表示「未遂」，不能用来表示尚未实施的具体计划。
完整整改和兼容策略见根目录 `DATABASE_WORKFLOW_REMEDIATION.md`。

---

## 八、最近一次数据库重构：与之前相比改了什么

最近一次结构性整改针对的是“模块状态与临床数据能否长期保持一致”，不是简单增加几个字段。
7 张外部业务表保持原结构；新增能力全部放在应用自有表或兼容代码中。

| 主题 | 更动前 | 现在 | 优化价值 |
|---|---|---|---|
| 数字编码 | 认可程度在代码中按 0–5，业务表按 0–2；`execution_result=3` 会被旧校验丢弃；风险类型 3 的含义冲突 | 认可/理解统一 0–2；执行结果统一 1–4；复盘决策 1–3；风险类型 1–3，且 3 只表示“自杀未遂” | 避免临床含义被静默改写或数据直接丢失 |
| 模块子步骤 | 只有 `module + memory`，Router 主要从长对话文本猜测是否完成 | 新增 `conversation_module_progress`，四个模块分别保存白名单步骤 key，单向累加 | 多轮流程可恢复、可查询，不会因摘要或上下文裁剪而反复问同一步 |
| PA 目标周期 | 模块二、三、四各自按用户取最新一行；第二轮目标可能串到第一轮记录 | 新增 `pa_cycles` 与 `clinical_record_cycle_links`，每轮目标有独立 cycle，临床记录按 cycle 关联 | 多轮 PA 目标不会互相污染，模块四总能复盘正确的目标卡 |
| 当前模块 | 会话表有运行态，同时还向用户级 `user_profile.current_module` 写入；两个设备/两段对话会互相覆盖 | `conversation_runtime_states.module` 成为每段对话唯一权威；`user_profile.current_module` 只保留兼容展示 | 同账号并行对话彼此独立，Agent 回答“当前模块”有确定来源 |
| 互动统计 | `interaction_status` 有表但没有接入 | 每轮更新活跃时间、总轮数、周频率、模块停留；完成闭环时更新周期数与目标历史 | 可以做停滞识别和周期统计，不再只有空壳字段 |
| AI 可观测性 | 只有零散模型名/思考内容，难以定位“慢在哪里、为什么失败” | 新增 `ai_execution_events`，并在 `conversation_messages` 保存首 token、各阶段耗时、token、请求 ID、错误码与提示词指纹 | 可以区分主模型、风险闸门、Router、抽取和总结的性能/错误 |
| 多设备同步 | 新消息与后台补写路由思考缺少统一变化标记 | `conversations.revision` 在消息变化或后台补写时递增 | 前端能按修订号判断是否需要同步，而不是靠猜测消息数量 |
| 删除完整性 | 只删主对话时，运行态或后台结果可能残留，甚至被仍在运行的任务写回来 | 删除前等待并加锁，再显式清理消息、运行态、步骤、周期链接、遥测与内存会话 | 不会“删了又复活”，也不会遗留应用侧孤儿数据 |

### 为什么没有直接 ALTER 7 张业务表

`user_profile`、四张模块记录表、`risk_monitoring`、`interaction_status` 属于外部系统，项目约束明确禁止擅自改表。
因此本次用 sidecar（旁挂表）补齐步骤、周期和遥测：既能获得新能力，也不破坏 DMS 中既有字段、旧数据或其他系统的读取契约。

### 数据迁移与兼容处理

- 线上旧记录中发现的 `module_one_record.user_approval_level=5` 已按旧制“完全认可”幂等迁移为新制 `2=认可`。
- 已有风险记录没有修改或删除；具体计划但未实施仍归入 `risk_expression_type=2`，计划细节保存在 `risk_context`。
- 旧对话没有步骤或 PA 周期行时可以继续读取；新 sidecar 表按需要逐步创建，不要求一次性回填伪造状态。
- 最近新增的“查看历史每日记录”没有新增表：它只按登录用户分页读取既有 `assessment_entries` 与 `activity_logs`，因此不存在重复存储。

更完整的缺陷逐项处理、触发规则和测试证据见 `DATABASE_WORKFLOW_REMEDIATION.md`。

---

## 九、这份文档怎么重新生成

结构变了就重跑，**不要手工改字段表**——它们是生成的，手改会在下次生成时丢掉。
在 `backend/` 目录下：

```bash
python scripts/dump_schema.py && python scripts/gen_database_doc.py
```

第一步读 `backend/.env` 里的 `DATABASE_URL`，查 `information_schema` 的
COLUMNS / TABLES / STATISTICS 三张视图，落到 `scripts/schema.json`；
第二步把它渲染成本文件。「说明」「写入来源」两列的人工内容在
`scripts/gen_database_doc.py` 里的 `DESC` / `WRITER` 字典中维护。
