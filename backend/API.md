# API 协议规范（backend）

统一走 OpenAI 兼容无关的自定义 REST + SSE 协议。启动：`uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1`（单 worker 红线：上传闸门/Chroma 写锁/BM25 为单进程对象）。

## 鉴权

配置 `APP_PASSWORD` 后，所有请求必须携带请求头：

```
X-API-Key: <password>
```

未配置 `APP_PASSWORD` 时鉴权跳过（本地开发）。鉴权失败返回 401。

## 会话

- 客户端在首次响应头 `X-Session-Id` 中取得会话 ID（服务端生成），后续请求通过 **请求头 `X-Session-Id`** 传递（优先级最高）；
- 兜底：JSON body 的 `session_id` 字段（header 缺失时生效）；
- 会话历史存服务端进程内（重启丢失；Phase 3 迁 Redis）。

## 问答 SSE（POST /api/chat）

请求体：`{"query": "...", "session_id": "可选"}`

响应：`text/event-stream`。事件序列**只有两种合法形态**：

```
1. 正常：event: token ...（0 或 N 次）→ event: done
2. 失败：event: error（随后连接关闭，无 done）
```

### data 编码（统一 JSON）

所有 `data:` 均为 JSON（`ensure_ascii=False`，紧凑）：

- `token`：`data: "答案片段"`（JSON 字符串）。**含换行/空行的片段由 json.dumps 转义为单行**，客户端必须 JSON.parse 后拼接，禁止把空行当事件分隔；
- `done`：`data: {"sources": [...], "is_kb": true, "is_reject": false, "from_cache": false, ...}`；
- `error`：`data: {"code": "<稳定错误码>", "message": "面向用户的安全文案"}`。

客户端解析规则：读到 `event:` 行后按空行切分事件；对每条 `data:` 行尝试 JSON.parse，失败才按纯文本处理（当前实现全部为 JSON，此分支仅为兼容）。

### 稳定错误码枚举（error 事件）

| code | 含义 | 触发 |
|---|---|---|
| `authentication` | 模型服务鉴权失败（401/403/Key 无效） | DashScope Key 错误 |
| `rate_limit` | 模型服务限流或额度不足（429） | 并发过高/欠费前的限流 |
| `model_not_found` | 模型名不存在或不可用（404） | OPENAI_MODEL 配错 |
| `generation_error` | 其他生成失败 | 网络、空响应等兜底 |

> 分类规则复用 `enterprise_rag.services.rag_service._general_failure_details`，与业务层一致。

### 断连语义

- 客户端 `AbortController.cancel()` 或网络断开 → 服务端检测 `request.is_disconnected()` 后通知生产者停止迭代，并在生产者线程内 `close()` 生成器（触发中断保留逻辑，不保存缓存）；
- 尽力而为限制：LLM 同步客户端的上游请求停止依赖生成器关闭；彻底 async 化在 Phase 3。

## 上传（POST /api/upload）

- `multipart/form-data`，字段名 `files`（可多个）；
- 响应：`{"success": true, "task_id": "upload_xxx", ...}`；
- 服务端**流式**写入暂存（`_UploadFileAdapter` 直接读 UploadFile 临时文件），不整批复制进内存；
- 大小/批次上限沿用 `document_service` 配置（单文件 20MB、批次 200MB）。

## 任务轮询（GET /api/tasks/{task_id}）

返回 `{"name","status","stage","progress","file_count","total_bytes","message",...}`；`status` 为 `pending|processing|completed|error`。客户端 1–2s 轮询。

## 知识库管理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/kb/documents | 文档列表 |
| DELETE | /api/kb/documents/{source} | 删除单个文档（404 表示不存在或忙） |
| DELETE | /api/kb/clear | 清空知识库（409 表示知识库正在更新） |
| GET | /api/kb/stats | doc_count / chunk_count / generation / healthy |
| GET | /api/health | 健康检查 |

## 前端状态机要求

- 不要依赖"固定 meta→token→done"序列；只按事件类型驱动：token 追加文本、done 收尾并取 sources、error 展示错误并按 code 分类提示；
- 停止按钮调用 AbortController 即可，无需等 done。
