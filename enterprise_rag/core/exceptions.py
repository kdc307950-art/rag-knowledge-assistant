# 自定义异常体系：RAG/LLM/检索/文档处理异常基类
class RAGException(Exception):
    """RAG基础异常"""
    pass

class LLMException(RAGException):
    """LLM调用异常"""
    pass

class RetrievalException(RAGException):
    """检索异常"""
    pass


class KnowledgeBaseBusyError(RetrievalException):
    """知识库正在执行上传或破坏性变更，当前不能获取稳定检索快照。"""
    pass


class DocumentGovernanceError(RetrievalException):
    """文档生效关系未确认，普通问答必须 fail closed。"""

    code = "document_governance_unresolved"


class ACLUnavailableError(RetrievalException):
    """The caller identity required for ACL evaluation is missing or invalid."""

    code = "acl_unavailable"

class DocumentException(RAGException):
    """文档处理异常"""
    def __init__(self, message: str, *, storage_may_have_changed: bool = False):
        super().__init__(message)
        # 上层上传批次需要据此决定是否强制刷新 BM25 派生索引。
        self.storage_may_have_changed = storage_may_have_changed


class DocumentAuthorizationError(DocumentException):
    """The upload principal is not allowed to create or replace this source."""

    code = "document_authorization_failed"
