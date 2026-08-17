# 企业知识库智能助手

企业内部 RAG（检索增强生成）应用，面向制度、流程、福利、项目资料和业务文档的检索问答。前端正从 Streamlit 迁移到 React + FastAPI：React M2 已提供鉴权、运行诊断和 SSE 流式问答；上传、任务进度和文档管理仍由 Streamlit 过渡层承载，M4 验收前不得删除该层。

系统默认采用严格知识库模式：除明确问候语外，所有问题先检索企业知识库；检索不到时直接拒答，不自动使用模型通用知识补全。用户只有在界面中主动点击后，才能切换到带醒目标识的通用办公回答。

## 目录

- [快速开始](#快速开始)
- [架构说明](#架构说明)
- [核心流程](#核心流程)
- [代码结构](#代码结构)
- [配置项](#配置项)
- [数据与运维](#数据与运维)
- [测试与排障](#测试与排障)

## 快速开始

### 环境要求

- Python `3.10` 或 `3.11`（当前固定依赖不支持 Python 3.12）
- Streamlit `1.61.1`
- Node.js（本机已用 `v24.19.0` 验证 React 前端构建）
- 已安装 `uv`
- 可访问 OpenAI 兼容接口（默认配置兼容 DashScope）

### 安装依赖

在项目根目录执行：

```powershell
uv sync --group dev
```

### 配置模型接口

默认使用 DashScope 时，在项目根目录创建 `.env` 文件：

```env
DASHSCOPE_API_KEY=your-dashscope-api-key
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_MODEL=qwen3.7-max
```

连接其他 OpenAI 兼容服务时使用：

```env
OPENAI_API_KEY=your-provider-api-key
OPENAI_BASE_URL=https://your-openai-compatible-endpoint/v1
OPENAI_MODEL=your-model-name
```

未设置 `OPENAI_BASE_URL` 和 `OPENAI_MODEL` 时，系统使用 `enterprise_rag/config.py` 中的默认值。连接 DashScope 时优先读取 `DASHSCOPE_API_KEY`，避免误用操作系统中为其他服务配置的 `OPENAI_API_KEY`；未提供专用变量时仍兼容原有 `OPENAI_API_KEY`。

配置优先级为：启动进程中显式设置的环境变量 > 项目根目录 `.env` > `enterprise_rag/.env`。因此部署平台或启动脚本可以覆盖本地文件，而根目录配置仍可覆盖包内示例值。更新 Key 或运行目录后必须重启 Streamlit 服务；日志只输出 Key 指纹，不输出明文。

首次启动需要下载 Embedding 与 Reranker 模型。可联网环境保持 `HF_HUB_OFFLINE=0`；离线部署应先下载模型，再设置 `HF_HUB_OFFLINE=1`，也可以直接把 `EMBEDDING_MODEL`、`RERANKER_MODEL` 指向包含 `config.json` 的本地模型目录。

### 启动 React 问答界面（当前主入口）

```powershell
# 终端 1：FastAPI，必须保持单进程单 worker
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1

# 终端 2：React 开发服务器
Set-Location frontend
npm install
npm run dev
```

浏览器访问：`http://127.0.0.1:5173`

React M2 使用 `POST /api/chat` 的 `token* -> done | error` SSE 协议。停止按钮会中止浏览器请求并保留已显示正文，但同步模型调用只能尽力停止，不能保证供应商侧立即停止推理或计费。服务端生成的 `X-Session-Id` 会保存在浏览器本地并随下一问回传；退出或鉴权失败会清除它。

### 过渡期 Streamlit 界面

上传、任务进度、文档列表和清空操作尚未迁移到 React。需要这些功能时仍可启动：

```powershell
uv run streamlit run enterprise_rag/app.py --server.port 8501
```

## 架构说明

### 设计原则

1. **RAG-First**：非问候输入统一进入检索流程，不依赖 LLM 预判是否走知识库。
2. **检索即裁决**：是否生成回答只由本次检索结果决定，不使用二次置信度猜测。
3. **严格拒答**：没有可靠资料时返回“知识库中未找到”，不会自动调用通用模型。
4. **显式 G2**：用户主动点击“改用通用办公回答”后，才调用通用模型，并标注“不基于企业知识库”。按钮只在生成成功后标记为已使用，鉴权失败、空响应或流式中断时保留重试入口。
5. **历史最小化**：只有指代型追问才使用历史改写；独立问题不会继承上一轮主题。
6. **来源可追溯**：回答元数据保存来源、章节、页码或段落信息。

### 分层结构

```text
表现层（迁移中）
  frontend/src/            React：鉴权、诊断、SSE 问答与停止（M2）
  enterprise_rag/app.py
    ├─ ui/chat_ui.py       过渡期聊天、G2/起草按钮
    └─ ui/sidebar.py       过渡期文件上传、任务状态、知识库管理

业务编排层
  services/chat_service.py       问候白名单、历史选择、RAG 编排
  services/rag_service.py        检索决策、严格回答、拒答、G2、起草
  services/document_service.py   磁盘暂存、有界后台队列、并行解析、串行入库和任务进度
  services/cache_service.py     会话缓存与持久化缓存适配

检索与生成层
  rag/retriever.py               召回、重排、去重、阈值过滤、上下文构造
  rag/hybrid.py                  向量 + BM25 混合检索
  rag/chunker.py                 章节切分与父子分块
  rag/reranker.py                Reranker 模型加载
  llm/client.py                  LLM 调用、追问改写、流式文本解析
  llm/prompts.py                 严格知识库与起草提示词

可选扩展
  services/entity_service.py     人工结构化实体知识的渐进式对话能力；当前不参与主 RAG 路由

存储与基础设施层
  storage/vector_store.py        Chroma 物理分块、两阶段入库与检索快照
  storage/kb_manifest.py         SQLite 激活版本清单与知识库逻辑代际
  storage/embedding.py           Embedding 模型
  storage/cache.py               SQLite TTL/LRU 回答缓存
  core/state.py                  Streamlit session_state
  utils/loader.py                PDF/DOCX/TXT/MD 解析
  utils/cleaner.py               模型输出清洗与 Markdown 渲染
```

## 核心流程

### 问答流程

```text
用户输入
  ├─ 明确问候语
  │    └─ 静态回复，不调用模型和检索
  └─ 其他问题
       ├─ 判断是否需要历史改写
       ├─ 混合召回：向量 + BM25
       ├─ Reranker 重排、去重、阈值过滤
       ├─ 有可靠片段？
       │    ├─ 是：严格基于 context 流式生成并保存来源
       │    └─ 否：硬拒答，并可展示 G2 按钮
       └─ 用户主动点击 G2：通用模型回答，明确标注非知识库内容
```

### 文档入库流程

```text
上传文件
  ├─ 超过 20MB：拒绝
  ├─ 整批超过配置上限：拒绝
  └─ 其余整批文件：主线程分块暂存到磁盘后立即加入有界后台队列
       ↓
最多 2 路有限窗口并行解析 PDF/DOCX/TXT/MD
       ↓
解析完成一份即串行切分、向量化和写入，随后释放正文
       ↓
为本次上传生成随机 revision，并以 staging 状态分批写入 Chroma
       ↓
校验该 revision 的分块数量与 ID 集合
       ↓
SQLite manifest 原子切换 source -> active revision
       ↓
尽力清理旧 revision；整批结束后统一刷新 BM25 派生索引
```

Chroma 保存物理分块，`data/kb_manifest.sqlite3` 保存每个来源当前可见的完整版本。manifest 是唯一提交点和唯一知识库代际来源：未提交 staging、残缺重试数据和清理失败留下的旧 revision 都不会进入列表、计数、向量检索或 BM25。检索结果携带 manifest generation；缓存读取前后与写入前后都会复核该代际，防止更新窗口内返回或保存旧答案。

## 代码结构

```text
enterprise_rag/
├─ app.py                 Streamlit 应用入口
├─ config.py              模型、检索、分块、缓存和功能开关
├─ ui/                    聊天界面与侧边栏
├─ services/              对话、RAG、文档和缓存服务
├─ rag/                   分块、混合检索、重排和上下文构造
├─ storage/               Chroma、Embedding、SQLite 缓存
├─ llm/                   LLM 客户端、提示词和解析器
├─ agent/                 历史记忆辅助工具
├─ core/                  状态、常量和异常
└─ utils/                 文件加载、清洗和日志

tests/                    回归、流程、模型就绪和 UI 测试
data/                     所有运行数据的默认根目录
├─ kb_data/               Chroma 持久化知识库
├─ kb_manifest.sqlite3    来源到激活 revision 的原子清单
├─ upload_staging/        上传文件临时暂存目录
├─ backgrounds/           用户上传的界面背景
├─ entity_blocks.json     可选的结构化实体知识数据
├─ answer_cache.sqlite3   持久化回答缓存
└─ logs/                  结构化运行日志与事故复盘台账
```

## 配置项

| 配置项 | 默认值 | 作用 |
| --- | --- | --- |
| `OPENAI_BASE_URL` | DashScope 兼容地址 | LLM 服务地址 |
| `OPENAI_MODEL` | `qwen3.7-max` | LLM 模型名称 |
| `EMBEDDING_MODEL` | `BAAI/bge-small-zh-v1.5` | 向量模型 |
| `RERANKER_MODEL` | `BAAI/bge-reranker-v2-m3` | 重排模型 |
| `HF_HUB_OFFLINE` | `0` | `1` 时禁止在线下载 Hugging Face 模型 |
| `APP_PASSWORD` | 空 | 可选的本地应用访问口令 |
| `RAG_DATA_DIR` | `<项目根>/data` | 统一迁移缓存、背景、Chroma 与 manifest 的运行数据根目录 |
| `RAG_LOG_DIR` | `<RAG_DATA_DIR>/logs` | 运行日志目录；适合部署时挂载到独立持久化卷 |
| `LOG_LEVEL` | `INFO` | 日志最小等级，例如 `DEBUG`、`INFO`、`WARNING` |
| `LOG_MAX_BYTES` | `5242880` | 单个日志文件滚动前的最大字节数，默认 5MB |
| `LOG_BACKUP_COUNT` | `7` | 每类日志保留的滚动备份数量 |
| `CACHE_SALT` | 空 | 回答缓存键的可选 HMAC 盐值 |
| `INITIAL_RETRIEVAL_K` | `50` | 初始候选数量 |
| `FINAL_TOP_K` | `8` | 最终上下文片段数量 |
| `RERANK_SCORE_THRESHOLD` | `0.5` | 重排绝对阈值，低于该分数的片段直接过滤 |
| `PARENT_CHUNK_SIZE` / `CHILD_CHUNK_SIZE` | `2000` / `500` | 父块/子块大小 |
| `ANSWER_CACHE_TTL_SECONDS` | `86400` | 回答缓存有效期（秒） |
| `DRAFT_ENABLED` | `true` | 是否允许基于资料起草 |
| `FALLBACK_ENABLED` | `true` | 是否显示 G2 按钮 |
| `UPLOAD_TASK_WORKERS` | `1` | 后台入库任务 worker 数；默认保持 Chroma 单写入 |
| `UPLOAD_PARSE_WORKERS` | `2` | 单个上传任务内的最大并行解析数 |
| `UPLOAD_QUEUE_CAPACITY` | `4` | 执行中与等待中的上传任务总上限 |
| `UPLOAD_MAX_BATCH_MB` | `200` | 单次选择文件的合计大小上限（MB） |
| `UPLOAD_STAGING_TTL_SECONDS` | `86400` | 异常退出后遗留暂存目录的清理期限（秒） |
| `VECTOR_WRITE_BATCH_SIZE` | `512` | 每次向量化并写入 Chroma 的最大子块数 |
| `EMBEDDING_BATCH_SIZE` | `32` | Embedding 模型内部编码批次大小 |

## 数据与运维

- `data/kb_data/`：Chroma 向量库。删除会清空已入库文档。
- `data/kb_manifest.sqlite3`：知识库逻辑清单。它与 `kb_data/` 必须作为一个整体备份和恢复，不能只删除或恢复其中之一。
- 向量库不再支持通过 `RAG_KB_DIR` 单独迁移；如需更换存储位置，只设置 `RAG_DATA_DIR`，保证 Chroma、manifest、缓存和背景配置使用同一运行数据根目录。
- Windows 下 `RAG_DATA_DIR` 不要放在用户配置文件目录（如 `C:\Users\<用户名>\Documents`、`AppData`）或云同步目录：实测该类路径下 Chroma 无法持久化 HNSW 索引二进制文件（segment 目录为空），进程重启后索引丢失并报 `Error loading hnsw index`，向量数据不可恢复需重新入库。请将运行数据放在普通磁盘目录（如 `D:\...\data`）。
- `data/entity_blocks.json`：可选的结构化实体知识数据；设置 `RAG_DATA_DIR` 后会随运行数据目录迁移。
- 结构化实体服务当前是保留扩展，不参与 `ChatService.chat_stream()` 的主问答链路；事实问题仍统一执行 RAG-First。
- `data/answer_cache.sqlite3`：持久化回答缓存。删除只会清空缓存，不会删除知识库。
- `data/logs/app.log`：JSON Lines 格式的运行日志，记录 INFO 及以上事件。
- `data/logs/error.log`：JSON Lines 格式的错误日志，记录 ERROR 和 CRITICAL 事件；每一行均可单独解析。
- `data/logs/incident_history.jsonl`：已复盘的高价值事故台账，不会把每一次瞬时错误都登记为事故。
- 日志会自动脱敏已配置的 API Key 和 Bearer Token；业务日志只记录问题长度、结果数量等诊断字段，不记录完整用户提问或文档正文。排障时优先查看 `error.log`，不要把日志中的来源文件名、路径或堆栈直接外发。
- 主线程和后台上传线程的未捕获异常会自动写入 `error.log`；已捕获的业务异常应使用模块级 `logger.exception(...)` 或 `logger.error(...)` 记录，页面只展示面向用户的简短提示。
- 文档内容更新、删除或清空时，系统会递增知识库代数，使旧回答缓存失效。
- 后台上传线程只写任务快照，不直接操作 `st.session_state`。
- 浏览器上传内容会先由 Streamlit 主线程以 1MB 分块写入 `data/upload_staging/`，因此大文件提交阶段仍受本机磁盘速度影响；解析、向量化和入库在暂存完成后才进入后台。
- 上传任务默认最多保留 4 个执行中/等待任务；队列已满时会立即拒绝新任务，避免内存和磁盘无界增长。
- 同一任务最多预取并行解析 2 个文件；一份解析完成后即按上传顺序进入串行 Embedding 与 Chroma 写入并释放正文，避免整批解析文本同时驻留内存。
- 上传批次从暂存阶段起持有知识库变更闸门；其他会话的删除/清空操作会被拒绝，避免“清空成功后后台又写回”。
- 上传期间没有稳定的只读快照，向量检索与混合检索会快速返回“知识库正在更新”，不会读取中间状态。
- 每次来源更新先写随机 staging revision，校验完整后再通过 SQLite manifest 原子激活。激活后的旧物理分块即使清理失败也不可见。
- 只有本批实际新增或更新了文档时才刷新 BM25；多个重叠批次的刷新会合并到最后一个批次结束时执行。
- 检索会记录 manifest generation；生成前若 generation 已变化则要求重问，生成期间变化则允许显示已生成正文但禁止缓存。
- 回答缓存不再维护第二套 generation 或 dirty 哨兵；缓存键和竞态校验统一读取 manifest generation，避免两个数据库独立提交导致旧缓存复活。
- 正常完成或失败后都会清理对应暂存目录；进程异常退出遗留的目录会按 TTL 在后续提交时清理。
- `.env`、`kb_data/` 和回答缓存数据库不应提交到公共仓库。
- 侧栏的“文档数”按唯一文件统计，“知识块”按 Chroma 分块数量统计，两者不是同一指标。
- 上传任务区分“新增、跳过、部分失败、全部失败”，重复且未变更的文件不会计为新增。

### 离线备份与恢复

Chroma/HNSW 在本项目中没有可靠的在线热快照契约。创建或恢复备份前必须停止 Streamlit、FastAPI 以及其他可能写入 `RAG_DATA_DIR` 的脚本，然后显式确认停写：

```powershell
uv run python scripts/backup.py create --confirm-stopped
```

备份先写入 `backups/.incomplete-*`，完成 SQLite `quick_check`、文件尺寸和 SHA-256 清单后再原子发布为 `backups/kb_YYYYMMDD_HHMMSS/`。默认保留最近 7 份；`upload_staging/` 属于未提交工作，不进入备份。检查已有备份：

```powershell
uv run python scripts/backup.py verify backups\kb_YYYYMMDD_HHMMSS
```

恢复会先校验完整性，再把当前数据目录改名保留为 `data.pre-restore-<时间戳>`，不会直接删除旧数据：

```powershell
uv run python scripts/backup.py restore backups\kb_YYYYMMDD_HHMMSS `
  --confirm-stopped --confirm-replace
```

至少完成一次非生产目录恢复演练，不能只验证“备份命令返回成功”：

```powershell
uv run python scripts/backup.py restore backups\kb_YYYYMMDD_HHMMSS `
  --data-dir .verify_tmp\restore-drill-data `
  --confirm-stopped --confirm-replace
$env:RAG_DATA_DIR = "$PWD\.verify_tmp\restore-drill-data"
uv run python -c "from enterprise_rag.storage.vector_store import get_vector_store_health; print(get_vector_store_health())"
Remove-Item Env:RAG_DATA_DIR
```

Windows 任务计划只应安排在明确的停写窗口；不要为了“每日自动”而在应用运行时复制 HNSW 文件。

### API 自检

FastAPI 启动后会在后台执行一次不阻塞服务启动的轻量自检。`GET /api/diagnostics` 聚合以下状态，React 顶栏使用同一接口展示：

- Embedding/Reranker：区分已加载、本地可用和缺失，不为自检主动加载模型；
- 向量库：真实读取 Chroma/HNSW；
- manifest：比较激活来源数和声明分块数与当前可见 Chroma 计数；
- LLM：只检查本地 Key 与模型配置，`network_verified=false`，不会产生模型调用费用；
- 错误摘要：读取 `error.log` 尾部，返回最近 24 小时计数和最多 3 条二次脱敏摘要。

### 部署边界

- `.streamlit/config.toml` 默认绑定 `127.0.0.1`，只允许本机访问。
- 若改为局域网或公网地址，至少应配置 `APP_PASSWORD`，并在反向代理层启用 HTTPS。
- 当前系统是单一共享知识库，没有用户、角色、租户隔离或文档级访问控制，不应直接作为生产多用户系统部署。
- 上传闸门、Chroma 写锁、任务快照和 BM25 状态都是单进程内对象。必须保持单个应用进程、单个写实例；多个进程共享同一 `RAG_DATA_DIR` 不受这些锁保护。
- Streamlit 会话状态按浏览器标签页隔离，但模型和向量库资源是进程级共享资源；高并发场景应另行拆分 API 服务、队列和权限层。

## 测试与排障

### 运行测试

```powershell
uv run pytest tests -q

Set-Location frontend
npm run test
npm run build
npm run lint
```

### 日志排障

在 PowerShell 中查看最近的错误记录：

```powershell
Get-Content "$env:RAG_DATA_DIR\logs\error.log" -Tail 50
```

如果没有设置 `RAG_DATA_DIR`，默认目录是项目根目录下的 `data\logs`。日志文件采用按大小滚动策略，调整 `LOG_MAX_BYTES` 与 `LOG_BACKUP_COUNT` 后需重启应用。高价值事故需完成根因、影响和修复方案复盘后，再人工写入 `incident_history.jsonl`；不要把普通重试、用户输入校验失败或包含敏感内容的异常原文直接写入事故台账。

当前测试覆盖：

- 模块导入和启动烟测
- FastAPI SSE 跨线程投递、断连检测、唯一终态和失败历史隔离
- React SSE 的 UTF-8 任意分块、CRLF、错误终态、畸形数据和 401 会话清理
- 离线原子备份、SHA-256 篡改检测、保留策略和恢复回滚
- 聚合诊断、manifest/Chroma 一致性、错误摘要脱敏和 API 鉴权
- RAG-First 路由与严格拒答
- 历史改写边界
- 向量/BM25 检索、重排和来源元数据
- 全量后台上传、磁盘暂存、有界队列、并行解析、Chroma 串行写和批次索引刷新
- SQLite TTL/LRU 缓存
- 原生 `st.write_stream` 的正常、空流和异常流
- G2 成功提交、鉴权失败提示和失败后重试状态
- DashScope 专用 Key 与通用 OpenAI 兼容 Key 的选择规则
- 在线、离线缓存、本地模型目录和缺失模型的就绪检测

当前完整测试基线：`161 passed`。

其中包含真实临时 Chroma + SQLite manifest 的一致性测试：未提交 staging 不可见、完整/残缺 staging 重试、旧版本清理失败隔离、manifest 代际传播、上传期间向量/混合查询熔断、BM25 最终候选二次可见性过滤，以及缓存 L1/L2 命中和写入期间代际变化时 fail closed。

上传管线的隔离调度基准（4 个文件、合计约 `0.76MB`）中，主线程暂存并提交约 `0.007s`，后台模拟管线约 `0.135s`，结束后暂存目录已删除。该结果只验证队列、暂存、批处理和清理开销，不代表真实 PDF 解析、Embedding 或 Chroma 的大文件吞吐量。

### 常见问题

**回答提示“未找到”**

检查文档是否已完成解析入库，再尝试使用文档中的原词提问。调试模式下可查看检索结果和来源。

**上传后检索不到专有名词**

确认文件不是空文件或乱码；系统已启用 BM25 关键词补召回，但最终仍会经过重排阈值过滤。

**模型无法连接**

默认 DashScope 配置应检查 `.env` 中的 `DASHSCOPE_API_KEY`、`OPENAI_BASE_URL` 和 `OPENAI_MODEL`；其他 OpenAI 兼容服务检查 `OPENAI_API_KEY`。修改后必须重启 Streamlit。若界面提示“模型鉴权失败”，说明请求已经到达模型服务，但当前 Key 未通过认证。

**知识库显示为空，但提问提示“检索服务暂时不可用”**

先查看日志中是否包含 `Error loading hnsw index`。这表示 Chroma HNSW 索引损坏，不是正常的“无资料”状态。确认旧知识库数据可以丢弃后，停止服务，同时删除运行目录下的 `kb_data` 和 `kb_manifest.sqlite3`（包括可能存在的 `-wal`、`-shm`），再重启应用创建新知识库。不要删除同级 `backgrounds`、`background.json` 或 `answer_cache.sqlite3`。若该问题反复出现，优先检查 `RAG_DATA_DIR` 是否位于用户配置目录（见“数据与运维”），并参考 `data/logs/incident_history.jsonl` 中的事故复盘。

**通用办公失败后按钮消失**

当前版本已改为成功后才消费按钮。鉴权失败、空响应或生成中断时，状态条会显示“生成失败，可重试”，原严格拒答下方的“改用通用办公回答”按钮仍可再次点击。

**测试执行后不退出**

项目已在 `pyproject.toml` 中禁用 pytest 结果缓存插件，避免受限目录中的 `.pytest_cache` 写入阻塞退出。
