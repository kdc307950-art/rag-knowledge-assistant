# 配置参考

> 文档版本：`0.1`
> 适用应用版本：`0.1.0`
> 最近核对：`2026-08-21`

运行时默认值和校验逻辑以 `enterprise_rag/config.py` 为唯一事实源。本表是便于部署的索引；修改配置后必须重启应用。

Windows 的 DeepSeek 分时价格计算依赖项目中的条件依赖 `tzdata`；Linux 通常使用系统 IANA 时区数据库。依赖安装必须使用当前锁文件，不能只复制源码运行。

## 加载优先级

进程环境 > 项目根目录 `.env` > `enterprise_rag/.env`。`.env.example` 是非秘密模板，不是运行时事实源。

## 环境与鉴权

| 变量 | 代码默认值 | 说明 |
| --- | --- | --- |
| `RAG_ENVIRONMENT` | `development` | `production` 启用生产安全门控 |
| `PUBLIC_BASE_URL` | `http://127.0.0.1:8000` | 生产必须为 `https://` 地址 |
| `DEPLOYMENT_MODE` | `dev` | `dev/single_user/multi_user` |
| `AUTH_MODE` | `legacy` | `multi_user` 必须为 `users` |
| `APP_PASSWORD` | 空 | `single_user` 必填；multi-user 中仅兼容服务账户 |
| `AUTH_SECRET` | 空 | `multi_user` 必填；生产至少 32 字节并拒绝弱模式 |
| `AUTH_DB_PATH` | `<RAG_DATA_DIR>/auth.sqlite3` | 必须位于 `RAG_DATA_DIR` 内 |
| `AUTH_TOKEN_TTL_SECONDS` | `28800` | 最短 300 秒 |
| `AUTH_COOKIE_NAME` | `rag_access` | 浏览器 HttpOnly Cookie 名 |
| `AUTH_COOKIE_SECURE` | `0` | HTTPS 生产必须为 `1` |

生产环境还要求 `DEPLOYMENT_MODE=multi_user`、`AUTH_MODE=users`、至少一个 active admin，并配置 `ALERT_WEBHOOK_URL`。

## LLM 与检索模型

