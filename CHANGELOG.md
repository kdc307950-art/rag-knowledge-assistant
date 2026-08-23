# 变更记录

本文件记录对使用、部署、接口、数据、安全或运维有影响的变更。提交历史仍是代码级事实源；本文件用于发布人员判断升级影响。

## Unreleased

### Documentation

- 建立文档中心、唯一事实源和文档版本规则。
- 将根 README 收缩为入口，并拆分本地开发、架构、配置、生产部署、升级回滚、运维、安全和验收文档。
- 统一默认 LLM 配置口径，以 `enterprise_rag/config.py` 为准。
- 将 Windows 反向代理明确为非生产参考，生产拓扑统一为 Linux Docker Compose + Nginx HTTPS。
- 补齐当前已有 HTTP/SSE 接口、权限和错误语义。
- 文档治理复核记录改写为现行 9 部法律语料的复核依据；员工手册时代的 `reference` 结论明确标记废止，`config/document_governance.json` 的 `notes` 同步更新，两处保持一致。

### Fixed

- Windows 安装显式包含 IANA `tzdata`，避免 DeepSeek 分时成本估算因无法加载 `Asia/Shanghai` 而返回 unknown。
- Pyright 只分析项目 Python 源码、运维脚本和测试，不再扫描虚拟环境、前端依赖和运行数据。

## 0.1.0 - 2026-08-20

### Baseline

- React + FastAPI 企业知识库助手。
- 严格知识库问答、来源追溯、显式通用回答和资料起草。
- 文档上传、原子版本激活、混合检索、ACL 和本地用户鉴权。
- 结构化日志、诊断、Prometheus 监控、企业微信告警和离线备份恢复。

该条目是现有能力的首个受控文档基线，不表示此前所有能力都在同一天实现。
