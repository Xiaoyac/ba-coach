# BA Coach 正式部署与更新

生产地址：<https://bacoach.xyz/>  
广州服务器：`8.134.178.40`  
部署根目录：`/opt/bacoach`

## 当前架构

```text
公网 80/443
    ↓
Nginx（HTTPS、SSE 不缓冲）
    ↓ 127.0.0.1:3000
Next.js 15
    ↓ /api/* → 127.0.0.1:8000
FastAPI
    ↓
阿里云 RDS MySQL
```

前后端都在广州 ECS 上运行，开发电脑关机后网站仍保持在线。本机的三个
`BA Coach` 计划任务已经禁用。

## 日常更新：只需要这一条命令

在 `D:\心理学项目` 打开 PowerShell：

```powershell
.\infra\deploy\deploy.ps1
```

脚本会依次：

1. 执行 `npm run typecheck`；
2. 执行后端完整 `pytest`；
3. 只打包生产源码，明确排除 `.env`、`.venv`、`node_modules`、`.next` 和日志；
4. 经 SSH 上传到服务器；
5. 在独立版本目录安装依赖并构建 Next.js；
6. 本机健康检查通过后切换 `/opt/bacoach/current`；
7. 重启 systemd 服务并检查正式 HTTPS。

第一次部署已把生产密钥放在服务器 `/etc/bacoach/backend.env`。日常发布不会
重新上传 `.env`，也不会覆盖数据库或用户对话。

`KnowledgeBase/` 是部署包允许上传的非秘密配置。每次发布会运行
`scripts/import_project_knowledge.py`，按内容哈希幂等更新
`knowledge_sources` / `knowledge_chunks`；未变化的文献不会重复插入。该过程只写
应用自有的知识库表，不修改七张外部临床业务表。

只有在 API Key、数据库地址或生产配置发生变化时，才应单独修改服务器环境
文件，然后重启后端；不要把这些秘密写进部署包或 Git。

## 查看状态与日志

```powershell
ssh -i C:\Users\20640\.ssh\bacoach_deploy_ed25519 root@8.134.178.40
```

进入服务器后：

```bash
systemctl status bacoach-backend bacoach-frontend nginx
journalctl -u bacoach-backend -f
journalctl -u bacoach-frontend -f
curl http://127.0.0.1:8000/health
curl -I http://127.0.0.1:3000/
```

正常结果：后端健康接口为 200、前端为 200；未登录访问
`/api/conversations` 应返回 401。

## 回滚

每次发布都是一个 UTC 时间戳目录：

```bash
ls -1 /opt/bacoach/releases
readlink -f /opt/bacoach/current
```

若新版本出现问题，把 `current` 指回上一个版本并重启：

```bash
ln -sfn /opt/bacoach/releases/<上一个版本号> /opt/bacoach/current
systemctl restart bacoach-backend bacoach-frontend
```

部署脚本在新版本健康检查失败时也会自动执行同样的回滚。数据库 schema 变化
不在代码回滚范围内，因此增加 Alembic 后，涉及数据库迁移的版本必须另外准备
向下迁移或备份恢复方案。

## 服务器文件与权限

- `/opt/bacoach/releases/`：版本化代码和 Linux 依赖；
- `/opt/bacoach/current`：当前版本软链接；
- `/etc/bacoach/backend.env`：数据库与模型密钥，`0640 root:bacoach`；
- `/etc/bacoach/frontend.env`：Next.js 服务端环境，`0640 root:bacoach`；
- `/etc/systemd/system/bacoach-*.service`：开机自启与异常重启；
- `/etc/nginx/sites-available/bacoach.xyz`：HTTPS 反向代理。

FastAPI 和 Next.js 都只监听 `127.0.0.1`。安全组不应开放 3000 或 8000。

## 当前需要跟进的生产事项

1. DeepSeek 与豆包均已上线；豆包保留深度思考并使用 `low` 推理强度。模块路由与
   风险检测固定使用 DeepSeek，不跟随账号的正文模型偏好。
2. TLS 证书到期时间为 **2026-11-23**。服务器直连 Let's Encrypt 受限，当前续期
   仍保留旧 FRP 代理。应在到期前迁移到阿里云证书或 DNS-01 自动续期，然后停用
   `frps.service` 并关闭安全组端口 7000。
3. 项目尚未接入 Alembic；当前应用自有字段通过部署前幂等脚本迁移。更复杂的改表
   仍应先建立 Alembic 基线，七张外部业务表不得由部署脚本修改。
4. 真实上线前还应再次核对危机干预资源和风险字段数字编码。
