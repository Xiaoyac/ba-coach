# BA Coach 技术交接与 macOS 迁移说明

更新时间：2026-09-03

这份文档用于把当前 Windows 开发目录迁移到 macOS，并记录系统架构、关键链路、已知问题、部署方式和下一步修复优先级。文档不包含 API Key、数据库密码、SMTP 密码、SSH 私钥或用户数据。

## 1. 项目定位与当前状态

BA Coach 是一个以行为激活（Behavioral Activation）为核心的心理支持 Web 应用。

- 前端：Next.js 15、React 19、TypeScript、Tailwind CSS v4。
- 后端：FastAPI、LangGraph、SQLAlchemy Async。
- 生产数据库：阿里云 RDS MySQL。
- 本地默认数据库：SQLite，仅用于开发和隔离测试。
- 模型：Claude、DeepSeek、豆包，通过各 provider adapter 调用。
- 长期记忆：远程 MemOS/OpenMem 服务。
- 知识检索：MySQL 知识表加进程内词法索引。
- 正式站点：`https://bacoach.xyz`。

项目根目录当前原本不是 Git 仓库。首次迁移会在根目录初始化 Git，并将源码、测试、知识库和基础设施配置纳入版本控制；本机环境文件、日志、数据库、依赖目录和构建产物不会上传。

## 2. 目录结构

```text
backend/                  FastAPI、LangGraph、模型 provider、数据库及测试
frontend/                 Next.js 前端
KnowledgeBase/            BA、PA、MI、BCT 知识源文件
infra/deploy/             生产部署脚本
infra/nginx/              Nginx 配置
infra/systemd/            后端与前端 systemd 服务
infra/frp/                旧 FRP/证书续期配置
infra/windows/            Windows 本地运行历史配置
README.md                 完整开发和架构说明
DEPLOY.md                 当前生产部署、日志、回滚说明
DATABASE.md               数据库结构说明
DATABASE_WORKFLOW_REMEDIATION.md
                          数据库与工作流整改记录
```

## 3. 核心请求链路

### 3.1 对话和工作流

每轮对话大致执行：

```text
请求进入 /api/chat 或 /api/chat/stream
  -> 解析可选登录身份 subject_id
  -> extract_memory：加载会话、当前模块和持久记忆
  -> analyze_intent：读取本轮应执行的模块
  -> recall_memory：特定节点查询 MemOS
  -> module_1/2/3/4：检索知识、拼装 Prompt、调用模型
  -> route_next_module：标记后台路由待执行
  -> summarizer：执行规则型收尾任务
  -> update_memory_and_format：保存消息并返回结果
  -> 响应完成后运行 Router Agent 和模块转换任务
```

LangGraph 只负责状态与 DAG 编排。模型请求由 `backend/app/providers/` 中的官方 SDK adapter 直接完成。

`subject_id` 是认证账号对应的用户档案 UUID；`session_id` 是单个对话。两者不可混用。

### 3.2 Prompt 组成

模块 Prompt 会组合：

- 稳定的全局与模块提示词；
- 用户档案；
- 临床结构化数据；
- 当前模块与 PA card；
- 当前会话历史；
- MemOS 长期记忆；
- 本轮检索到的知识片段。

稳定提示词与动态上下文分段，便于利用 provider 的 Prompt cache。

## 4. 身份、档案与当前已知故障

当前认证使用 bearer access token：登录后前端把 token 写入 `localStorage`，后续请求通过 `Authorization: Bearer ...` 发送。后端把 token 哈希后查询 `auth_sessions`，解析出 `subject_id`。

### 4.1 已观察到的故障

移动端打开“我的档案”时出现 `Not authenticated`。这个错误来自鉴权层，而不是档案查询层：请求没有携带可解析的 bearer token，或者 token 已过期、session 不存在、环境不一致。

当前最明确的客户端缺陷是：`setToken()` 写入 `localStorage` 失败时会吞掉异常，但登录 UI 仍进入工作区，而且没有真正的内存 token 回退。Safari 存储受限时可能产生“看起来已登录，实际请求没有 token”的假登录状态。

聊天路由允许匿名身份，所以鉴权丢失后可能仍能聊天，但会同时失去：

- 档案和临床上下文；
- MemOS 查询和保存；
- 对话持久化；
- 跨设备连续性。

### 4.2 建议修复顺序

1. `setToken()` 增加真正的进程内回退，并显式返回存储结果。
2. 登录成功后立即调用 `/api/auth/me`，验证通过后才能进入工作区。
3. 建立统一 401 处理器：清空登录状态、提示会话失效、返回登录页。
4. 已登录 UI 不得静默降级为匿名聊天。
5. 生产同域环境改为 `HttpOnly + Secure + SameSite=Lax` 的 host-only session cookie，并为写操作增加 CSRF/Origin 校验。
6. 服务端日志只记录缺失、格式错误、不存在、已过期等原因，不记录原始 token。

