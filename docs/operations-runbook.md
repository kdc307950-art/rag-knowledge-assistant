# 运维手册

> 文档版本：`0.1`
> 适用应用版本：`0.1.0`
> 最近核对：`2026-08-21`

## 日常检查

部署记录必须给以下职责指定实际姓名或团队和联系方式；仓库不能替组织决定责任人：

| 职责 | 责任范围 |
| --- | --- |
| 服务负责人 | 可用性、变更批准、风险接受和事故升级 |
| 发布负责人 | 版本、备份、发布、回滚和交付证据 |
| 运维值班 | 探针、监控、告警、容量和初步处置 |
| 数据负责人 | 业务文档、治理证据、保留和删除批准 |
| 安全负责人 | 用户权限、秘密、证书和安全事件 |

告警升级路径和响应时限必须写入部署记录；未指定时，告警链路只能视为技术接通，不能视为组织响应闭环。

每次值守检查至少确认：

```bash
docker compose ps
docker compose logs --tail=100 backend nginx
docker compose --profile monitoring ps
```

检查 Grafana 的服务可用性、向量库健康、错误率、模型延迟、拒答率和鉴权失败。面板正常不等于通知正常，按变更窗口或组织要求定期发送测试告警。

## 探针解释

| 端点 | 成功 | 失败含义 |
| --- | --- | --- |
| `/api/live` | 进程可响应 | 进程或 HTTP 服务不可用 |
| `/api/ready` | 鉴权、模型本地可用性、向量库和 manifest 满足当前契约 | 不应接收业务流量 |
| `/api/diagnostics` | admin 可读取聚合诊断 | 403 表示非 admin；它不执行 LLM 网络调用 |
| `/metrics` | token 正确或请求来自 loopback | 401/403 表示指标访问策略拒绝 |

`ready` 在线模式只确认模型可从配置来源取得，不主动下载；LLM 检查只确认本地配置，不代表供应商网络已验证。

## 日志

| 文件 | 用途 | 默认保留 |
| --- | --- | --- |
| `app.log` | 应用事件 | 30 天 |
| `error.log` | ERROR/CRITICAL | 30 天 |
| `access.log` | 路由、状态、耗时、SSE 业务终态 | 7 天 |
| `audit.log` | 鉴权、上传、删除、清空、备份 | 90 天 |
| `alerts.log` | 外部检查的告警和恢复 | 由运行目录管理 |
| `incident_history.jsonl` | 人工确认的事故复盘 | 人工治理 |

排障时优先使用 `X-Request-Id` 关联 access、app 和 error。日志不应包含查询正文、文档正文、API Key、Bearer Token 或上传原始文件名；仍应按敏感运行数据控制访问。

## 备份

Chroma/HNSW 不支持本项目认可的在线热快照。创建备份前停止所有写入者：

```bash
docker compose stop backend
docker compose run --rm --no-deps backend \
  python scripts/backup.py create --confirm-stopped
docker compose start backend
```

备份包含 `RAG_DATA_DIR` 中的知识库、manifest、缓存、用户和可选质量数据；不包含上传暂存。脚本先写 `.incomplete-*`，完成哈希和 SQLite `quick_check` 后才发布正式目录，默认保留最近 7 份。

再次验证指定备份：

```bash
docker compose run --rm --no-deps backend \
  python scripts/backup.py verify <backup-path>
```

只有 `verification_ledger.jsonl` 中最近一次实际验证成功的记录才用于备份新鲜度判断。

## 隔离恢复演练

恢复演练不能覆盖 live 数据。以宿主机开发环境为例：

```powershell
uv run python scripts/backup.py restore backups\kb_YYYYMMDD_HHMMSS `
  --data-dir .verify_tmp\restore-drill-data `
  --confirm-stopped --confirm-replace
$env:RAG_DATA_DIR = "$PWD\.verify_tmp\restore-drill-data"
uv run python -c "from enterprise_rag.storage.vector_store import get_vector_store_health; print(get_vector_store_health())"
Remove-Item Env:RAG_DATA_DIR
```

还应抽查用户库、文档数、manifest、已知检索和质量库（若启用）。记录备份 ID、演练目标目录、耗时和结果。

## 生产恢复

1. 停止 backend 和所有写入脚本。
2. 记录当前数据目录状态、故障时间和已选备份。
3. 执行 `backup.py verify`。
4. 执行 restore：

```bash
docker compose run --rm --no-deps backend \
  python scripts/backup.py restore <backup-path> \
  --confirm-stopped --confirm-replace
```

5. 启动 backend，验证 readiness、用户登录、文档数和抽样检索。
6. 启动 Nginx 并完成外部验收。
7. 保留脚本生成的 `data.pre-restore-*`，直到恢复验收和事故复盘完成。

## 用户和权限操作

新增用户：

```bash
docker compose run --rm --no-deps backend \
  python scripts/create_user.py alice --department hr --role viewer
```

当前仓库只提供创建用户 CLI，没有受控的改密、停用、角色变更或删除 CLI。不要直接修改 SQLite 数据库。需要这些操作时应通过经评审的离线管理过程执行；在对应能力真正实现前，将其记录为运维限制。

## 质量数据

启用质量反馈后，按计划运行：

```bash
docker compose exec backend python scripts/purge_quality_data.py
```

`QUALITY_ENCRYPTION_KEY` 丢失后历史问题和回答不可恢复。数据库和密钥必须分别受控，但恢复时两者缺一不可。

## 文档治理变更

先预览：

```bash
docker compose exec backend python scripts/sync_document_governance.py
```

证据和预览复核后才写入：

```bash
docker compose exec backend python scripts/sync_document_governance.py --apply
```

治理变更后重新运行检索黄金集并记录配置哈希。普通聊天接口不能绕过治理过滤。

## 事故响应

1. 发现：记录时间、告警、请求 ID、版本和影响。
2. 控制：数据一致性或权限异常时停止 Nginx/backend，避免扩大影响。
3. 诊断：保留日志、manifest、备份台账和容器状态，不泄露秘密。
4. 恢复：优先按已演练的回滚或恢复步骤操作。
5. 验证：执行完整主流程和监控通知检查。
6. 复盘：将已确认的根因、影响、处置和预防措施写入受控事故记录。

外部健康检查运行在同一主机，不能发现整机断电、全网中断或任务计划自身停止。生产可用性需要另配远端探测。
