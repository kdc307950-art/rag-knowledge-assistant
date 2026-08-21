# 安全手册

> 文档版本：`0.1`
> 适用应用版本：`0.1.0`
> 最近核对：`2026-08-21`

## 安全边界

当前系统提供单机用户鉴权、角色、文档 ACL、HTTPS、日志脱敏和生产启动门控。它不提供多租户隔离、多实例一致性、企业身份提供商集成、自动密钥轮换或完整用户生命周期管理。

## 生产鉴权

生产强制：

```env
RAG_ENVIRONMENT=production
DEPLOYMENT_MODE=multi_user
AUTH_MODE=users
AUTH_COOKIE_SECURE=1
PUBLIC_BASE_URL=https://kb.example.com
AUTH_SECRET=<至少32字节随机值>
ALERT_WEBHOOK_URL=<受控Webhook>
```

浏览器使用 HttpOnly、Secure、SameSite=Lax Cookie。受控脚本可以使用 Bearer；`APP_PASSWORD` 在 multi-user 中仅是可选高权限兼容通道，不是普通用户登录凭据，也不是 token 签名密钥。

登录失败按“来源地址 + 用户名”在进程内限制：60 秒内 5 次失败后返回 429。进程重启会清空该计数，因此它不是外围防暴力破解的替代品。

## 角色和文档 ACL

| 角色 | 主要权限 |
| --- | --- |
| `viewer` | 查询自己可见的文档和问答 |
| `editor` | viewer 权限；创建本部门 `private/department` 文档并管理获授权文档 |
| `admin` | 全局诊断、文档管理、清空知识库和反馈评审 |

visibility：

- `all`：所有已登录用户；
- `department`：同部门和 admin；
- `private`：服务端记录的上传者和 admin。

`owner_id` 由服务端从当前身份生成，客户端不能指定。遗留文档缺少完整 ACL 时，非 admin 访问应 fail closed。完整规则见 [ACL 契约](metadata-acl-contract.md)。

## 密钥管理

以下值必须放在部署密钥存储或权限严格的 `.env` 中：模型 API Key、`AUTH_SECRET`、`APP_PASSWORD`、`METRICS_TOKEN`、`ALERT_WEBHOOK_URL`、`GRAFANA_ADMIN_PASSWORD`、`QUALITY_ENCRYPTION_KEY` 和 TLS 私钥。

最低要求：

- `.env` 权限限制为部署账号可读；
- 不提交 Git，不出现在工单、截图、命令历史或日志；
- 不同用途使用独立秘密，不复用；
- 轮换前保留可回退的受控配置；
- 轮换后重启相关服务并验证登录、指标、通知或数据解密；
- 怀疑泄露时立即撤销供应商 Key、轮换本地秘密并检查审计日志。

当前应用没有在线密钥轮换机制，轮换需要维护窗口。轮换 `AUTH_SECRET` 会使现有 token 失效；轮换 `QUALITY_ENCRYPTION_KEY` 前必须制定旧数据解密/迁移方案，否则历史质量数据不可读。

## TLS 与网络

- Nginx 只允许 TLS 1.2/1.3，并设置 HSTS 和常用安全头；
- 80 只服务 ACME challenge，其余跳转 443；
- backend 不映射宿主机端口，只在 Compose 网络 expose 8000；
- `/api/live`、`/api/ready` 从公网 Nginx 返回 404；
- `/metrics` 从公网入口拒绝，Prometheus 使用容器网络和独立 token；
- Grafana 只绑定 `127.0.0.1:3000`，通过 SSH 隧道访问。

证书续期后必须复制新的 `fullchain.pem`/`privkey.pem` 并执行 `docker compose exec nginx nginx -s reload`，随后验证证书有效期和 HTTPS。

## 数据和日志

- `RAG_DATA_DIR`、备份和模型缓存按内部数据保护级别控制；
- `quality.sqlite3` 只对问题和回答字段做 Fernet 应用层加密，不是整库加密；
- SQLite 元数据、用户 ID、来源标签和时间戳可能为明文；
- 备份包含用户、token 吊销、知识库和可选质量数据，应加密存储并限制访问；
- 上传暂存可能短时包含原文，异常退出后按 TTL 清理；
- 日志对已配置秘密做脱敏，但不能把脱敏视为允许公开日志。

## 文档内容风险

知识库文档是业务输入，不应被视为可信程序指令。系统的严格提示词和 ACL 不能证明所有提示注入、错误引用或冲突条款都被消除。涉及劳动、法律、财务或高风险决策的回答必须由业务责任人复核。

文档权威性由 `config/document_governance.json` 明确声明，不从文件名、时间或内容相似度推断。`reference` 只表示允许检索，不表示冲突时存在自动优先级。

## 已知安全缺口

- 没有 SSO/MFA；
- 没有用户停用、改密和角色变更的正式 CLI/API；
- 登录限流是进程内的；
- 没有多租户隔离；
- 没有自动依赖漏洞扫描、SBOM 或制品签名的现成流程；
- 没有主机外可用性探测；
- 没有在线秘密轮换。

这些限制必须进入风险接受或后续治理记录，不能在说明书中描述为已经解决。

## 安全事件最小处置

1. 隔离入口或停止 backend，阻止继续访问/写入。
2. 保留请求 ID、审计日志、错误日志、版本和时间线。
3. 撤销疑似泄露的外部 Key，轮换对应本地秘密。
4. 检查用户库、文档 ACL、治理配置和备份完整性。
5. 按 [升级与回滚手册](upgrade-rollback.md) 恢复可信版本或数据。
6. 验证鉴权、ACL、日志、告警和主业务流程后再恢复入口。
