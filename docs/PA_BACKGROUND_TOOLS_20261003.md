# PA 卡片后台工具执行（2026-10-03）

PA 卡片的原生工具调用已从前台回复模型移到独立的后台 LLM。前台使用普通 complete/stream 接口，回复持久化后启动后台任务，不等待查询、保存、校验或确认轮次，也不再为了工具结果额外生成一次正文。

## 执行与并发

- 后台沿用系统 Router 模型，创建独立 provider wrapper，不继承前台 deep/max 档位。线上为 Kimi K3；Ark 必须思考，实际请求为 reasoning_effort=low，输出上限 4096，单请求超时 30 秒，整项工具任务仍受既有轮数与总超时限制。
- 后台 PA 任务与模块路由等待队列分开。确认语义判断在数据库会话关闭后执行，重新加锁时核对原始文本、所有权、消息边界与版本。
- 只有对应用户消息及紧随其后的已持久化助手回复仍为当前边界时才允许写入。新消息、删除会话或版本变化会使旧任务失效。
- 当前生成的前台回复只作为完成边界，不作为新的用户意愿、评分、确认或复盘证据。
- 正式卡片展示文本、确认标记与 ready 状态在同一事务提交。后台结束后更新会话缓存与 revision，前端通过既有同步机制读取结果。
- 沿用现有功能开关。线上 GOAL_CARD_UI_ENABLED=true，PA_CARD_TOOLS_ENABLED=false；已启用的 M2 目标卡工具现在走后台。独立 M4 原生工具开关保持关闭；启用该开关时也使用后台路径，已有 M4 普通后台持久化保持原流程。

## 验证

完整后端回归：2537 passed、2 skipped、3 warnings，93.41 秒。包括 M2/M4、普通与流式回复、最高思考档位且无快速开头、后台模型阻塞时新消息继续回复、旧任务拒绝写入、核心/次要卡原子展示与确认、关停取消。

真实生产配置模型的合成测试通过：get_pa_card → save_pa_card（时长 10 改为 15 分钟）→ continue_pa_conversation，3 个原生工具请求，14.419 秒；没有前台生成调用、没有最终措辞请求、没有生产数据库写入。测试耗时用于验证异步链路，不代表实际用户整体延迟的固定改善值。

## 发布

- 当前 release：/opt/bacoach/releases/20261003T054553Z。
- 可回滚 release：/opt/bacoach/releases/20261003T040846Z。
- 15 个部署源文件逐个 SHA-256 匹配；运行中后端 cwd 已指向新 release，主回复节点不再引用 PAToolReply。
- 后端 health 正常，前后端服务 active，公网 HTTPS 返回 200。
- 本次没有数据库迁移，也没有发布其他本地功能修改。Git 工作区仍保留既有未提交修改；生产发布不代表 Git 已提交或推送。

可复核证据：.test-tmp/pa-background-1003/verification.json、live-probe.json、live-runtime.json、final-backend-regression.log、manifest.json。
