# 企业知识库智能助手

[![CI](https://github.com/kdc307950-art/rag-knowledge-assistant/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kdc307950-art/rag-knowledge-assistant/actions/workflows/ci.yml)

> 文档版本：`0.1`
> 适用应用版本：`0.1.0`
> 最近核对：`2026-08-21`

企业内部 RAG（检索增强生成）应用，当前主入口为 React + FastAPI。系统采用严格知识库模式：除明确问候语外，问题先检索企业知识库；没有可靠资料时拒答，只有用户主动选择后才使用带明确标识的通用办公回答。

本 README 只提供项目入口。配置、接口、部署和运维细节以 [文档中心](docs/README.md) 中声明的事实源和专题文档为准。

## 已有能力

- 本地用户登录、`admin/editor/viewer` 角色和文档 ACL；
- SSE 流式知识库问答、来源追溯、严格拒答、通用办公回答和资料起草；
- PDF、DOCX、TXT、MD 批量上传，后台解析、任务轮询和文档管理；
- Chroma 分块存储、SQLite manifest 原子激活、向量 + BM25 混合检索和重排；
- 可选回答质量反馈、离线检索/引用评估；
- HTTPS Nginx 入口、Prometheus、Grafana、Alertmanager 和企业微信通知；
- 就绪探针、结构化日志、离线备份、校验与恢复演练。

没有列入上述范围的能力不能从文档推定为已支持。

## 快速开始

要求：Python `3.10` 或 `3.11`、Node.js `22`、`uv`。首次运行需要联网下载 Embedding 与 Reranker 模型，或提前准备本地模型目录。

```powershell
uv sync --group dev
Copy-Item .env.example .env
```

实际默认 LLM 端点由 `enterprise_rag/config.py` 定义，当前为 DeepSeek 兼容接口。编辑 `.env`，至少填写对应密钥：

```env
DEEPSEEK_API_KEY=replace-with-real-key
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-v4-flash
```

启动后端和前端：

```powershell
# 终端 1；单 worker 是当前架构硬约束
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1

# 终端 2
Set-Location frontend
npm ci
npm run dev
```

浏览器访问 `http://127.0.0.1:5173`。本地开发模式不启用用户鉴权，不得直接暴露到局域网或公网。完整步骤和验证方法见 [本地开发指南](docs/getting-started.md)。

## 当前架构边界

```text
Browser
  -> React
  -> FastAPI REST + SSE
  -> RAG service
  -> Embedding / Reranker / LLM
  -> Chroma + SQLite manifest/cache/auth/quality
```

- 必须保持单应用进程、单 worker、单写实例；上传闸门、Chroma 写锁、BM25、任务快照和会话历史均包含进程内状态。
- 当前是单机部署，不是多实例或多租户 SaaS。
- 会话历史保存在进程内，应用重启后丢失。
- Chroma/HNSW 没有本项目认可的在线热快照契约；备份和恢复必须先停写。
- `RAG_DATA_DIR` 是完整运行数据单元，不得只迁移 `kb_data` 或单个 SQLite 文件。

完整设计和数据流见 [架构说明](docs/architecture.md)，部署限制见 [验收与已知限制](docs/acceptance-and-known-limits.md)。

## 生产部署入口

当前受支持的生产拓扑是原生 Linux + Docker Compose + Nginx HTTPS。WSL2 和 Windows 反向代理只用于本地或受控内网部署，不作为正式生产拓扑。

```bash
cp .env.example .env
docker compose config --quiet
docker compose build
docker compose run --rm --no-deps backend \
  python scripts/create_user.py admin --department general --role admin
docker compose up -d
docker compose ps
```

生产启动前必须完成密钥、HTTPS 证书、管理员、监控通知和备份准备，不能只执行以上命令。按顺序使用：

1. [生产部署手册](docs/deployment-runbook.md)
2. [升级与回滚手册](docs/upgrade-rollback.md)
3. [运维手册](docs/operations-runbook.md)
4. [安全手册](docs/security.md)
5. [验收与已知限制](docs/acceptance-and-known-limits.md)

## 开发验证

```powershell
npx --yes pyright@1.1.311
uv run pytest tests -q

Set-Location frontend
npm run lint
npm run test
npm run build
```

测试通过数不写死在说明书中；每次交付必须记录本次实际命令、提交号、环境、通过结果和未执行项。

## 关键文档

| 读者/任务 | 文档 |
| --- | --- |
| 全部读者、事实源与版本规则 | [文档中心](docs/README.md) |
| 开发人员、本地运行 | [本地开发指南](docs/getting-started.md) |
| 开发与评审人员、系统设计 | [架构说明](docs/architecture.md) |
| 部署人员、环境变量 | [配置参考](docs/configuration-reference.md) |
| API 与前端开发 | [API 协议](backend/API.md) |
| 生产部署人员 | [生产部署手册](docs/deployment-runbook.md) |
| 发布人员 | [升级与回滚手册](docs/upgrade-rollback.md) |
| 运维和值班人员 | [运维手册](docs/operations-runbook.md) |
| 安全和审计人员 | [安全手册](docs/security.md) |
| 验收人员 | [验收与已知限制](docs/acceptance-and-known-limits.md) |
| 变更追踪 | [变更记录](CHANGELOG.md) |

## 文档治理

文档与实现冲突时，不按“看起来更新”判断。先依据 [文档事实源表](docs/README.md#唯一事实源)确认权威来源，再修正文档或实现。涉及安全、数据恢复或对外 API 的差异必须阻断发布，不能口头豁免。
