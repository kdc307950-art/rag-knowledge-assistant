# 文档中心

> 文档版本：`0.1`
> 适用应用版本：`0.1.0`
> 状态：维护中
> 最近核对：`2026-08-21`

本目录按读者和任务组织。根 `README.md` 是入口，不是配置、API 或生产运行事实源。

## 阅读路径

| 角色 | 必读顺序 |
| --- | --- |
| 新开发人员 | [本地开发](getting-started.md) -> [架构](architecture.md) -> [API](../backend/API.md) |
| 部署人员 | [配置参考](configuration-reference.md) -> [生产部署](deployment-runbook.md) -> [验收](acceptance-and-known-limits.md) |
| 发布人员 | [升级与回滚](upgrade-rollback.md) -> [生产部署](deployment-runbook.md) -> [变更记录](../CHANGELOG.md) |
| 运维人员 | [运维手册](operations-runbook.md) -> [运维契约](ops-contract.md) -> [安全手册](security.md) |
| 安全/审计人员 | [安全手册](security.md) -> [ACL 契约](metadata-acl-contract.md) -> [文档治理复核](document-governance-review.md) |
| 质量评审人员 | [验收](acceptance-and-known-limits.md) -> [检索评估](../eval/README.md) -> [Groundedness 契约](groundedness-contract.md) |

## 唯一事实源

| 事实 | 唯一事实源 | 文档职责 |
| --- | --- | --- |
| 应用版本、Python 约束、Python 依赖 | `pyproject.toml`、`uv.lock` | 解释安装和兼容范围，不另造版本号 |
| 前端依赖和命令 | `frontend/package.json`、`frontend/package-lock.json` | 解释运行和验证方法 |
| 环境变量、默认值、配置校验 | `enterprise_rag/config.py` | `configuration-reference.md` 提供可读索引 |
| 环境模板 | `.env.example` | 提供可复制的非秘密模板；默认值冲突时以代码为准 |
| HTTP 路径、请求模型、响应行为 | `backend/main.py`、`backend/api/`、运行时 `/openapi.json` | `backend/API.md` 解释稳定协议和权限 |
| 生产镜像和进程命令 | `Dockerfile` | 部署文档解释前提、验证和失败处理 |
| Compose 服务、卷、端口和健康检查 | `docker-compose.yml` | 部署/运维文档提供操作顺序 |
| HTTPS 和反向代理行为 | `nginx.conf` | 部署文档说明证书与公网边界 |
| 文档治理策略 | `config/document_governance.json` | 治理文档解释审批证据和人工结论 |
| 文档 ACL 初始映射 | `config/document_acl.json` | ACL 文档解释字段和授权语义 |
| 价格计算输入 | `config/llm_prices.json` | 价格指南解释来源和估算边界 |
| CI 验证项目 | `.github/workflows/ci.yml` | 验收文档记录当前执行结果，不写永久通过数 |

若两个事实源自身冲突，视为实现缺陷并阻断相关交付；文档不得自行选择一个值掩盖冲突。

## 受控文档清单

| 文档 | 用途 |
| --- | --- |
| [本地开发指南](getting-started.md) | 安装、启动、验证和常见本地问题 |
| [架构说明](architecture.md) | 组件、问答/入库流程、数据和并发边界 |
| [配置参考](configuration-reference.md) | 环境变量、默认值、生产必填项 |
| [生产部署手册](deployment-runbook.md) | Linux Compose 首次部署和部署验收 |
| [升级与回滚手册](upgrade-rollback.md) | 发布前备份、镜像保留、升级和回滚 |
| [运维手册](operations-runbook.md) | 日常检查、备份恢复、告警和事故响应 |
| [安全手册](security.md) | 鉴权、ACL、密钥、日志、TLS 和风险边界 |
| [验收与已知限制](acceptance-and-known-limits.md) | 交付检查、证据格式、容量未知项和限制 |
| [API 协议](../backend/API.md) | 当前 HTTP/SSE 接口、权限和错误语义 |
| [运维契约](ops-contract.md) | SLO、保留期、探针和监控语义 |
| [文档治理证据模板](document-control-evidence-template.md) | 将业务文档提升为权威来源前的人工证据包 |
| [文档治理复核](document-governance-review.md) | 当前员工手册生效关系的复核记录 |
| [ACL 契约](metadata-acl-contract.md) | 文档元数据与检索前授权规则 |
| [Groundedness 契约](groundedness-contract.md) | 引用完整性和人工支持性评估边界 |
| [LLM 价格指南](cost-pricing-guide.md) | 价格表来源和估算限制 |
| [Windows 反向代理](deployment-reverse-proxy.md) | 非生产的 Windows/Caddy/Nginx 参考拓扑 |
| [检索评估](../eval/README.md) | 黄金集格式和离线评估命令 |

## 版本与变更规则

1. 应用版本取自 `pyproject.toml`；FastAPI 暴露的版本必须与其一致。
2. 每份受控文档声明文档版本、适用应用版本和最近核对日期。
3. 行为、接口、配置、部署或安全边界变化时，同一提交内更新相应文档和 `CHANGELOG.md`。
4. 只有措辞、排版和链接变化时递增文档版本，不虚增应用版本。
5. 旧操作被替代时必须明确标记“已废弃”并指向新操作，不能保留两套无优先级步骤。
6. 交付证据记录 Git 提交、应用版本、执行环境、命令、时间和结果；历史通过数不能作为当前证据。

## 维护检查

文档变更至少执行：

```powershell
git diff --check
rg -n "qwen3[.]7-max|当前仓库的本[机]|当前生产配[置]" README.md docs backend/API.md
uv run pytest tests/test_deployment_contract.py tests/test_health_check.py tests/test_backup.py -q
```

第一条检查格式，第二条检查已经废弃的易漂移措辞，第三条验证部署、健康检查和备份契约。完整交付仍需执行 [验收清单](acceptance-and-known-limits.md)。
