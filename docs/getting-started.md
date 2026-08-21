# 本地开发指南

> 文档版本：`0.1`
> 适用应用版本：`0.1.0`
> 最近核对：`2026-08-21`

本文只适用于本地开发和验证。生产环境使用 [生产部署手册](deployment-runbook.md)。

## 环境要求

- Windows PowerShell 或原生 Linux shell；
- Python `3.10` 或 `3.11`，不支持 Python `3.12`；
- `uv`；
- Node.js `22`；
- 可用的 OpenAI 兼容模型接口；
- 首次启动可访问 Hugging Face，或已经准备本地 Embedding/Reranker 模型。

版本约束以 `pyproject.toml`、`uv.lock`、`frontend/package.json` 和 `frontend/package-lock.json` 为准。

## 安装

在项目根目录执行：

```powershell
uv sync --group dev
Copy-Item .env.example .env

Set-Location frontend
npm ci
Set-Location ..
```

不要把真实 `.env`、模型密钥或运行数据提交到 Git。

## 配置模型

当前代码默认使用 DeepSeek 兼容端点：

```env
DEEPSEEK_API_KEY=replace-with-real-key
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-v4-flash
```

DashScope 示例：

```env
DASHSCOPE_API_KEY=replace-with-real-key
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_MODEL=replace-with-supported-model
```

其他 OpenAI 兼容服务使用 `OPENAI_API_KEY`。配置优先级为：进程环境 > 根目录 `.env` > `enterprise_rag/.env`。完整定义见 [配置参考](configuration-reference.md)。

## 启动

终端 1：

```powershell
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1
```

终端 2：

```powershell
Set-Location frontend
npm run dev
```

打开 `http://127.0.0.1:5173`。后端 API 文档默认可从 `http://127.0.0.1:8000/docs` 查看；协议边界以 [API 协议](../backend/API.md) 为准。

## 首次验证

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/live
Invoke-RestMethod http://127.0.0.1:8000/api/ready
```

预期：`live` 返回 `ok=true`；`ready` 返回 HTTP 200 和 `ready=true`。模型文件缺失、向量库异常或鉴权配置错误时，`ready` 返回 503，应先解决具体状态而不是绕过探针。

## 测试

```powershell
npx --yes pyright@1.1.311
uv run pytest tests -q

Set-Location frontend
npm run lint
npm run test
npm run build
```

这些命令可能加载真实临时 Chroma 数据，但不应调用付费 LLM。每次交付记录本轮实际输出，不复用历史通过数。

## 本地数据

默认运行数据位于 `data/`：

- `kb_data/`：Chroma 数据；
- `kb_manifest.sqlite3`：激活版本清单；
- `auth.sqlite3`：本地用户和 token 吊销记录；
- `answer_cache.sqlite3`：回答缓存；
- `quality.sqlite3`：可选质量反馈；
- `upload_staging/`：未提交上传暂存；
- `logs/`：结构化日志。

不要只复制、删除或恢复其中一部分来模拟迁移。完整数据边界见 [运维手册](operations-runbook.md)。

## 常见问题

**首次启动很慢**：Embedding 和 Reranker 可能正在下载。离线环境必须先准备本地模型，再设置 `HF_HUB_OFFLINE=1`。

**模型鉴权失败**：检查端点对应的专用 Key。DeepSeek 优先读取 `DEEPSEEK_API_KEY`，DashScope 优先读取 `DASHSCOPE_API_KEY`，其他端点读取 `OPENAI_API_KEY`。修改后重启后端。

**知识库损坏**：看到 `Error loading hnsw index` 时先停止服务并保留现场，不要直接删除数据。按 [运维手册](operations-runbook.md) 的恢复流程处理。

**测试不退出或临时目录拒绝访问**：项目已把 pytest 临时目录固定在工作区；确认没有另一个测试进程占用 `.pytest-tmp-work`。