线上诊断时先检查失败 `/api/profile` 请求是否带有 Authorization。没有请求头时查前端存储；有请求头但仍为 401 时查 `auth_sessions`、过期时间、数据库环境和代理配置。

## 5. Thinking/Reasoning 泄漏

### 5.1 当前实现风险

DeepSeek 和豆包 adapter 可启用 thinking，并读取 `reasoning_content`。后端当前还会：

- 通过 SSE 发出 `reasoning_delta`；
- 在 JSON 响应中携带 reasoning；
- 把 reasoning 写入 `conversation_messages.reasoning_content`；
- 由前端在“查看深度思考”区域展示。

因此原始 reasoning 已经离开服务端，前端隐藏或折叠并不是安全边界。

系统已有 `backend/app/reasoning.py`，用于处理 `<thinking>`、`<think>`、未闭合标签和部分 `response` 重复。但流式保护器主要检查输出前缀：如果标签前有 BOM、零宽字符、HTML entity、Markdown 围栏或其他未识别内容，保护器可能提前进入 passthrough。

截图中 `<thinking>` 看起来位于首字符。若线上确实是当前代码且第一个字节真的是普通 `<thinking>`，现有前缀保护器理论上应该拦截，所以还必须核查：

- 线上后端是否为当前 release；
- 前端是否加载了旧构建；
- 标签前是否存在不可见字符；
- 内容来自实时 SSE 还是旧数据库记录；
- provider 把内容放进了 `content` 还是 `reasoning_content`；
- 浏览器收到的是 `content_delta` 还是 `reasoning_delta`。

### 5.2 推荐的安全边界

1. 面向用户的回复关闭 provider thinking。
2. 不向浏览器发送原始 `reasoning_delta`。
3. 不把原始 reasoning 持久化到数据库或日志。
4. provider adapter 对应用层只暴露最终回答。
5. 如果需要解释，应单独生成受约束的“回答依据摘要”，不能展示模型 scratchpad。
6. 若必须流式输出，使用可以扫描任意位置和跨 chunk 标签的状态机；绝对 fail-closed 的方案是完整缓冲、规范化后再发送。

历史数据修复应先运行 `backend/scripts/repair_reasoning_channels.py` 的 dry-run，核对目标记录后再 `--apply`，最后重新 dry-run 并要求 `changed=0`。执行前必须备份数据库。

## 6. 知识库与 RAG

当前系统有实际生效的 RAG 链路，但属于轻量词法 RAG，不是向量语义 RAG。

```text
KnowledgeBase 文件
  -> scripts/import_project_knowledge.py
  -> Markdown/Excel 转换和分块
  -> MySQL knowledge_sources / knowledge_chunks
  -> DatabaseKnowledgeBase 进程内词法索引
  -> 按模块过滤、相关性评分、top-k
  -> # Retrieved Knowledge system segment
  -> 模型生成回答
```

### 6.1 导入和分块

- 源文件使用 SHA-256 内容哈希，未变化时幂等跳过。
- 同名内容发生变化时，在事务中替换旧 chunk。
- Markdown 按标题和段落切分。
- 单块最大约 1400 字符；超长段落重叠约 160 字符。
- Excel 成人活动 MET 数据会转换为文本条目。

当前源文件离线解析约 1331 个 chunk：BA 160、PA 759、MI 354、BCT 58。该数字不等于已确认的线上数据库数量，需通过管理员 API或只读 SQL 复核生产数据。

### 6.2 检索

- 服务启动时从 MySQL 加载到进程内索引。
- 缓存 TTL 为 300 秒。
- 中文生成二元、三元片段；英文提取词项，并有有限双语扩展。
- 得分近似为 `IDF × 饱和 TF × 标题加权 × 查询覆盖率`。
- 排序后优先保证知识类别多样性。
- 查询由当前输入、最多两条不同的历史用户消息和 PA card 组成，最多约 6000 字符。

当前模块映射：

- BA：模块 1–4；
- PA：模块 2；
- MI：模块 2–3；
- BCT：模块 3。

模块 top-k 分别为 2、4、4、2。危机节点不经过普通模块 RAG。

### 6.3 当前局限

- 没有 embedding 或 vector database；
- 没有 semantic search、hybrid retrieval 或 reranker；
- 没有强制来源引用；
- 没有系统化检索质量和忠实度评测；
- 启动 warm-up 失败时应用仍会启动，可能无声降级为没有知识上下文；
- 多 worker 时管理员导入只会立即失效当前进程缓存，其他进程最多等待 TTL。

后续可升级为 BM25/词法加向量的 hybrid retrieval，再增加 reranker、chunk 来源引用和离线评测集。

## 7. MemOS 与其他记忆层

MemOS/OpenMem 是每个用户的远程长期记忆，不是共享知识库。

