# 运维契约

本文档是单机、单进程 React + FastAPI 部署的可观测性边界。它支持本地用户和文档 ACL，但不是多租户或多实例生产承诺，也不把“指标存在”当成“系统一定可用”。

## SLO 与保留期

| 项目 | 目标 | 说明 |
| --- | --- | --- |
| API 可用性 | 每周 99% | `/api/live` 只代表进程存活；`/api/ready` 同时检查模型可取得性、向量库和 manifest 一致性。 |
| 备份新鲜度 | 最近一次验证成功不超过 7 天 | 创建备份后会验证，显式 `backup.py verify` 也会写验证台账。 |
| 请求错误率 | 最近窗口至少 10 个请求时，错误率 < 5% | SSE 的 `interrupted` 单独记录，不假装能区分用户主动停止与网络断开。 |
| 慢请求 | 最近 15 分钟内达到阈值的已完成请求少于 3 条 | 普通请求使用 `total_ms`；SSE 只看 `llm_ms`，不把客户端持有连接时间当成服务端延迟。 |
| access.log | 7 天 | 按文件大小滚动，并在应用启动时按时间删除旧文件。 |
| audit.log | 90 天 | 记录上传、删除、清空、备份和鉴权失败，不记录查询正文或密钥。 |
| error.log | 30 天 | 只用于异常摘要，不作为请求错误率的分母；旧文件在启动时清理。 |
| 质量反馈 | 默认关闭；启用后最多 30 天 | `runs` 与 `feedback` 共用一个 SQLite 文件，问题和回答字段使用部署密钥进行应用层加密。 |

## 端点契约

- `GET /api/live`：不鉴权，进程存活时返回 `200 {"ok": true}`。
- `GET /api/ready`：不鉴权；除 embedding/reranker、向量库和 manifest 外，检查部署鉴权契约。`dev` 返回 `200` 和 `auth.warning=authentication_disabled`；`single_user` 缺 `APP_PASSWORD`，或 `multi_user` 缺 `AUTH_SECRET`、用户库不可读写、角色数据异常、没有 active admin 时返回 `503`。在线模式把“可从配置的 Hub 下载”视为可用，不主动下载模型。空知识库是合法状态，更新窗口返回 `200` 且状态为 `updating`。
- `GET /metrics`：优先使用独立 `METRICS_TOKEN`。Prometheus 用 `Authorization: Bearer <token>` 发送，手动 curl 用 `X-Metrics-Token: <token>`。未配置 token 时只允许 loopback，不能把 `APP_PASSWORD` 的本地 fail-open 规则复制到公网。
- 每个 HTTP 响应带服务端生成的 `X-Request-Id`。客户端传入的同名 header 不会被信任。

## 日志与隐私

access 日志只包含模板路由、方法、状态、耗时、SSE 业务终态、请求 ID 和非重叠阶段耗时；阶段字段统一使用 `retrieval_ms`、`rerank_ms`、`llm_ms`、`total_ms`。请求上下文内的 `app.log`/`error.log` 也带同一 `request_id`，启动自检等脱离请求的日志没有该字段。查询原文、session ID、API Key、上传原始文件名和 query string 不进入 access/audit 日志。

SSE 的 HTTP 状态通常是 `200`，因此错误率必须使用业务终态：`ok`、`error`、`interrupted`。中断原因当前无法可靠区分用户点击停止与网络断开，所以 `interrupted` 不参与“模型故障”告警。

质量反馈只适用于已完成的普通知识库回答。浏览器为每次 `/api/chat` 发送 `X-Message-Id`；服务端先同步写入 `runs` 的终态，再在 `done` 事件中声明 `feedback_eligible=true`。点赞和点踩不可由普通用户删除或改写；相同 verdict 幂等，反向 verdict 冲突，管理员可以审阅但不改写原始反馈。该功能是单租户审计闭环，不构成多租户隔离。

