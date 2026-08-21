# 生产部署手册

> 文档版本：`0.1`
> 适用应用版本：`0.1.0`
> 支持拓扑：原生 Linux + Docker Compose v2 + Nginx HTTPS
> 最近核对：`2026-08-21`

本文描述首次生产部署。升级已有实例使用 [升级与回滚手册](upgrade-rollback.md)。

## 部署红线

- 只运行一个 backend 容器、一个 Uvicorn worker、一个写实例。
- 不允许把 `DEPLOYMENT_MODE=dev` 暴露到局域网或公网。
- 公网入口只允许 HTTPS 443；80 仅用于 ACME challenge 和跳转。
- `/api/live`、`/api/ready`、`/metrics` 不经公网入口暴露。
- 生产数据只存放在 Compose 持久卷，不写入镜像层。
- 未通过准备检查、恢复演练或发布后验收时不得宣称生产就绪。

## 前提

- 原生 Linux 主机；
- Docker Engine `>=24` 和 Docker Compose v2；
- 80/443 可被目标用户访问；
- 正式域名和受信任 CA 证书；
- 可访问模型供应商和模型文件来源，或已准备离线模型缓存；
- 企业微信机器人 webhook；
- 具备 Docker 和证书目录权限的部署账号；
- 已记录本次 Git 提交和应用版本。

CPU、内存、磁盘和并发推荐值尚无受控容量测试证据，必须在目标环境按 [验收手册](acceptance-and-known-limits.md) 实测后确定。

## 1. 获取并确认版本

```bash
git status --short
git rev-parse HEAD
grep '^version' pyproject.toml
```

工作区必须符合组织的发布策略。把提交号、应用版本、部署人员和时间写入交付记录。

## 2. 配置环境

```bash
cp .env.example .env
chmod 600 .env
```

生产至少设置：

```env
RAG_ENVIRONMENT=production
PUBLIC_BASE_URL=https://kb.example.com
DEPLOYMENT_MODE=multi_user
AUTH_MODE=users
AUTH_SECRET=<至少32字节、非重复模式的随机字符串>
AUTH_COOKIE_SECURE=1

DEEPSEEK_API_KEY=<按实际供应商设置对应专用Key>
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-v4-flash

METRICS_TOKEN=<独立随机令牌>
ALERT_WEBHOOK_URL=<企业微信机器人Webhook>
GRAFANA_ADMIN_PASSWORD=<非默认强口令>
```

不要把真实值写进部署记录、终端截图或 Git。完整变量见 [配置参考](configuration-reference.md)。

## 3. 准备 HTTPS

证书必须位于：

```text
certs/fullchain.pem
certs/privkey.pem
```

Let's Encrypt HTTP-01 示例：

```bash
mkdir -p certbot/www certs
sudo certbot certonly --webroot -w "$PWD/certbot/www" -d kb.example.com
sudo install -m 0644 /etc/letsencrypt/live/kb.example.com/fullchain.pem certs/fullchain.pem
sudo install -m 0600 /etc/letsencrypt/live/kb.example.com/privkey.pem certs/privkey.pem
```

本地自签证书只用于验收，不得作为公网生产证书。证书缺失时 Nginx 应启动失败，这是预期的安全行为。

## 4. 配置预检

```bash
docker compose config --quiet
docker compose --profile monitoring config --quiet
docker compose build
```

检查最终 Compose 结果时不要把包含秘密的展开配置保存到公共日志。镜像构建必须成功，并确认 backend 命令仍包含 `--workers 1`。

## 5. 创建首个管理员

首次启动前，在 `rag_data` 卷中创建管理员：

```bash
docker compose run --rm --no-deps backend \
  python scripts/create_user.py admin --department general --role admin
```

命令交互读取并确认密码，不会打印密码。重复执行同名用户会失败，不能用它覆盖现有管理员。

## 6. 启动主服务

```bash
docker compose up -d backend nginx
docker compose ps
docker compose logs --tail=100 backend nginx
```

backend 必须 healthy，Nginx 必须 running。失败时停止部署，按日志中的具体安全门控或模型状态修复，不得删除 healthcheck。

## 7. 启动监控

```bash
docker compose --profile monitoring up -d
docker compose --profile monitoring ps
```

`METRICS_TOKEN`、`ALERT_WEBHOOK_URL`、`GRAFANA_ADMIN_PASSWORD` 任一缺失时，monitoring profile 应硬失败。Grafana 只绑定 `127.0.0.1:3000`，通过 SSH 隧道访问：

```bash
ssh -N -L 3000:127.0.0.1:3000 <user>@<host>
```

## 8. 发布后验收

```bash
curl -fsS https://kb.example.com/
curl -I http://kb.example.com/
curl -i https://kb.example.com/api/live
curl -i https://kb.example.com/api/ready
curl -i https://kb.example.com/metrics
```

预期：主页可访问；HTTP 跳转 HTTPS；三个内部端点从公网入口均不可用。再从 backend 容器内部验证 readiness：

```bash
docker compose exec backend python -c \
  "import json,urllib.request;print(json.load(urllib.request.urlopen('http://localhost:8000/api/ready')))"
```

必须实际登录，上传一份允许的测试文档，等待任务完成，完成一次知识库问答并核对来源，然后删除测试文档。不要用包含真实敏感内容的样本。

验证告警链路：

```bash
docker compose run --rm --no-deps \
  -v "$PWD/scripts/check_alert_webhook.py:/tmp/check.py:ro" \
  wechat-webhook python /tmp/check.py http://wechat-webhook:5001/webhook
```

只有企业微信同时收到 firing 和 recovery 消息才算链路通过。

## 9. 首次备份与恢复演练

先停止 backend：

```bash
docker compose stop backend
docker compose run --rm --no-deps backend \
  python scripts/backup.py create --confirm-stopped
docker compose start backend
```

随后在隔离数据目录完成恢复和健康检查。具体命令见 [运维手册](operations-runbook.md)。没有恢复演练证据的备份不构成恢复能力证明。

## 部署完成条件

只有 [验收清单](acceptance-and-known-limits.md) 的阻断项全部通过、未执行项明确记录、回滚版本可用，部署才完成。
