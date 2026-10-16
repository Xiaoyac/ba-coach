# 目标与记录统一入口

状态：已于 2026-10-10 部署，线上版本 `20261010T070658Z`，上一版本 `20261009T123302Z`。

对应待办第 3、5 项。依用户确认，M1–M4 均可使用“目标与记录”，目标的正式设置和确认仍沿用聊天中的模块流程。

- 普通用户顶部菜单、管理员侧栏合并为“目标与记录”。内部“目标 / 每日记录”导航共用一个弹窗，目标档案和每日表单以嵌入方式显示，不嵌套两个 dialog。
- 目标档案仍为只读，提供“返回对话讨论目标”；查看资料不会改动当前目标、模块或数据库写入规则。
- M3 的“记录今天 / 修改今天”快捷入口直达每日记录页。M1/M2/M4 可从统一入口补填、查看和修改历史记录。
- 两个页签切换保留填写内容和目标浏览位置。每日记录有未保存内容时，关闭或 Escape 先提示继续填写或放弃；保存请求未结束时不关闭。刷新或离开网页由浏览器提示未保存内容。保存失败和版本冲突保持表单。
- 继续使用已有每日记录接口、日期和乐观并发规则，不合并目标计划、实际活动和每日总体评分的数据。

## 修改文件

- `frontend/components/GoalsAndRecordsModal.tsx`：新统一容器、导航和关闭保护。
- `frontend/components/ConversationWorkspace.tsx`：原入口指向统一容器，跨模块入口保持可用。
- `frontend/components/Chat.tsx`：普通用户顶部合并入口；M3快捷记录保留。
- `frontend/components/ConversationSidebar.tsx`：管理员侧栏合并入口。
- `frontend/components/GoalOverview.tsx`：支持嵌入，统一窗口内减少重复标题和页脚。
- `frontend/components/DailyAssessmentModal.tsx`：支持嵌入，报告未保存与提交状态；保存成功清除未保存标记。
- `infra/qa/goals-records-1009.cjs`：合成浏览器验收，不写入真实用户数据。

## 验证

- Next 生产构建和 TypeScript 检查。
- 浏览器测试覆盖 1440×900、390×900、360×640，明/暗两主题及管理员入口。
- 每组普通用户测试覆盖 M1–M4 统一入口、目标档案卡和计划详情、M3快捷直达、切页签保存草稿、Escape关闭保护、保存失败后重试、M4历史记录修改、版本冲突后保留，以及成功保存前后活动原有零分/版本绑定。
- 测试有网络层 `/api/` 兜底拦截，所有数据为合成夹具。
- 截图和测试结果在 `.test-tmp/requirements-1009/browser/`；任务前备份在 `.test-tmp/requirements-1009/before-frontend/`。
- 另核对管理员两段会话的目标概览、详情均携带 `session_id`，切换账户时销毁旧弹窗和未保存草稿。
- 第 6 项知识库、第 4 项每日漏填提醒、第 11 项不包含在本次范围内。
- 部署后针对 `https://bacoach.xyz` 的真实前端资源重新运行 7 组界面测试及管理员双会话、切账号隔离测试，均通过。接口数据仍由合成夹具提供并拦截，未写入生产用户记录；结果分别在 `public-browser-results.log` 和 `public-isolation-results.log`。

## 并行修改隔离

构建候选最终基于线上 `20261009T123302Z`，保留该版本的会话目标隔离、每日活动评分文案。发布只使用 `.test-tmp/requirements-1009/release-overlay/frontend/` 内 6 个文件；清单为 `frontend-overlay-manifest.json`，相对线上补丁为 `frontend-overlay.patch`。设置页面 `SettingsLink` 等功能已在 `20261009T123302Z` 上线，最终候选完整保留该线上版本，只叠加本次统一入口。此前基于脏工作区生成的 `frontend-task.patch` 和 `frontend-manifest.json` 仅作过程备份，不应用于发布。
