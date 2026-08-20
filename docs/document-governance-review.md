# 员工手册治理复核记录

初次复核：2026-08-18
结论更新：2026-08-19（负责人确认发布主体与生效日期）

## 初次复核（2026-08-18）已核验事实

- `2025版新劳动合同法下的企业员工手册.md` 文件存在，正文未出现发布日期、发布部门、批准人、公告编号、生效日期或废止条款。
- `企业员工手册（2025版）.md` 文件存在，正文未出现发布日期、发布部门、批准人、公告编号、生效日期或废止条款。
- 两个文件名均包含“2025版”，文件名不能证明版本先后或替代关系。
- 两份正文都描述面向全体员工，因此 ACL 暂按 `classification=policy`、`visibility=all` 处理；未证实发布部门时使用中性 `department=general`。

当时的结论是生效关系 **未证实**，配置保持 `default_retrieval_policy=unresolved`，普通问答安全拒答。

## 当前结论（2026-08-19 起生效）

负责人补充确认了初次复核缺失的事实：

1. 两份手册均由 HR / 人力资源部发布；
2. 均自 2025-01-01 起正式生效；
3. 二者**互补并行、各有侧重，不存在主从替代关系**；
4. 手册 2 中的 `[公司名]`/`[地址]`/`[省]` 等占位符是有意保留的脱敏处理，不影响文档有效性。

据此：

- 生效关系：**已确认**。两份手册同时有效。
- `authority_level` 定为 `reference`：生效关系已确认、可正常检索，但因为同属 `employee_handbook` family 且无唯一权威，**不满足 `authoritative` 的条件**。
- `default_retrieval_policy` 由 `unresolved` 改为 `all_active`，普通知识库问答现可正常检索两份手册。
- 两份文档均**未**标记为 `superseded` / `archived`——没有替代关系就不得制造替代关系。

配置落点见 `config/document_governance.json`；该文件的 `notes` 字段保存同一结论，两处必须一致。

## 仍未取得、因而仍受限的部分

`reference` 不等于 `authoritative`。以下能力在拿到完整证据包之前不可用：

- 条款冲突时的**自动优先级裁决**。当前两份手册地位对等，系统不会替业务方选择口径。
- `default_retrieval_policy=authoritative` 收紧策略（会因为不存在 `authoritative` 来源而检索为空）。

升级为 `authoritative` 需要至少一份可追溯材料（制度正文签发页、审批单、全员公告或 HR/法务正式通知），并明确：

1. 发布部门与批准人；
2. 版本号和生效日期；
3. 是否废止另一份手册；
4. 冲突条款的优先级及适用员工范围。

拿到证据后，按 [document-control-evidence-template.md](document-control-evidence-template.md) 填写证据包，使用 `scripts/sync_document_governance.py --apply` 同步治理字段，再重建黄金集基线。