- 接口前缀：`/api/openmem/v1`。
- 保存：`/add/message`。
- 检索：`/search/memory`。
- 鉴权：`Token`。
- 用户范围：用户档案 UUID。
- 默认最多检索 5 条。
- 主要在新会话首轮或首次进入模块 4 时召回。
- 模块转换后在后台生成摘要并保存，不能阻塞用户回复。

当前系统同时存在四种上下文：共享知识库、结构化档案/临床数据、MemOS 长期记忆和当前 session memory。它们各自查询，最终在 Prompt 组装阶段汇合。

## 8. 数据库与迁移约束

- `create_all()` 只会创建缺失的表，不会为已有生产表补字段。
- 项目尚未建立 Alembic 基线，当前仍依赖若干幂等迁移脚本。
- 七张外部临床业务表不得由应用自动 ALTER。
- 数据库测试必须显式 override `get_db` 到隔离 SQLite，不能只修改环境变量后假设不会连接生产 MySQL。
- 涉及 reasoning 历史清洗、身份迁移或临床编码修复时，必须先备份并使用 dry-run。

详见 `DATABASE.md` 和 `DATABASE_WORKFLOW_REMEDIATION.md`。

## 9. macOS 本地环境初始化

建议先安装 Homebrew、Git、Python 3.12、Node.js 22。

```bash
xcode-select --install

# 安装 Homebrew 后
brew install git python@3.12 node@22

git clone <private-github-repository-url>
cd <repository-directory>

cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt
cp .env.example .env
# 手动把旧电脑 backend/.env 中真正需要的秘密安全迁移到新电脑；不要提交到 Git
python -m uvicorn app.main:app --reload --port 8000
```

另开终端：

```bash
cd <repository-directory>/frontend
npm ci
cp .env.local.example .env.local
npm run dev
```

访问：

- 前端：`http://localhost:3000`
- 后端文档：`http://localhost:8000/docs`
- 健康检查：`http://localhost:8000/health`

不要在开发服务器运行时执行 `npm run build`，因为它们共享 `.next`。

### 9.1 macOS 上不能直接使用的内容

- `backend/run_dev.cmd`
- `infra/windows/`
- `infra/deploy/deploy.ps1`
- Windows SSH 私钥路径，例如 `C:\Users\...\.ssh\...`

当前生产发布入口是 PowerShell 脚本。迁移到 macOS 后，要么安装 PowerShell：

```bash
brew install --cask powershell
pwsh ./infra/deploy/deploy.ps1 -IdentityFile ~/.ssh/bacoach_deploy_ed25519
```

要么把该流程重写为 Bash。重写时必须保留测试、allow-list 打包、远端构建、原子切换软链接、健康检查和失败回滚。

## 10. 生产部署概览

```text
公网 80/443
  -> Nginx HTTPS
  -> Next.js 127.0.0.1:3000
  -> /api/* 转发至 FastAPI 127.0.0.1:8000
  -> 阿里云 RDS MySQL
```

- release：`/opt/bacoach/releases/<UTC timestamp>`；
- 当前版本：`/opt/bacoach/current` 软链接；
- 生产秘密：`/etc/bacoach/*.env`，不在 release 和 Git 中；
- 服务：`bacoach-backend.service`、`bacoach-frontend.service`；
- 证书当前记录的到期日：2026-11-23；
- 证书自动续期仍依赖旧 FRP 路径，需在到期前迁移到阿里云证书或 DNS-01。

## 11. 测试和验证

后端：

```bash
cd backend
source .venv/bin/activate
python -m pytest -q
```

前端：

```bash
cd frontend
npm ci
npm run typecheck
npm run build
```

最近一次针对 profile、chat、reasoning 和 knowledge base 的隔离测试共 70 项通过。当前 thinking 测试仍缺少 BOM、零宽字符、HTML entity、中途标签、历史脏数据和线上版本不一致场景。

## 12. 接手后的 P0/P1 工作

P0：

1. 修复假登录和统一 401 处理。
2. 禁止原始 reasoning 离开服务端，并清理历史泄漏内容。
3. 核对线上前后端 release、SSE 事件和数据库 schema。
4. 确认生产知识库实际 source/chunk 数量。

P1：

1. 建立 Alembic 基线和可回滚迁移流程。
2. 为 auth、SSE、provider、RAG 增加不记录敏感内容的可观测性。
3. 增加 reasoning 标签变体和部署版本差异测试。
4. 将 Windows 发布脚本迁移为跨平台流程或 CI/CD。
5. 在 2026-11-23 前迁移 TLS 自动续期方案。

## 13. 不进入 GitHub 的文件

- `backend/.env`、`frontend/.env.local`；
- `.venv/`、`node_modules/`、`.next/`；
- `*.db`、`*.sqlite*`；
- `*.log`、`runtime-logs/`；
- `.tools/`；
- `.claude/` 本机设置；
- SSH 私钥及任何密钥文件。

迁移秘密时应使用密码管理器、加密 U 盘或 GitHub Secrets，不能通过 commit、聊天消息或普通云盘明文传输。