| 变量 | 代码默认值 | 说明 |
| --- | --- | --- |
| `OPENAI_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容端点 |
| `OPENAI_MODEL` | `deepseek-v4-flash` | LLM 模型名 |
| `DEEPSEEK_API_KEY` | 空 | DeepSeek 端点优先使用 |
| `DASHSCOPE_API_KEY` | 空 | DashScope 端点优先使用 |
| `OPENAI_API_KEY` | 空 | 其他兼容端点或专用 Key 缺失时使用 |
| `LLM_STREAM_USAGE_MODE` | `auto` | `auto/on/off`；auto 只为 DashScope 开启 usage |
| `LLM_PRICE_TABLE_PATH` | `config/llm_prices.json` | 估算价格表，不是供应商账单 |
| `EMBEDDING_MODEL` | `BAAI/bge-small-zh-v1.5` | 可为模型 ID 或本地目录 |
| `RERANKER_MODEL` | `BAAI/bge-reranker-v2-m3` | 可为模型 ID 或本地目录 |
| `HF_HUB_OFFLINE` | `0` | `1` 禁止在线模型下载 |
| `RAG_DOCUMENT_GOVERNANCE_PATH` | `config/document_governance.json` | 文档治理配置 |

Key 选择由端点识别决定，不能通过同时填写多个 Key 猜测使用哪个。应用日志只记录 Key 名称、来源和指纹，不应记录明文。

## 运行数据、日志与缓存

| 变量 | 代码默认值 | 说明 |
| --- | --- | --- |
| `RAG_DATA_DIR` | `<项目根>/data` | 完整运行数据根目录 |
| `RAG_LOG_DIR` | `<RAG_DATA_DIR>/logs` | 日志目录 |
| `RAG_BACKUP_DIR` | `<项目根>/backups` | 备份目录；容器中由卷覆盖 |
| `LOG_LEVEL` | `INFO` | 日志等级 |
| `LOG_MAX_BYTES` | `5242880` | 单日志滚动大小 |
| `LOG_BACKUP_COUNT` | `7` | 滚动文件数 |
| `LOG_RETENTION_DAYS` | `30` | app/error 保留天数 |
| `ACCESS_LOG_RETENTION_DAYS` | `7` | access 保留天数 |
| `AUDIT_LOG_RETENTION_DAYS` | `90` | audit 保留天数 |
| `ANSWER_CACHE_ENABLED` | `1` | multi-user 中应结合 ACL 风险审查 |
| `ANSWER_CACHE_TTL_SECONDS` | `86400` | 回答缓存 TTL |
| `CACHE_SALT` | 空 | 缓存键可选 HMAC 盐 |

`RAG_KB_DIR` 已停用；设置为非默认位置会直接报错。迁移时只使用 `RAG_DATA_DIR`。

## 上传与处理

| 变量 | 代码默认值 | 说明 |
| --- | --- | --- |
| `UPLOAD_TASK_WORKERS` | `1` | 后台任务 worker，当前保持单写入 |
| `UPLOAD_PARSE_WORKERS` | `2` | 单任务并行解析数 |
| `UPLOAD_QUEUE_CAPACITY` | `4` | 执行中与等待任务总上限 |
| `UPLOAD_MAX_BATCH_MB` | `200` | 批次合计上限；单文件业务上限为 20MB |
| `UPLOAD_STAGING_TTL_SECONDS` | `86400` | 异常暂存清理期限 |
| `VECTOR_WRITE_BATCH_SIZE` | `512` | Chroma 写入批次 |
| `EMBEDDING_BATCH_SIZE` | `32` | Embedding 编码批次 |
| `RERANK_BATCH_SIZE` | `16` | Reranker 批次 |
| `RERANK_SCORE_THRESHOLD` | `0.5` | 最终过滤阈值 |

## 功能开关和版本键

| 变量 | 代码默认值 | 说明 |
| --- | --- | --- |
| `DRAFT_ENABLED` | `1` | 资料起草 |
| `FALLBACK_ENABLED` | `1` | 显式通用办公回答 |
| `PROMPT_VERSION` | `grounded-v3` | 变化时使旧缓存失效 |
| `RETRIEVAL_VERSION` | `hybrid-rerank-v2` | 变化时使旧缓存失效 |
| `MODEL_VERSION` | `OPENAI_MODEL` 值 | 模型行为版本键 |

## 质量反馈

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `QUALITY_CAPTURE_ENABLED` | `0` | 默认关闭 |
| `QUALITY_DB_PATH` | `<RAG_DATA_DIR>/quality.sqlite3` | 必须位于数据目录内 |
| `QUALITY_ENCRYPTION_KEY` | 空 | Fernet key；启用时必填并独立保管 |
| `QUALITY_RETENTION_DAYS` | `30` | 需外部定时执行清理脚本 |
| `QUALITY_MAX_QUERY_CHARS` | `4000` | 入库前问题上限 |
| `QUALITY_MAX_ANSWER_CHARS` | `12000` | 入库前答案上限 |
| `QUALITY_FEEDBACK_LIMIT_PER_HOUR` | `30` | 每用户每小时上限 |

## 监控和外部检查

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `METRICS_TOKEN` | 空 | 未设置时 `/metrics` 仅允许 loopback |
| `ALERT_WEBHOOK_URL` | 空 | 生产和 monitoring profile 必填 |
| `GRAFANA_ADMIN_PASSWORD` | 空 | monitoring profile 必填且拒绝常见默认口令 |
| `RAG_READY_URL` | `http://127.0.0.1:8000/api/ready` | 外部检查地址 |
| `RAG_ALERT_STATE` | `<RAG_LOG_DIR>/alerts_state.json` | 外部检查冷却状态 |
| `RAG_ALERT_LOG` | `<RAG_LOG_DIR>/alerts.log` | 外部检查告警台账 |
| `HEALTH_BACKUP_MAX_AGE_DAYS` | `7` | 最近验证备份最大年龄 |
| `HEALTH_MIN_FREE_GB` | `1` | 最低剩余磁盘空间 |
| `HEALTH_ERROR_RATE_THRESHOLD` | `0.05` | 错误率阈值 |
| `HEALTH_ERROR_MIN_REQUESTS` | `10` | 计算错误率的最低请求数 |
| `HEALTH_AUTH_FAILURE_MIN` | `2` | 模型鉴权失败阈值 |
| `HEALTH_SLOW_REQUEST_MS` | `60000` | 普通请求慢阈值 |
| `HEALTH_SLOW_LLM_MS` | `30000` | LLM 阶段慢阈值 |
| `HEALTH_SLOW_MIN_COUNT` | `3` | 慢请求告警最低次数 |

## 代码固定参数

以下当前不是环境变量，不能只改 `.env`：`INITIAL_RETRIEVAL_K=50`、`FINAL_TOP_K=8`、`HYBRID_ALPHA=0.5`、父块 `2000/200`、子块 `500/50`、最大上下文 `8000`。修改这些值属于实现和评估变更，必须同步更新检索基线和变更记录。
