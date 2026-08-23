# 检索评测

> 文档版本：`0.2`
> 适用应用版本：`0.1.0`
> 最近核对：`2026-08-23`

`scripts/eval_retrieval.py` 是显式的离线回归命令。它直接调用 `retrieve_context()`，不做 query 改写，也不调用 LLM。不要把从 Chroma 恢复出来的文本提交进 Git：那份快照只是重建出来的来源，原始编码不可靠。

## 现行黄金集

| 文件 | 用途 |
| --- | --- |
| `eval/retrieval_cases_law.jsonl` | **当前基线**：54 条用例，覆盖 9 部法律法规（49 可答 + 5 拒答），由 `scripts/build_law_golden_set.py` 生成后人工核对 |
| `eval/retrieval_cases.template.jsonl` | 中性起草模板，全部 `review_status=unreviewed`，故意无法通过评测 |

用例集与语料是绑定的：语料整体更换时旧用例不会「退化」，而是直接失效——`expected_sources` 指向已不存在的 source，评测必然全挂。这种情况下删除旧用例集、重建新基线，不要试图修补。2026-08 由员工手册换成法律语料时即按此处理（旧用例集可在本文件的 Git 历史中查阅）。

## 用例格式

每行一个 JSON 对象。新用例使用 schema v2，以便对单一大体量来源做证据级评测：

```json
{"schema_version":2,"case_id":"law_001","query":"...","tags":["law","factual"],"expected_sources":["民法典合同编.md"],"expected_evidence":[{"source":"民法典合同编.md","parent_id":"..."}],"expected_refusal":false}
```

`tags` 是语料自定义的元数据。评测器接受稳定的 ASCII 标识符（如 `law`、`factual`、`proper_noun`、`sensitive`、`expected_refusal`，仅限字母、数字、`_` 和 `-`），原样保留在报告中，**不**把它们当作隐藏打分逻辑。带历史追问的用例可以保留 `history` 字段作为说明，但这个纯检索评测器按设计忽略它；query 改写必须单独评测。

拒答用例设 `expected_refusal: true` 且 `expected_sources` 留空。可答的 v2 用例列出一个或多个精确 source 名，以及人工复核过的 `expected_evidence` 锚点（`parent_id`、`chapter` 或 `paragraph`）。Source Recall 只是文档级的粗粒度信号；当知识库里存在体量很大的单一来源时，evidence Recall/MRR 才是主指标。整套用例必须逐条对照真实源文件人工复核，**合成或猜测出来的标签不构成有效基线**。v1 仍可读入以便迁移，但不产出 evidence 指标。

多份 active 文档包含同一条规定时，普通事实型用例只指定一个规范来源；只有明确的跨文档比对用例才要求全部命中，否则重复文档会扭曲 recall。

## 来源策略

schema v2 的 `source_policy` 可取 `authoritative_only`、`all_required` 或 `any_equivalent`。最后一种还需要 `source_groups`，每组命中其一即可。报告保留原有的 `recall_at_k` 字段，并增加 `authoritative_recall_at_k`、`multi_document_coverage_at_k` 和 `any_equivalent_hit_rate_at_k`。

现行法律语料中 `any_equivalent` 的用途：**法律本体与司法解释并存时，同一个问题存在多个同等权威的来源**。例如「违约金过分高于损失如何处理」既命中民法典第五百八十五条，也命中合同编通则司法解释第六十五条（后者含"超过损失 30%"的具体标准）；「辩护律师会见」既命中刑事诉讼法第三十九条，也命中刑诉法解释第五十六条。这两例最初只标注单一来源，被评测判为未命中，改为 `any_equivalent` 后才反映真实检索质量。这是对语料结构的如实描述，不是放宽判定。

装载新语料后生成中性起草模板：

```powershell
uv run python scripts/create_golden_set_template.py --output eval/retrieval_cases_<语料名>.jsonl --count 20
```

模板覆盖事实型、专有名词、跨文档、历史追问和拒答五类用例，但被刻意标记为 `review_status=unreviewed`。评测器会一直拒绝执行，直到每条用例都对照源文档及其元数据核对过、改成 `approved` 为止。

## 运行

```powershell
uv run python scripts/eval_retrieval.py --cases eval/retrieval_cases_law.jsonl --output eval/results/retrieval-<日期>.json --baseline eval/results/retrieval-previous.json
```

报告会记录用例文件哈希、检索与模型配置、`document_governance_sha256` 策略快照，以及一份 manifest 语料快照（每个 active source 的 generation、active revision、内容哈希和 chunk 数）。当上一份报告缺少该指纹、或其语料与本次不同时，`--baseline` 会以退出码 `3` 拒绝比较。报告同时给出 `evaluation_error_rate` 和 `busy_rate`：**评测错误非零时，再高的 Recall/MRR 也不是干净基线**。

人工标注前先查看真实锚点：

```powershell
uv run python scripts/inspect_kb_metadata.py --source "民法典合同编.md" --contains "格式条款" --limit 10
```

该命令只打印元数据和一小段摘录，不会创建或批准任何用例。

## 端到端问答评测

`scripts/eval_retrieval.py` 只评检索。`scripts/eval_end_to_end.py` 把同一份用例跑完整链路（query → 检索 → LLM 生成 → 判分），三个判分维度：法条编号命中（规则）、groundedness 内容支持（LLM-as-judge）、拒答正确性（规则）。生成与判分均 `temperature=0`。

```powershell
uv run python scripts/eval_end_to_end.py --cases eval/retrieval_cases_law.jsonl --output eval/results/e2e-<日期>.json
```

成本：可答用例每条 = 1 次生成 + 1 次判分；拒答用例每条 = 1 次生成。用 `--limit N` 可以只跑前 N 条试水。

两者不能互相替代：检索指标通过不代表回答可用。实测中 `split_by_chapters` 把法条列举项误判为章节标题，导致 80 个块在列举前截断——条款号仍在，检索评测全绿，只有端到端评测暴露出回答里「资料未列出具体种类」。落地结论见 [律所落地说明](../docs/law-firm-landing.md)。

## 反馈候选用例

`scripts/export_feedback_cases.py` 只导出管理员已采纳的反馈到 `eval/dev/`。输出被 Git 忽略，且包含解密后的问答原文，必须按敏感数据处理。每条记录标记 `manual_label_required=true`；复核人必须先核实来源、补上证据锚点，才能据此创建一条 approved 的检索用例。该脚本拒绝写入 `eval/holdout/`，后者始终只允许人工维护。

## Groundedness 复核

`scripts/eval_groundedness.py` 评测的是**已经生成好**的回答。每条 JSONL 用例包含 `answer`、该回答对外暴露的 `source_ids`，以及一条或多条人工复核过的 `claims`，判定值为 `supported`、`contradicted`、`unsupported` 或 `not_reviewed`。报告把来源 ID 完整性与人工支持性**分开呈现**：一个格式合法的 `[S1]` 并不证明该主张确实源自 S1。

```powershell
uv run python scripts/eval_groundedness.py --cases eval/groundedness_cases.jsonl --output eval/results/groundedness-<日期>.json
```

用例文件需自行创建，仓库不附带样例（内容依赖具体语料和已生成的回答）。
