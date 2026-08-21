# API 协议规范

> 文档版本：`0.2`
> 适用应用版本：`0.1.0`
> 最近核对：`2026-08-21`

当前 API 是面向 React 前端和受控运维客户端的自定义 REST + SSE 协议，不是 OpenAI API 兼容层。路由和请求模型的唯一事实源是 `backend/main.py`、`backend/api/` 和运行实例的 `/openapi.json`。

本地启动：

```powershell
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1
```

单 worker 是硬约束。生产 Nginx 不公开 `/docs`、`/openapi.json`、live、ready 或 metrics；需要接口定义时从同版本受控环境导出。

## 通用约定

- JSON 响应使用 UTF-8。
- 每个 HTTP 响应带服务端生成的 `X-Request-Id`。
- `/api/chat` 响应还带 `X-Session-Id` 和 `X-Message-Id`。
- HTTP 校验错误使用 FastAPI 标准 `{"detail": ...}` 结构。
- SSE 通常先返回 HTTP 200，业务成功或失败必须读取终态事件。
- Cookie 认证的生产写请求必须带与 `PUBLIC_BASE_URL` 同源的 `Origin`；Bearer 请求不使用 Cookie CSRF 检查。

## 鉴权模式

| 模式 | 凭据 | 使用范围 |
| --- | --- | --- |
| `dev` + `legacy` | 无 | 仅本机开发，服务端注入 local admin |
| `single_user` + `legacy` | `X-API-Key: <APP_PASSWORD>` | 单人受控部署 |
| `multi_user` + `users` | HttpOnly Cookie 或受控 Bearer | 当前生产模式 |

浏览器通过 `/api/auth/login` 获取 HttpOnly Cookie。该接口**不在 JSON 中返回 token**，前端也不持久化 Bearer。后端能验证 `Authorization: Bearer <token>`，但当前仓库没有面向普通脚本的 Bearer 签发接口；不得从浏览器 Cookie 中提取 token 作为运维方案。

`APP_PASSWORD` 在 multi-user 模式中不参与普通用户登录，也不作为 token 签名密钥。

## 路由总表

| 方法 | 路径 | 鉴权/权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/live` | 无 | 进程存活 |
| GET | `/api/ready` | 无 | 依赖与鉴权就绪 |
| GET | `/metrics` | metrics token 或 loopback | Prometheus 文本 |
| POST | `/api/auth/login` | multi-user 登录 | 设置 Cookie |
| GET | `/api/auth/me` | 已认证 | 当前用户 |
| POST | `/api/auth/logout` | 已认证 | 吊销 token 并删除 Cookie |
| POST | `/api/chat` | 已认证 | 严格知识库 SSE 问答 |
| POST | `/api/chat/general` | 已认证 | 显式通用办公 SSE 回答 |
| POST | `/api/chat/draft` | 已认证 | 基于资料 SSE 起草 |
| POST | `/api/feedback` | 已认证、功能开启、回答归属本人 | 提交一次反馈 |
| GET | `/api/feedback/review` | admin | 待评审反馈 |
| PATCH | `/api/feedback/{feedback_id}/review` | admin | 完成反馈评审 |
| POST | `/api/upload` | editor/admin | 上传并创建后台任务 |
| GET | `/api/tasks/{task_id}` | 任务所有者/admin | 查询任务 |
| GET | `/api/kb/documents` | 已认证 | ACL 过滤的文档列表 |
| DELETE | `/api/kb/documents/{source}` | 获授权 editor/admin | 删除文档 |
| DELETE | `/api/kb/clear` | admin | 清空知识库 |
| GET | `/api/kb/stats` | 已认证 | ACL 过滤的统计 |
| GET | `/api/health` | 已认证 | 向量库健康 |
| GET | `/api/diagnostics` | admin | 聚合只读诊断 |

## 认证接口

### `POST /api/auth/login`

只在 `multi_user/users` 可用。请求：

```json
{"username":"alice","password":"secret"}
```

成功设置 `rag_access`（或 `AUTH_COOKIE_NAME`）HttpOnly Cookie：

```json
{"expires_in":28800,"user":{"id":1,"username":"alice","roles":["viewer"],"department":"hr"}}
```

`401` 表示凭据错误，`429` 表示 60 秒窗口内失败过多，`503` 表示生产或用户鉴权未就绪，非 multi-user 模式返回 `404`。

### `GET /api/auth/me`

返回：

```json
{"user":{"id":1,"username":"alice","roles":["viewer"],"department":"hr"}}
```

### `POST /api/auth/logout`

吊销当前 token 并删除 Cookie：

```json
{"ok":true}
```

## SSE 问答

三个问答接口均返回 `text/event-stream`，响应头含 `Cache-Control: no-cache` 和 `X-Accel-Buffering: no`。

合法事件序列只有：

```text
token (0..N 次) -> done
token (0..N 次) -> error，然后连接关闭
```

所有 `data:` 都是单行 JSON：

```text
event: token
data: "答案片段"