`quality.sqlite3` 使用 SQLite 的 WAL、`busy_timeout` 和外键约束；问题和回答列由 `QUALITY_ENCRYPTION_KEY` 做应用层 Fernet 加密。SQLite 的 schema、时间戳、用户 ID、来源标签和反馈分类仍是明文元数据，因此这不是 SQLCipher 意义上的“整库加密”，数据库文件与密钥必须分别纳入受控存储和备份策略。不要将该 key 写入日志、评估样本或源码。

LLM 指标按 SDK 实际调用尝试计数；tenacity 重试会产生多个 attempt，这是故意保留的稳定性信号，不把它伪装成一次逻辑请求。`rag_llm_tokens_total` 只记录供应商实际返回的 input/output token，缺失或中断时保持未知，不用字符数估算。`rag_llm_cost_estimated_total` 只按 `LLM_PRICE_TABLE_PATH` 的价格表计算，精确匹配标记 `exact`，均价降级标记 `estimated`，无价格时不生成成本；任何成本都不是供应商账单。流式 usage 默认仅对 DashScope 兼容地址开启；自定义 OpenAI-compatible 地址需将 `LLM_STREAM_USAGE_MODE=on` 显式打开。检索指标按最终去重且过阈值的结果记录 `hit/empty/error/busy`，拒答只在严格知识库拒答分支计数；检索、重排和 LLM 生成的阶段耗时写入 access 事件。

## 备份边界

Chroma/HNSW 没有本项目可依赖的在线热快照契约。创建或恢复前停止 FastAPI 以及其他写入者，并传入 `--confirm-stopped`。`RAG_DATA_DIR` 是唯一的运行数据备份单元：其中的 `kb_data/`、`kb_manifest.sqlite3`、回答缓存、`auth.sqlite3` 和启用后的 `quality.sqlite3` 必须一起处理；恢复前先校验哈希和 SQLite `quick_check`。`AUTH_DB_PATH` 与 `QUALITY_DB_PATH` 被限制在该目录内，不能用外部路径绕过备份。

外部健康检查读取 `verification_ledger.jsonl`，只承认最近一次实际验证成功的备份，不把目录修改时间当成备份新鲜度。

## 部署边界

默认监听 `127.0.0.1`、单进程单 worker。`DEPLOYMENT_MODE=dev` 仅允许本地开发；局域网或公网暴露必须使用 `single_user` 或 `multi_user` 并启用 HTTPS。`single_user` 使用 `APP_PASSWORD`；`multi_user` 使用 Bearer Token、本地 SQLite 用户和检索前 ACL，且必须有独立 `AUTH_SECRET` 与至少一个 active admin。`APP_PASSWORD` 在 multi-user 中仅作为可选高权限服务账户，不是普通用户凭据，也不能作为 token 密钥。本项目没有租户隔离或多实例写入支持。

浏览器登录使用 HttpOnly `rag_access` Cookie，生产 HTTPS 必须设置 `AUTH_COOKIE_SECURE=1`；Bearer 只作为脚本、测试和受控客户端兼容协议。认证 Cookie、用户库和治理配置必须纳入备份与恢复演练。

若启用质量反馈，生产部署还必须配置独立的 Fernet key、每日执行保留期清理，并把 `quality.sqlite3` 纳入同一受控备份域。密钥丢失会使历史问题和回答不可解密，不能通过重置应用恢复。

反向代理不得把 `/api/live`、`/api/ready` 直接暴露给公网；它们应仅供本机或受控监控网段访问。compose 自带的 nginx 对这两个精确路径固定返回 `404`，backend 容器健康检查直接访问自身的 `/api/ready`。若代理 `/metrics`，必须配置 `METRICS_TOKEN`，因为反向代理的 loopback 来源会绕过“仅本机”判断。compose 自带的 nginx 直接 `deny all` 拒绝 `/metrics`，Prometheus 走容器网络抓取，不经过公网入口。

