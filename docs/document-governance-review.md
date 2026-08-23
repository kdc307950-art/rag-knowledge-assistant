# 文档治理复核记录

> 文档版本：`0.2`
> 适用应用版本：`0.1.0`
> 最近核对：`2026-08-23`

本记录说明 `config/document_governance.json` 中每条 `authority_level` / `retrieval_status` 背后的**人工依据**。配置本身是唯一事实源（见 [文档中心](README.md) 的「唯一事实源」表），本文只解释结论，不复制字段值；两处冲突时以配置为准并视为缺陷。

本次复核：2026-08-23（法律语料）
上一次复核：2026-08-18 / 08-19（员工手册语料，已随语料下线而废止，见文末「变更历史」）

## 复核对象

`corpus/` 现行 9 部法律法规，逐部登记在 `config/document_governance.json` 的 `documents` 中：

民法典合同编、民法典合同编通则司法解释、公司法、劳动合同法、刑法、刑事诉讼法、行政处罚法、行政复议法、刑诉法解释。

## 已核验事实（2026-08-23）

1. 9 部文档在 `config/document_governance.json` 中的 `control` 证据包**全部填满**：`issuing_department`、`approver`、`approval_reference`、`notice_reference`、`replacement_decision`、`conflict_priority`、`evidence_refs` 无一为空。
2. 9 部文档分属 **9 个互不相同的 `document_family`**，每个 family 内只有一个来源。
3. `evidence_refs` 均为官方发布渠道的公开 URL（中国人大网、最高人民法院官网、税务总局政策法规库等），审计人员可独立定位原文，不依赖本仓库。
4. 运行时知识库 `data/kb_manifest.sqlite3` 的 `source_metadata` 中，9 个 source 实际落地的 `authority_level` 均为 `authoritative`、`retrieval_status` 均为 `active`，与配置一致——治理字段确实生效，不是只写在配置里。
5. 9 部文档的 `effective_to` 均为 `null`、`supersedes` 均为空、`replacement_decision` 均为 `does_not_replace`：现行版本并行有效，本批语料内部不存在替代关系。

## 当前结论

- `authority_level`：全部 **`authoritative`**。
- `retrieval_status`：全部 **`active`**。
- `default_retrieval_policy`：保持 **`all_active`**。

### 为什么这批语料能定 `authoritative`，而上一批（员工手册）不能

对照 [文档治理证据模板](document-control-evidence-template.md) 的两条硬性复核项：

| 复核项 | 员工手册（2026-08 已下线） | 现行法律语料 |
| --- | --- | --- |
| 同一 `document_family` 仅一个 `authoritative` + `active` 来源 | ❌ 两份手册共用 `employee_handbook` family，互补并行、无主从 | ✅ 9 部各占一个 family，family 内唯一 |
| `control.evidence_refs` 可被审计人员定位 | ❌ 正文无发布日期、发布部门、批准人、公告编号，仅有负责人口头补充确认 | ✅ 发布机关 + 批准文号（主席令 / 法释号）+ 官方来源 URL |

差别不在于「法律比手册重要」，而在于**证据是否可追溯到本仓库之外**。上一批语料卡在 `reference` 是证据不足的正确结果，不是评级偏严。

### 为什么 `default_retrieval_policy` 不切到 `authoritative`

当前 9 部文档全是 `authoritative` + `active`，两种策略的检索结果**完全相同**，切换没有即期收益，但会带来一处耦合：`load_governance` 在 `default_retrieval_policy=authoritative` 时会拒绝加载任何 `active` + `reference` 条目（见 `tests/test_document_governance.py::test_authoritative_policy_rejects_active_reference_sources`）。一旦切过去，将来纳入律所私有语料（合同模板、办案 SOP 等天然只能是 `reference`）就会变成「加一份参考资料必须同时改全局策略」。

保持 `all_active` 是本次复核给出的判断，不是历史决策的延续；改动前请连同上述耦合一并评估。

## 仍未取得、因而仍受限的部分

1. **法律的修订与废止未做版本管理**。当前 9 部的 `effective_to` 全为空、`supersedes` 全为空，等于系统认定「这些版本永久有效」。法律实际会被修正、修订、废止（例如公司法 2023 修订替代 2018 修正），届时必须由人工把旧版本置为 `superseded` + `archived` 并写入新版本的 `supersedes`——系统不会自己发现法律变了，也没有到期提醒。
2. **条文冲突不做自动裁决**。`conflict_priority` 是给人看的说明文本，不参与检索排序。新法优于旧法、特别法优于一般法这类效力规则未在代码中实现。
3. **`evidence_refs` 的可用性未做定期巡检**。链接失效不会触发任何告警。

## 变更流程

新增或调整语料的治理字段时：

1. 按 [文档治理证据模板](document-control-evidence-template.md) 填写证据包；
2. `uv run python scripts/sync_document_governance.py` 预览，复核无误后 `--apply`；
3. 重建检索基线并记录 `document_governance_sha256`；
4. 同一提交内更新本记录与 `config/document_governance.json` 的 `notes`。

## 变更历史

- **2026-08-23**：语料由员工手册整体替换为 9 部法律法规（提交 `f4edb4d` → `4f6b566`），全部复核为 `authoritative`。**2026-08-18 / 08-19 针对两份员工手册的复核结论随语料下线一并废止**，不再适用于任何现行文档；其历史版本可在本文件的 Git 历史中查阅。
- **2026-08-19**：员工手册生效关系经负责人确认，`authority_level` 定为 `reference`，`default_retrieval_policy` 由 `unresolved` 改为 `all_active`。（已废止）
- **2026-08-18**：员工手册初次复核，生效关系未证实，配置保持 `unresolved`，普通问答安全拒答。（已废止）
