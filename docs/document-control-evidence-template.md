# 文档控制证据包模板

本模板用于把文档设为 `authoritative` 之前的人工复核。它不是法律意见，也不能由文件名、修改时间或内容相似度替代。

审批功能当前提供隔离的模拟流程，用于验证状态机、双人审批、证据引用和候选配置生成；模拟审批不构成真实 HR/法务批准，也不会自动切换生产配置。

每个 `document_family` 建立一份证据包，保存到受访问控制的位置；`config/document_governance.json` 仅保存记录编号或受控路径，禁止把员工个人信息、口令或完整合同文本写入配置或日志。

## 基本信息

| 项目 | 填写 |
| --- | --- |
| document_family | |
| 当前候选权威来源文件名 | |
| 正式版本号 | |
| 生效日期（YYYY-MM-DD） | |
| 发布部门 | |
| 批准人 | |
| 审批编号 | |
| 公示/告知记录编号 | |
| 替代结论（replaces/does_not_replace/scope_distinct） | |
| 冲突时优先级及依据 | |
| 适用公司主体 | |
| 适用地区 | |
| 适用员工类型 | |

## 版本关系结论

1. 当前候选是否明确替代其他来源：是/否。
2. 如“是”，列出被替代来源、废止或替代依据、证据编号和生效衔接日期。
3. 如“否”，按确认程度分两种落法，不要混用：
   - **生效关系已确认、但并行有效无主从**（当前两份员工手册即属此类）：标记 `reference` + `active`，全局用 `all_active`，普通检索照常开启。`reference` 表示“都有效”，不表示“已排序”。
   - **生效关系无法确认**：保持 `unconfirmed` 与全局 `unresolved`，不要开启普通检索。
4. 两份内容冲突时，记录由谁确认的优先级、依据和日期。仅 `reference` 无法让系统自动裁决冲突——需要唯一口径就必须完成本证据包并升为 `authoritative`。

## 配置落地前复核

- [ ] 证据包由 HR、法务或制度负责人复核并签名/留档。
- [ ] 同一 `document_family` 仅一个 `authoritative` + `active` 来源。
- [ ] 被替代来源已设为 `superseded` + `archived`，并列入当前来源的 `supersedes`。
- [ ] `control.evidence_refs` 均可被审计人员定位。
- [ ] 将 `default_retrieval_policy` 改为 `authoritative` 前，已确认没有 `active/unconfirmed` 也没有 `active/reference` 条目——两者在该策略下都不可检索，`load_governance` 会直接报错而不是静默缩小知识库。
- [ ] 先运行 `uv run python scripts/sync_document_governance.py` 预览，复核无误后才执行 `--apply`。
- [ ] 再运行检索基线并保存结果，记录其中的 `document_governance_sha256`。

## 隔离模拟

使用真实来源文件名显式指定候选权威文档和被替代文档。命令只在指定目录生成 `application.json`、追加式 `approval_events.jsonl` 和 `document_governance.candidate.json`：

```powershell
uv run python scripts/simulate_document_governance.py `
  --workdir .verify_tmp/governance-simulation `
  --authoritative-source "候选权威文件.md" `
  --superseded-source "候选旧版本.md"
```

候选配置会经过与生产相同的 `load_governance` 校验，但不会写入 `config/document_governance.json`、Chroma 或 manifest。