Compose 部署由 nginx 终止 TLS：443 是唯一的应用入口，80 仅保留 `/.well-known/acme-challenge/` 用于 ACME 续期，其余一律 `301` 到 HTTPS。宿主机 `./certbot/www/` 以只读方式挂载到 nginx 的 `/var/www/certbot`，Certbot `--webroot` 使用同一目录写入 challenge。只启用 TLS 1.2/1.3，附带 HSTS 及三个常规安全响应头。证书从宿主机 `./certs/` 只读挂载，必须同时存在 `fullchain.pem` 与 `privkey.pem`；缺失时 nginx 直接启动失败，不会降级成明文 HTTP 服务——这是刻意的，静默的 HTTP 回退比起不来更危险。本地验收可用 `scripts/gen_dev_certs.sh` 生成自签证书，生产必须替换为受信任 CA 签发的证书。

## 监控栈边界

监控栈是 `--profile monitoring` 的可选组件，默认不启动，也不影响主服务。

`monitoring-preflight` 是整个 profile 的前置闸门：`METRICS_TOKEN`、`ALERT_WEBHOOK_URL`、`GRAFANA_ADMIN_PASSWORD` 任一为空，或 Grafana 口令使用 `changeme`/`admin`/`password`，则该容器退出 1，其余四个服务因 `service_completed_successfully` 全部停在 `Created` 不启动。Prometheus 和企业微信适配器各自还有一道容器内校验，缺 token 或缺 webhook 时同样拒绝启动；Alertmanager 还会等待适配器健康检查通过。企业微信即使返回 HTTP 200，只要响应不是合法 JSON 或 `errcode != 0`，适配器仍返回 `502` 让 Alertmanager 重试。所有闸门都是宁可起不来或明确失败：一个抓不到数据或丢弃告警的监控栈会让人误以为有覆盖。

端口暴露边界：只有 Grafana 绑定宿主机端口，且限定 `127.0.0.1:3000`，运维通过 SSH 隧道访问（`ssh -N -L 3000:127.0.0.1:3000 <user>@<host>`）。Prometheus、Alertmanager 和 webhook 适配器只在容器网络内 `expose`，没有宿主机端口，不可从公网直达。Grafana 管理员口令无默认值，匿名访问关闭。

企业微信通道与外部检查的 webhook 一样是尽力而为的通知渠道：适配器只把 Alertmanager 的 payload 转成群机器人 markdown，自身不重试也不落库，失败时返回 `502` 交由 Alertmanager 按 `group_interval`/`repeat_interval` 重发。群机器人长时间不可达期间的通知会直接丢失，本地台账仍是唯一可追溯记录。

## 外部检查

由 Windows 任务计划每 5 分钟运行。任务不会由应用自动注册；需要显式安装或卸载。

该检查只是“应用进程外、同一台主机内”的监督：当前任务以交互用户身份运行，用户未登录、Windows/任务计划停止、主机断电或网络整体中断时无法自行发送告警。需要覆盖整机可用性时，应另配远端探测；不要把本脚本误当成主机外监控。

安装：`./scripts/register_health_check_task.ps1 -Action Install -IntervalMinutes 5`
卸载：`./scripts/register_health_check_task.ps1 -Action Uninstall`

手动试跑：`uv run python scripts/health_check.py`

脚本从进程外检查 `/api/ready`、验证成功备份的新鲜度、文件系统剩余空间、access 日志错误率、慢请求和持续模型鉴权/配额失败。慢请求检查默认读取最近 15 分钟：普通请求的 `phases.total_ms` 达到 `HEALTH_SLOW_REQUEST_MS`，或任意请求的 `phases.llm_ms` 达到 `HEALTH_SLOW_LLM_MS`；SSE 的连接总时长不参与判断，`interrupted` 和 `/metrics` 也不参与。告警状态写入 `alerts_state.json`，事件写入 `alerts.log`，同一故障冷却 30 分钟；恢复事件会单独记录。Webhook 是尽力而为的通知渠道，不能替代本地台账。
