# 运维契约

本文档是单机、单进程 React + FastAPI 部署的可观测性边界。它不是多用户生产承诺，也不把“指标存在”当成“系统一定可用”。

## SLO 与保留期

| 项目 | 目标 | 说明 |
| --- | --- | --- |
| API 可用性 | 每周 99% | `/api/live` 只代表进程存活；`/api/ready` 同时检查模型可取得性、向量库和 manifest 一致性。 |
| 备份新鲜度 | 最近一次验证成功不超过 7 天 | 创建备份后会验证，显式 `backup.py verify` 也会写验证台账。 |
| 请求错误率 | 最近窗口至少 10 个请求时，错误率 < 5% | SSE 的 `interrupted` 单独记录，不假装能区分用户主动停止与网络断开。 |
| access.log | 7 天 | 按文件大小滚动，并在应用启动时按时间删除旧文件。 |
| audit.log | 90 天 | 记录上传、删除、清空、备份和鉴权失败，不记录查询正文或密钥。 |
| error.log | 30 天 | 只用于异常摘要，不作为请求错误率的分母；旧文件在启动时清理。 |

## 端点契约

- `GET /api/live`：不鉴权，进程存活时返回 `200 {"ok": true}`。
- `GET /api/ready`：不鉴权；离线模式下 embedding/reranker 不可用、向量库依赖失败或 manifest 与向量库不一致时返回 `503`。在线模式把“可从配置的 Hub 下载”视为可用，不主动下载模型。空知识库是合法状态，更新窗口返回 `200` 且状态为 `updating`。
- `GET /metrics`：优先使用独立 `METRICS_TOKEN`。未配置 token 时只允许 loopback，不能把 `APP_PASSWORD` 的本地 fail-open 规则复制到公网。
- 每个 HTTP 响应带服务端生成的 `X-Request-Id`。客户端传入的同名 header 不会被信任。

## 日志与隐私

access 日志只包含模板路由、方法、状态、耗时、SSE 业务终态、请求 ID 和非重叠阶段耗时；阶段字段统一使用 `retrieval_ms`、`rerank_ms`、`llm_ms`、`total_ms`。请求上下文内的 `app.log`/`error.log` 也带同一 `request_id`，启动自检等脱离请求的日志没有该字段。查询原文、session ID、API Key、上传原始文件名和 query string 不进入 access/audit 日志。

SSE 的 HTTP 状态通常是 `200`，因此错误率必须使用业务终态：`ok`、`error`、`interrupted`。中断原因当前无法可靠区分用户点击停止与网络断开，所以 `interrupted` 不参与“模型故障”告警。

LLM 指标按 SDK 实际调用尝试计数；tenacity 重试会产生多个 attempt，这是故意保留的成本/稳定性信号，不把它伪装成一次逻辑请求。检索、重排和 LLM 生成的阶段耗时写入 access 事件。

## 备份边界

Chroma/HNSW 没有本项目可依赖的在线热快照契约。创建或恢复前停止 FastAPI 以及其他写入者，并传入 `--confirm-stopped`。备份目录、`kb_data/`、`kb_manifest.sqlite3` 和回答缓存必须作为同一运行数据单元处理；恢复前先校验哈希和 SQLite `quick_check`。

外部健康检查读取 `verification_ledger.jsonl`，只承认最近一次实际验证成功的备份，不把目录修改时间当成备份新鲜度。

## 部署边界

默认监听 `127.0.0.1`、单进程单 worker。局域网或公网暴露必须同时配置 `APP_PASSWORD` 和 HTTPS。本项目没有用户、角色、租户或文档级权限隔离；不要把单口令 API 当成多租户身份系统。

反向代理不得把 `/api/live`、`/api/ready` 直接暴露给公网；它们应仅供本机或受控监控网段访问。若代理 `/metrics`，必须配置 `METRICS_TOKEN`，因为反向代理的 loopback 来源会绕过“仅本机”判断。

## 外部检查

由 Windows 任务计划每 5 分钟运行。任务不会由应用自动注册；需要显式安装或卸载。

该检查只是“应用进程外、同一台主机内”的监督：当前任务以交互用户身份运行，用户未登录、Windows/任务计划停止、主机断电或网络整体中断时无法自行发送告警。需要覆盖整机可用性时，应另配远端探测；不要把本脚本误当成主机外监控。

安装：`./scripts/register_health_check_task.ps1 -Action Install -IntervalMinutes 5`
卸载：`./scripts/register_health_check_task.ps1 -Action Uninstall`

手动试跑：`uv run python scripts/health_check.py`

脚本从进程外检查 `/api/ready`、验证成功备份的新鲜度、文件系统剩余空间、access 日志错误率和持续模型鉴权/配额失败。告警状态写入 `alerts_state.json`，事件写入 `alerts.log`，同一故障冷却 30 分钟；恢复事件会单独记录。Webhook 是尽力而为的通知渠道，不能替代本地台账。
