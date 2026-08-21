# 架构说明

> 文档版本：`0.1`
> 适用应用版本：`0.1.0`
> 最近核对：`2026-08-21`

## 设计原则

1. RAG-First：非问候输入统一进入知识库检索。
2. 检索即裁决：没有满足条件的片段就拒答，不用模型通用知识补齐。
3. 显式降级：通用办公回答只能由用户主动触发，并标记为非知识库内容。
4. 来源可追溯：回答终态携带本次检索来源和稳定的回答内来源编号。
5. 检索前授权：ACL 在向量或 BM25 候选返回前执行，客户端不能自行扩大可见范围。
6. 单一提交点：SQLite manifest 决定来源的当前激活 revision 和知识库 generation。

## 组件

```text
frontend/src
  React UI、Cookie 会话、SSE、上传与任务轮询
        |
backend/api
  REST/SSE、鉴权、权限、探针、指标
        |
enterprise_rag/services
  Chat、RAG、Document、Diagnostics、Quality 编排
        |
enterprise_rag/rag + enterprise_rag/llm
  混合召回、重排、上下文、提示词、模型调用
        |
enterprise_rag/storage
  Chroma、manifest、ACL、缓存、用户与治理
```

主要入口：

- `backend/main.py`：FastAPI 应用与路由组装；
- `enterprise_rag/services/rag_service.py`：检索决策、回答、拒答、通用回答和起草；
- `enterprise_rag/services/document_service.py`：上传暂存、后台解析和串行写入；
- `enterprise_rag/storage/vector_store.py`：Chroma 和可见性查询；
- `enterprise_rag/storage/kb_manifest.py`：激活 revision 与 generation；
- `frontend/src/`：当前用户界面。

## 问答流程

```text
输入
  -> 明确问候？是：静态回复
  -> 必要时选择少量历史并改写指代型追问
  -> ACL + 文档治理生成可见 revision 集合
  -> 向量 + BM25 召回
  -> Reranker、去重、阈值过滤
  -> 无可靠片段：严格拒答
  -> 有可靠片段：基于 context 流式生成
  -> SSE done 返回来源；失败返回唯一 error 终态
```

客户端中止只能尽力停止同步供应商调用，不能保证立即终止供应商侧推理或计费。中止或失败的部分正文不写入后续会话历史。

## 文档入库流程

```text
multipart 上传
  -> 分块写入 upload_staging
  -> 有界任务队列
  -> 最多 2 路并行解析
  -> 按上传顺序串行切分、Embedding、写 Chroma staging revision
  -> 校验分块数量和 ID
  -> manifest 原子激活 source revision
  -> 尽力清理旧 revision
  -> 批次结束刷新 BM25
```

上传批次持有知识库变更闸门。更新期间检索快速返回 busy，不读取中间状态。旧 revision 即使物理清理失败，也不会在 manifest 激活集合中可见。

## 数据一致性

- Chroma 保存物理分块；manifest 保存逻辑可见版本。
- manifest 是唯一提交点，不声明 Chroma 与 SQLite 之间存在跨数据库 ACID。
- 缓存键和写入前后校验使用 manifest generation，防止知识库变化后旧答案复活。
- `RAG_DATA_DIR` 包含知识库、manifest、缓存、用户和可选质量数据，是一个备份恢复单元。
- 上传暂存是未提交工作，不进入正式备份。

## 鉴权与 ACL

| 模式 | 用途 | 鉴权 |
| --- | --- | --- |
| `dev` | 本地开发 | 本地 admin 上下文，不对外暴露 |
| `single_user` | 单人受控使用 | `X-API-Key` |
| `multi_user` | 当前生产模式 | HttpOnly Cookie；受控客户端可用 Bearer |

角色为 `admin`、`editor`、`viewer`。文档 visibility 为 `all`、`department`、`private`。完整授权规则见 [ACL 契约](metadata-acl-contract.md)。

## 并发与扩展边界

以下状态位于单进程内：上传队列、写入锁、BM25 状态、任务快照、HTTP 会话历史。因而：

- FastAPI 必须 `--workers 1`；
- 不支持多个实例共享同一 `RAG_DATA_DIR`；
- 不支持滚动多实例升级；
- 不支持跨实例会话或任务恢复；
- 高并发扩展需要重新设计队列、状态存储和向量写入所有权，当前文档不声称具备这些能力。

## 可观测性

- `/api/live`：进程存活；
- `/api/ready`：鉴权、模型可用性、向量库和 manifest 一致性；
- `/metrics`：Prometheus 指标，独立 token 或仅 loopback；
- `access.log`：请求与 SSE 业务终态；
- `audit.log`：鉴权和知识库变更事件；
- `error.log`：异常；
- 外部健康检查：同机进程外监督，不等同主机外可用性监控。

详细语义见 [运维契约](ops-contract.md)。