event: done
data: {"sources":[],"is_kb":true,"is_reject":false}

event: error
data: {"code":"generation_error","message":"安全提示","partial":false}
```

客户端必须按空行切分事件，再 JSON 解析 `data`。不能假定固定的 meta -> token -> done 顺序。

### `POST /api/chat`

请求：

```json
{"query":"年假如何计算？","session_id":"可选兜底值"}
```

请求头：

- `X-Session-Id`：优先于 body 的 `session_id`；服务端校验当前用户所有权。
- `X-Message-Id`：可选 UUID；省略时服务端生成。启用质量记录后，重复使用返回 409。

正常 `done` 可能包含：`sources`、`source_refs`、`citation_validation`、`thought`、`is_reject`、`is_kb_busy`、`fallback_allowed`、`draft_allowed`、`from_cache`、`query`、`retrieval_query`、`message_id`、`feedback_eligible`。

会话历史保存在服务端进程内。只有成功且非空的完成回答写入历史；错误和中断内容不写入。

### `POST /api/chat/general`

请求：

```json
{"query":"起草一份普通会议通知"}
```

只能由用户显式触发。成功终态带 `is_kb=false`、`is_general=true`。

### `POST /api/chat/draft`

请求：

```json
{"query":"起草通知","retrieval_query":"年假制度"}
```

使用当前用户可见的有效资料。成功终态带 `is_kb=true`、`is_draft=true`。

### 稳定 SSE 错误码

| code | 含义 |
| --- | --- |
| `authentication` | 上游模型鉴权失败 |
| `rate_limit` | 上游限流或额度不足 |
| `model_not_found` | 模型不存在或不可用 |
| `empty_response` | 模型未返回有效正文 |
| `retrieval_error` | 知识库检索不可用 |
| `document_governance_unresolved` | 文档生效关系未确认，问答 fail closed |
| `generation_error` | 其他生成失败 |

客户端断开后，服务端约每 200ms 检查一次并通知生产者停止。这是尽力取消，不能承诺供应商侧立即终止推理或计费。

## 文档上传和任务

### `POST /api/upload`

`multipart/form-data`：

| 字段 | 必需 | 说明 |
| --- | --- | --- |
| `files` | 是，可重复 | PDF/DOCX/TXT/MD；空文件忽略 |
| `classification` | 否 | `policy/process/benefit/technical/other` |
| `department` | 否 | ASCII 部门代码 |
| `visibility` | 否 | `all/department/private` |

`owner_id` 由服务端生成，客户端不能提交。editor 只能创建本部门 private/department 文档；admin 可管理全局文档。单文件上限 20MB，批次默认上限 200MB。

成功示例：

```json
{"success":true,"task_id":"upload_xxx","async_tasks":["upload_xxx"]}
```

常见错误：400 无有效内容或业务校验失败；403 权限不足；422 元数据非法；429 队列已满；503 元数据不可读取。

### `GET /api/tasks/{task_id}`

任务仅所有者和 admin 可见；不存在、已淘汰或属于其他用户时均返回 404。响应包含 `name/status/stage/progress/file_count/total_bytes/message` 等任务快照。`status` 为 `pending/processing/done/partial/error`，后三项是终态。服务端只保留最近 20 个终态快照；重启后任务快照丢失。

## 知识库管理

### `GET /api/kb/documents`

返回当前用户可见文档：

```json
{"documents":[]}
```

### `DELETE /api/kb/documents/{source}`

路径参数可包含子路径。403 表示无权删除；404 表示文档不存在；409 表示知识库更新中；503 表示元数据不可用。

### `DELETE /api/kb/clear`

仅 admin。成功返回 `{"ok":true}`；更新中返回 409。

### `GET /api/kb/stats`

返回 ACL 过滤后的 `doc_count/chunk_count` 和向量健康状态。只有 admin 获得 manifest `generation`，其他用户为 `null`。

### `GET /api/health`

已认证的轻量向量库状态：

```json
{"ok":true,"vector_status":"正常"}
```

它不等同 `/api/ready`。

## 质量反馈

功能默认关闭；未配置时以下接口返回 503。

### `POST /api/feedback`

```json
{"message_id":"UUID","verdict":"down","reason":"wrong_source"}
```

`up` 不接受 reason；`down` 必须使用：`not_accurate`、`missing_source`、`wrong_source`、`refusal_unexpected`、`latency`、`irrelevant`、`unsafe`、`other`。

用户只能反馈自己的、服务端标记为 eligible 的回答。相同反馈幂等，反向 verdict 冲突。错误包括 403、404、409、422、429、503。

### `GET /api/feedback/review`

仅 admin。查询参数 `status=pending|accepted|rejected`，`limit=1..100`，默认 50。

### `PATCH /api/feedback/{feedback_id}/review`

仅 admin：

```json
{"accepted":true,"adjudication_result":"recall_failure"}
```

结果只能是 `recall_failure`、`answer_failure`、`not_a_failure`、`duplicate`。已完成或不存在返回 409。

## 探针、诊断和指标

### `GET /api/live`

无鉴权：`{"ok":true}`。只证明进程可响应。

### `GET /api/ready`

无鉴权。成功返回 HTTP 200 和 `ready=true`；鉴权模式、模型可用性、向量库或 manifest 不满足契约时返回 503。空知识库是合法状态，更新窗口可返回 200 和 `status=updating`。

### `GET /api/diagnostics`

仅 admin。聚合 Embedding/Reranker 本地状态、向量库、manifest、LLM 配置和最近错误摘要。`checks.llm.network_verified=false`，不会产生模型调用费用。

### `GET /metrics`

不在 `/api` 前缀下。设置 `METRICS_TOKEN` 时接受：

```text
X-Metrics-Token: <token>
Authorization: Bearer <token>
```

未设置 token 时仅 loopback 可访问。响应类型为 Prometheus `text/plain; version=0.0.4`。

## 通用 HTTP 状态

| 状态 | 含义 |
| --- | --- |
| 400 | 请求内容或业务校验失败 |
| 401 | 凭据缺失、错误、过期或吊销 |
| 403 | 身份有效但权限不足，或 Cookie Origin 不可信 |
| 404 | 接口未启用、资源不存在或为防信息泄漏而隐藏 |
| 409 | 状态冲突、知识库更新中或重复终态操作 |
| 422 | Pydantic/元数据校验失败 |
| 429 | 登录/反馈限流或上传队列已满 |
| 503 | 部署、鉴权、质量服务或依赖未就绪 |

## 兼容和变更

当前 API 没有 URL 版本前缀。修改路径、字段、SSE 终态、稳定错误码、权限或 Cookie 行为都属于破坏性风险，必须同步更新前端、契约测试、本文件和 `CHANGELOG.md`，并在发布前保存同版本 `/openapi.json` 作为验收证据。
