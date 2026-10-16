# 快速承接与后台 PA 的动态时间后置

本次只调整请求排列，没有缩短历史窗口、删除消息、修改数据库事实或改变 PA 工具执行规则。

快速承接在已支持后置系统消息的通道上启用现有 `prepare_context(prefix_cache=True)`：

```text
system：固定策略与时间解释规则
user / assistant：原有历史（用户消息保留原发送时间）
system：本轮时间、相对日期说明
user：本轮输入
```

后台 PA 将本轮时钟标为 `cacheable=False, after_history=True`，复用 native tool adapter 的 `ordered_messages`：

```text
system：模块规则、PA 工具规则、后台职责
user / assistant：已有历史
system：本轮消息发送时间、服务器当前时间
user：本轮输入
assistant tool_call / tool：应用预读的真实卡片结果
assistant tool_call / tool：之后各次调用与结果
```

动态时钟始终在本轮用户消息之前，不插入工具调用与结果之间。同一个后台任务续轮保持已有消息不变，追加原生工具结果；改变下一轮时钟不会再提前改变历史前面的系统提示词。

验证：90 项相关测试通过。新增测试捕获实际 SDK HTTP 请求，验证变化时钟下稳定前缀相同、历史时间与内容未改写、空历史有效、K3 low 参数保留、两轮工具的 ID/结果/思考字段及 trace 中 wire_messages 与真实序列一致。数据库 worker 集成测试验证真实后台入口创建后置时钟，而非只测试手工构造的 prompt。

已部署至 `20261008T093217Z`（北京时间 2026-10-08 17:32），以前一线上版本 `20261008T092258Z` 为底稿，仅更新 `provisional_reply.py` 和 `pa_background.py`，保留其他任务的线上更新。

部署前在服务器运行 SDK 请求捕获检查，覆盖有/无历史的快速承接、变化时钟和后台 PA 原生工具续轮；应用编译与导入通过。部署后核对前后端进程均运行于新版本，后端健康检查正常、公开首页返回 HTTP 200。前端沿用原构建，无数据库迁移或模型配置修改。

未用离线测试推算线上缓存命中率；真实改善需上线后按相同时间窗口及调用阶段比较。
