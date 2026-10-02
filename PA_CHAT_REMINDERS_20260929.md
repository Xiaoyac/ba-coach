# 活动后站内提醒与记录入口

## 已上线的每日记录按钮

- 提交：`635a24882a7025a0c4491a3c2ef0833a7c954c2a`，已推送 GitHub `main`。
- 线上版本：`/opt/bacoach/releases/20260929T003521Z`。
- `Chat.tsx`：M3 的每日记录入口从输入框下方的小字移到输入框上方，使用独立卡片和 44px 高按钮；生成中也可打开。
- 线上 235 个源文件哈希匹配发布清单；三个原有服务正常，网站 HTTP 200；模型、密钥和环境文件哈希未变。
- 线上静态资源配合隔离的模拟 API，在 1440、390、320px 验证按钮、两种主题和记录功能；没有写入真实用户数据。

## 站内提醒的行为

本功能代码与上面的按钮部署分开。站内提醒需要下面的显式迁移和独立服务启用，不能仅靠 Git 推送生效。

1. 按已确认 PA 计划的**开始时间＋活动时长＋1 小时**计算到期时间；不是目标卡创建后一小时，也不推断用户实际执行完毕。
2. 每 30 秒检查一次。写入原计划确认所在聊天的普通助手消息，页面通过已有 SSE／revision 同步显示；用户离线时也保存，回来可见。
3. 无需浏览器通知授权、VAPID、活跃登录或额外 LLM 请求。消息明确标注“活动后提醒”，没有模型推理或伪造模型名称。
4. 复用现有计划时间解析、提醒偏好和活动反馈核验：日期/周期、开始时间、时长须明确；“午饭后”等模糊表达不排程。计划必须当前有效且有真实来源。
5. 只在原聊天仍绑定该目标和周期、处于等待执行时发送。暂停、沙盒、正在复盘或等待确认的聊天不插入提醒。未完成的用户轮次或最近两分钟内的消息暂缓提醒。
6. 已报告做了／没做、取消或改期，或已经记录对应活动及时间的，不重复催促。用户档案中的拒绝提醒、提醒频率和时间段仍生效；站内与浏览器通知分别计数。
7. 每个目标、每次计划开始时间仅一次。去重凭据、聊天消息、revision 同一事务提交；失败会整体回滚。不会确认记录、推进模块、调用 Router 或更改聊天最近交互时间。
8. 沿用现有提醒的 30 分钟有效窗口，避免服务停机后补发过期消息。正常情况下是到期后下一次扫描送达；忙碌、提醒时段限制或服务故障可能延后或跳过，并不保证整点送达。
9. 管理员 `router_only` 对话若没有正式确认的 PA 计划，不会仅凭助手口头承诺产生提醒。

示例：计划 16:00 散步 15 分钟，17:15 后的下一次扫描写入：

> 活动后提醒：按原计划，「散步」在09月29日 16:15结束，现在已过约一小时。如果方便，可以打开「每日记录」记下活动与心情；还没做或计划有变化，也可以告诉我。

## 文件职责

| 文件 | 改动原因 |
| --- | --- |
| `backend/app/pa_chat_reminders.py` | 定时筛选、事务内复核、去重、写入真实聊天并更新 revision |
| `backend/app/chat_reminder_schema.py` | 独立站内提醒凭据，不伪装成浏览器设备，不修改业务表 |
| `backend/app/pa_push.py` | 提醒偏好核验接受指定的发送记录表，复用频率逻辑，浏览器推送默认行为不变 |
| `backend/app/config.py` | 新增默认关闭的启用开关，未迁移前不会意外写消息 |
| `backend/scripts/pa_chat_reminder_worker.py` | 独立后台进程、只读预检与单次扫描，无浏览器推送依赖 |
| `backend/scripts/migrate_pa_chat_reminders.py` | 复用已有迁移工具的目标库和结构检查，只创建一张空表 |
| `infra/deploy/bacoach-pa-chat-reminders.service` | 长期运行站内提醒进程，与 Web Push 分开启停 |
| 两份 `infra/deploy/server-deploy*.sh` | 后续发布时同步切换已启用的站内提醒进程，失败时回滚 |
| `backend/tests/test_pa_chat_reminders.py` | 到期、离线、重启、并发、回滚、偏好、取消、排程变更、消息排序和历史读取验证 |
| `infra/qa/pa-chat-reminder-0929.cjs` | 电脑和手机无需用户发言即可通过已有聊天同步显示提醒，并可打开记录 |

## 首次上线步骤

本地验证：26 项站内提醒专项测试通过；原有 Web Push／诊断通知回归通过；后端完整测试集通过（后续增加的迁移、开关及确认来源专项测试单独复验通过）。电脑与手机的模拟 SSE 提醒显示和记录弹窗测试通过。没有在生产用户聊天中发送测试提醒。

将代码发布到服务器后，在服务器中加载现有后端环境（不要输出密钥），进入新版本 `backend` 目录，设置 `PYTHONPATH=.`。

```bash
.venv/bin/python scripts/migrate_pa_chat_reminders.py --expected-database ba_coach_260908
.venv/bin/python scripts/migrate_pa_chat_reminders.py --expected-database ba_coach_260908 --apply
PA_CHAT_REMINDERS_ENABLED=true .venv/bin/python scripts/pa_chat_reminder_worker.py --check
```

预检只读，不会发消息。把本仓库的 `infra/deploy/bacoach-pa-chat-reminders.service` 安装到 `/etc/systemd/system/` 后：

```bash
systemctl daemon-reload
systemctl enable --now bacoach-pa-chat-reminders.service
systemctl is-active bacoach-pa-chat-reminders.service
```

服务单元只给此进程设置 `PA_CHAT_REMINDERS_ENABLED=true`，不修改模型、密钥、URL 或现有推送配置。后续代码发布脚本会重启已经启用的此服务，不会自动启用一个尚未迁移的服务。

停用：`systemctl disable --now bacoach-pa-chat-reminders.service`。已经写入的历史提醒保留；不删除表或真实聊天。首次启用若失败，停用服务，保留空表即可；不要重跑真实到期活动作为测试。
