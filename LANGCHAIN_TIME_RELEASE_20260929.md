# BA Coach：LangChain 与对话时间部署记录

部署完成：2026-09-29（北京时间）。站点：https://bacoach.xyz/

## 版本与范围

- 新版本：`/opt/bacoach/releases/20260929T122040Z`。
- 保留的回滚版本：`/opt/bacoach/releases/20260929T023510Z`。
- 以实际线上源码为基线，保留比原工作目录更新的业务、分享、Router 模式和前端功能。
- 部署源码在 `C:/Users/admin/.codex/worktrees/langchain-conversation-time/ba-coach`，分支 `codex/langchain-conversation-time`。原工作目录未被覆盖。
- 没有执行数据库迁移、知识库导入或修改线上密钥/模型配置。本次未访问或部署用户要求暂时忽略的另一项目。

## 实现

1. LangChain Core 的 `ChatPromptTemplate`、`MessagesPlaceholder`、`trim_messages` 已用于普通四模块及危机回复的上下文。按真实消息角色裁剪，保留系统片段和缓存标记，保留当前输入及原有用户内容包裹方式，排除历史内部推理。
2. 消息时间由服务器产生，UTC 保存；历史恢复、数据库边界读取、路由和信息提取保留时间。UTC 无时区的数据库返回值按 UTC 解释。新消息采用秒精度，与生产 MySQL 的时间精度一致。
3. 每轮向主模型追加不可缓存的当前北京时间、星期和历史消息时间对照，区分消息时间与事件时间；相对日期以相应消息时间为参照，未知历史时间不猜测，不能仅凭时间推断计划已完成。
4. 界面显示每条消息的北京时间。流式服务返回服务器时间，替换界面发送时的临时本机时间；刷新/同步仍采用原始记录时间。没有可靠时间的历史记录不会显示虚构时间。已有分享快照的字段和内容保持原状。
5. 运行指标记录 `context_pipeline.engine=langchain`、版本、裁剪前后消息数及耗时。线上运行 `langchain-core 1.6.3`；没有新增模型调用。

## 验证

- 相关专项回归最终 **117 项通过**；取消生成、会话裁剪、上下文锚点、推理分离及删除恢复回归 **36 项通过**，共 **153 项通过**。
- 新增时间测试覆盖跨午夜、星期、UTC/北京时间换算、旧记录未知时间、正文不能覆盖系统时钟、裁剪后的时间编号、历史恢复、路由时间、流式与数据库时间一致性。
- 全量运行 **1820 项，1776 项通过、44 项失败**。将这 44 项逐一在改动前的线上源码快照复跑，**44 项均失败**。其中一项旧消息字段快照断言已更新并通过，另外 **43 项既有失败尚未解决**，涉及原有回复校验、业务流程及模型默认值等。本次没有把全量结果标记为通过。
- 本地前端类型检查与生产构建通过；消息组件渲染验证了北京时间跨午夜显示，以及未知/异常时间不产生虚构日期。
- 首次部署的上线前检查发现 Python 3.12 与线上 Python 3.10 的 f-string 语法差异，未切换流量。修复后新增服务器全量后端语法检查及应用导入检查，再次部署通过。
- 线上前端构建、依赖一致性、后端上下文加载检查通过；后端、前端、提醒服务均 active，健康接口正常。
- 线上 **238 个源码文件**与部署包 SHA-256 一致。
- 公网首页与真实 `/api/chat` 均返回 200；服务器用户/助手时间字段正常。匿名合成测试未创建账户或持久化对话，其内存会话已删除（204）。

真实模型答复：

> 今天是2026年9月29日，星期二。明天是2026年9月30日，星期三。你计划今天晚饭后散步十分钟，明天再聊感受，记下了。

## 证据

- `output/deploy-langchain-time/deploy-final.log`：服务器构建、预检、切换与健康检查。
- `output/deploy-langchain-time/online-smoke.json`：真实匿名对话验证。
- `output/deploy-langchain-time/remote-source-verification.json`：发布源码一致性。
- `timestamp-langchain-tests.xml`、`time-regression-tests.xml`：相关回归结果。
- `backend-tests-time.log`、`baseline-comparison.xml`：全量及改动前基线对照。

回滚需将 `/opt/bacoach/current` 恢复为上述旧版本，并重启 `bacoach-backend`、`bacoach-frontend` 和 `bacoach-pa-push`；本次没有数据库结构变动。
