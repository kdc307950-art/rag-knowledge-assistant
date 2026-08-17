"""执行检索裁决、严格知识库生成、拒答、起草和显式通用回答。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterator

from ..rag.retriever import retrieve_context

from ..config import DRAFT_ENABLED, FALLBACK_ENABLED
from ..core.constants import MAX_GENERATION_QUERY_LENGTH
from ..core.exceptions import KnowledgeBaseBusyError
from ..llm.client import generate_answer_stream, needs_history_rewrite, rewrite_query
from ..llm.prompts import DRAFT_SYSTEM_PROMPT, SYSTEM_PROMPT_TEMPLATE
from ..llm.schema import AIResponse
from .cache_service import CacheService

logger = logging.getLogger(__name__)


def _general_failure_details(exc: Exception) -> tuple[str, str]:
    """把模型 SDK 异常转换为不泄露敏感信息的用户提示和审计代码。"""
    current: BaseException | None = exc
    seen: set[int] = set()
    status_code = None
    error_code = ""
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        status_code = status_code or getattr(current, "status_code", None)
        error_code = error_code or str(getattr(current, "code", "") or "")
        body = getattr(current, "body", None)
        if isinstance(body, dict):
            error_code = error_code or str(body.get("code", "") or "")
        current = current.__cause__ or current.__context__

    normalized_code = error_code.lower()
    if status_code in {401, 403} or normalized_code in {"invalid_api_key", "authentication_error"}:
        return "通用办公模型鉴权失败，请检查 DashScope API Key 配置后重试。", "authentication"
    if status_code == 429:
        return "通用办公模型当前请求过多或额度不足，请稍后重试。", "rate_limit"
    if status_code == 404 or "model" in normalized_code and "not" in normalized_code:
        return "通用办公模型配置不可用，请检查模型名称后重试。", "model_not_found"
    return "抱歉，通用办公回答暂时不可用，请稍后重试。", "generation_error"


@dataclass(frozen=True)
class RetrievalDecision:
    """封装一次检索的原始结果，作为“生成回答或严格拒答”的唯一决策依据。"""
    query: str
    retrieval_query: str
    context: str
    sources: list[str]
    raw_results: list[dict]
    history_summary: str
    kb_generation: int | None = None
    error: str | None = None
    busy: bool = False

    @property
    def has_results(self) -> bool:
        return bool(not self.error and self.context.strip() and self.raw_results)

    @property
    def max_score(self) -> float:
        return max((float(item.get("rerank_score", 0.0)) for item in self.raw_results), default=0.0)


class RagService:
    """以一次检索决策对象贯穿回答生成、缓存与来源元数据。"""
    def __init__(self):
        self.cache_service = CacheService()
        self._last_meta: dict = {}

    @staticmethod
    def _history_summary(messages: list, use_history: bool, history: str = "") -> str:
        if not use_history:
            return ""
        if messages:
            return "|".join(
                f"{message.get('role')}:{message.get('content', '')[:50]}"
                for message in messages[:-1][-4:]
                if message.get("role") in ("user", "assistant")
            )
        return history[:600]

    @staticmethod
    def _thought(decision: RetrievalDecision) -> str:
        if decision.busy:
            return "检索状态：知识库正在更新"
        if decision.error:
            return "检索状态：检索服务异常"
        if not decision.has_results:
            return "检索结果：未找到相关片段"
        source_names = [source.split(" | ")[0] for source in decision.sources[:3]]
        reference = ", ".join(source_names)
        return (
            f"问题理解：{decision.retrieval_query}\n\n"
            f"检索结果：召回 {len(decision.raw_results)} 个片段，选用 {len(decision.sources)} 个来源"
            + (f"\n\n参考文档：{reference}" if reference else "")
        )

    def retrieve_only(self, query: str, history: str = "", messages: list | None = None) -> RetrievalDecision:
        """Retrieve once and return the sole RAG decision object without generating."""
        # 只有确认是追问时才改写查询，防止新的独立问题继承上一轮语义。
        use_history = bool(history) and needs_history_rewrite(history, query)
        retrieval_query = query
        if use_history:
            retrieval_query = rewrite_query(history, query)
        history_summary = self._history_summary(messages or [], use_history, history)

        # 检索阶段与生成阶段解耦：这里无论问题类型都只负责查库和记录分数。
        try:
            retrieval_result = retrieve_context(
                retrieval_query,
                return_raw=True,
                return_generation=True,
            )
            # 兼容仍返回三元组的测试替身和旧扩展实现；生产检索器返回第四项代际快照。
            if len(retrieval_result) == 4:
                context, sources, raw_results, kb_generation = retrieval_result
            else:
                context, sources, raw_results = retrieval_result
                generation_reader = getattr(self.cache_service, "get_generation", None)
                kb_generation = generation_reader() if callable(generation_reader) else None
        except KnowledgeBaseBusyError as exc:
            # 不记录原始问题，避免日志落盘用户完整提问。
            logger.info("知识库更新期间暂停检索: query_length=%d", len(query))
            return RetrievalDecision(
                query=query,
                retrieval_query=retrieval_query,
                context="",
                sources=[],
                raw_results=[],
                history_summary=history_summary,
                error=str(exc),
                busy=True,
            )
        except Exception as exc:
            logger.exception("Knowledge-base retrieval failed")
            return RetrievalDecision(
                query=query,
                retrieval_query=retrieval_query,
                context="",
                sources=[],
                raw_results=[],
                history_summary=history_summary,
                error=str(exc),
            )

        decision = RetrievalDecision(
            query=query,
            retrieval_query=retrieval_query,
            context=context,
            sources=sources,
            raw_results=raw_results,
            history_summary=history_summary,
            kb_generation=kb_generation,
        )
        logger.info(
            "RAG retrieval completed: query_length=%d results=%d top_score=%.4f",
            len(query),
            len(raw_results),
            decision.max_score,
        )
        return decision

    @staticmethod
    def reject(query: str) -> str:
        return (
            f"抱歉，知识库中未找到与“{query}”直接相关的资料。\n\n"
            "请上传相关文档或换一种问法后重试。"
        )

    @staticmethod
    def _generation_messages(query: str) -> list[dict]:
        """仅传入本轮检索查询，避免完整会话历史再次污染生成阶段。"""
        return [{"role": "user", "content": query[:MAX_GENERATION_QUERY_LENGTH]}]

    def _stream_from_decision(
        self,
        decision: RetrievalDecision,
        *,
        template: str,
        mode: str,
    ) -> Iterator[str]:
        """基于已裁决的上下文流式生成，并维护缓存和可追溯元数据。"""
        if not decision.has_results:
            yield self.reject(decision.query)
            return

        # 检索结束后若知识库已切换代际，本次上下文不再是当前稳定快照。
        # 此时不读取旧代际缓存，也不继续用过期资料生成答案。
        if (
            decision.kb_generation is not None
            and self.cache_service.get_generation() != decision.kb_generation
        ):
            content = "知识库刚刚完成更新，请重新提交问题以使用最新资料。"
            self._last_meta = {
                "content": content,
                "sources": [],
                "thought": "检索状态：知识库版本已变化",
                "query": decision.query,
                "retrieval_query": decision.retrieval_query,
                "draft_allowed": False,
                "is_kb_stale": True,
            }
            yield content
            return

        # 缓存键包含改写后的查询和历史摘要，避免不同上下文误命中同一答案。
        cache_key = self.cache_service.make_key(
            decision.retrieval_query,
            decision.history_summary,
            original_query=decision.query,
            mode=mode,
            kb_generation=decision.kb_generation,
        )
        if decision.kb_generation is None:
            # 兼容不返回代际的测试替身与旧扩展实现；生产检索始终返回 manifest generation。
            cached = self.cache_service.get(cache_key)
        else:
            cached = self.cache_service.get(
                cache_key,
                expected_generation=decision.kb_generation,
            )
        if cached:
            # ``CacheService.get`` 已执行读前/读后校验；这里再做最后一层
            # 复核，兼容第三方缓存适配器，确保 yield 前不返回旧代际答案。
            if (
                decision.kb_generation is not None
                and self.cache_service.get_generation() != decision.kb_generation
            ):
                content = "知识库刚刚完成更新，请重新提交问题以使用最新资料。"
                self._last_meta = {
                    "content": content,
                    "sources": [],
                    "thought": "检索状态：知识库版本已变化",
                    "query": decision.query,
                    "retrieval_query": decision.retrieval_query,
                    "draft_allowed": False,
                    "is_kb_stale": True,
                }
                yield content
                return
            self._last_meta = {
                "content": cached["content"],
                "sources": cached.get("sources", decision.sources),
                "thought": self._thought(decision),
                "query": decision.query,
                "retrieval_query": decision.retrieval_query,
                "draft_allowed": mode == "rag" and DRAFT_ENABLED,
                "from_cache": True,
            }
            yield cached["content"]
            return

        full_response = ""
        try:
            # 系统提示词只注入本次检索到的资料，保证回答不能脱离知识库扩写。
            system_prompt = template.format(context=decision.context)
            for chunk in generate_answer_stream(
                system_prompt,
                self._generation_messages(decision.retrieval_query),
            ):
                if not isinstance(chunk, str) or not chunk:
                    continue
                if not full_response and not chunk.strip():
                    continue
                full_response += chunk
                yield chunk
        except Exception:
            logger.exception("严格知识库回答生成失败")
            # 已流出的内容无法从浏览器撤回。保留同一份中断内容到会话元数据，
            # 避免首屏、聊天历史和缓存审计出现三份不同的回答。
            interruption = "\n\n---\n\n⚠️ 回答生成中断，请稍后重试。"
            had_output = bool(full_response.strip())
            full_response = (full_response + interruption) if had_output else (
                "抱歉，生成回答时出现错误，请稍后重试。"
            )
            self._last_meta = {
                "content": full_response,
                "sources": [],
                "thought": self._thought(decision),
                "query": decision.query,
                "retrieval_query": decision.retrieval_query,
                "draft_allowed": False,
                "is_interrupted": had_output,
            }
            # 只有尚未输出任何文本时才直接向流发送错误全文；已有部分输出时，
            # 只补充中断标记，避免把前缀重复显示一遍。
            if full_response == "抱歉，生成回答时出现错误，请稍后重试。":
                yield full_response
            else:
                yield interruption
            return

        # 实时流、聊天历史和缓存保存同一份正文。提示词已要求只输出 Markdown，
        # 展示层使用安全 Markdown 渲染，因此这里不在流结束后静默改写已显示文本。
        if not full_response.strip():
            # 空流不应伪装成有来源的正常回答，也不写入答案缓存。
            full_response = "抱歉，未能生成有效回答，请重新提问。"
            self._last_meta = {
                "content": full_response,
                "sources": [],
                "thought": self._thought(decision),
                "query": decision.query,
                "retrieval_query": decision.retrieval_query,
                "draft_allowed": False,
                "is_empty": True,
            }
            yield full_response
            return

        # 模型生成可能持续数秒；期间若上传完成并递增代际，旧上下文回答不得写入新缓存。
        generation_unchanged = (
            decision.kb_generation is None
            or self.cache_service.get_generation() == decision.kb_generation
        )
        if generation_unchanged:
            try:
                if decision.kb_generation is None:
                    cache_written = self.cache_service.set(
                        cache_key,
                        {"content": full_response, "sources": decision.sources},
                    )
                else:
                    cache_written = self.cache_service.set(
                        cache_key,
                        {"content": full_response, "sources": decision.sources},
                        expected_generation=decision.kb_generation,
                    )
                generation_unchanged = cache_written is not False
            except Exception:
                # 缓存属于性能优化，写入失败不能覆盖已生成的正文。
                logger.exception("答案缓存写入失败，继续返回已生成内容")
        else:
            logger.info(
                "生成期间知识库代际变化，跳过答案缓存: query_length=%d",
                len(decision.query),
            )
        self._last_meta = {
            "content": full_response,
            "sources": decision.sources,
            "thought": self._thought(decision),
            "query": decision.query,
            "retrieval_query": decision.retrieval_query,
            "draft_allowed": mode == "rag" and DRAFT_ENABLED,
            "from_cache": False,
            "cache_skipped_generation_changed": not generation_unchanged,
        }

    def answer_stream(self, query: str, history: str, messages: list) -> Iterator[str]:
        """先检索后裁决：有资料才生成，无资料或检索异常则确定性返回。"""
        decision = self.retrieve_only(query, history, messages)
        if decision.busy:
            content = "知识库正在更新，请稍后重试。"
            self._last_meta = {
                "content": content,
                "sources": [],
                "thought": self._thought(decision),
                "is_kb_busy": True,
                "fallback_allowed": False,
                "draft_allowed": False,
            }
            yield content
            return
        if decision.error:
            content = "抱歉，知识库检索服务暂时不可用，请稍后重试。"
            self._last_meta = {"content": content, "sources": [], "thought": self._thought(decision)}
            yield content
            return
        if not decision.has_results:
            # 严格知识库模式下，没有可靠片段时不调用通用模型。
            content = self.reject(query)
            self._last_meta = {
                "content": content,
                "sources": [],
                "thought": self._thought(decision),
                "query": query,
                "retrieval_query": decision.retrieval_query,
                "fallback_allowed": FALLBACK_ENABLED,
                "draft_allowed": False,
                # 供 UI 区分严格拒答与通用模型回答；两者都不能伪装成有引用的 RAG 答案。
                "is_reject": True,
            }
            yield content
            return
        yield from self._stream_from_decision(decision, template=SYSTEM_PROMPT_TEMPLATE, mode="rag")

    def answer(self, query: str, history: str, messages: list) -> AIResponse:
        content = "".join(self.answer_stream(query, history, messages))
        meta = self._last_meta or {}
        return AIResponse(
            content=meta.get("content") or content,
            sources=meta.get("sources", []),
            thought=meta.get("thought"),
        )

    def draft_stream(self, query: str, retrieval_query: str) -> Iterator[str]:
        """重新检索原查询，并仅使用当前仍有效的资料生成文稿。"""
        if not DRAFT_ENABLED:
            content = "基于资料起草功能当前未启用。"
            self._last_meta = {
                "content": content,
                "sources": [],
                "thought": None,
                "draft_allowed": False,
            }
            yield content
            return
        # 起草前重新检索，避免使用已被删除或更新的旧上下文。
        decision = self.retrieve_only(retrieval_query)
        decision = RetrievalDecision(
            query=query,
            retrieval_query=decision.retrieval_query,
            context=decision.context,
            sources=decision.sources,
            raw_results=decision.raw_results,
            history_summary="",
            kb_generation=decision.kb_generation,
            error=decision.error,
            busy=decision.busy,
        )
        if decision.busy:
            content = "知识库正在更新，请稍后再尝试基于资料起草。"
            self._last_meta = {
                "content": content,
                "sources": [],
                "thought": self._thought(decision),
                "draft_allowed": False,
                "is_kb_busy": True,
            }
            yield content
            return
        if decision.error or not decision.has_results:
            content = "抱歉，起草所需资料已不可用，请重新提问后再试。"
            self._last_meta = {"content": content, "sources": [], "thought": self._thought(decision)}
            yield content
            return
        yield from self._stream_from_decision(decision, template=DRAFT_SYSTEM_PROMPT, mode="draft")

    def general_stream(self, query: str) -> Iterator[str]:
        """执行用户显式触发的 G2 回答，不携带历史、来源或 RAG 缓存。"""
        # 此路径只能由用户显式触发；不使用历史、来源或 RAG 缓存。
        system_prompt = (
            "You are a helpful office assistant. Answer in the user's language. "
            "Your answer is not based on the enterprise knowledge base, so do not claim that it is. "
            "Do not present a model knowledge cutoff as the current date. For time-sensitive "
            "facts that cannot be verified, state the uncertainty rather than claiming they are current."
        )
        full_response = ""
        had_output = False
        try:
            for chunk in generate_answer_stream(system_prompt, self._generation_messages(query)):
                if not isinstance(chunk, str) or not chunk:
                    continue
                if not had_output and not chunk.strip():
                    continue
                full_response += chunk
                if not had_output:
                    had_output = True
                yield chunk
        except Exception as exc:
            logger.exception("General fallback generation failed")
            # 浏览器已经显示的部分内容不能撤回，因此异常后的元数据必须保存
            # 同一份“部分回答 + 中断标记”，避免实时界面和聊天历史内容不一致。
            interruption = "\n\n---\n\n⚠️ 通用回答生成中断，请稍后重试。"
            fallback, error_code = _general_failure_details(exc)
            interrupted_content = (
                full_response + interruption
                if had_output
                else fallback
            )
            self._last_meta = {
                "content": interrupted_content,
                "sources": [],
                "thought": "通用办公回答：不基于企业知识库",
                "is_general": True,
                "is_interrupted": True,
                "action_failed": True,
                "error_code": error_code,
            }
            # 已经流出正文时只追加中断标记；否则发送完整兜底文本。
            yield interruption if had_output else fallback
            return
        if not full_response.strip():
            full_response = "抱歉，通用办公回答未能生成有效内容，请稍后重试。"
            self._last_meta = {
                "content": full_response,
                "sources": [],
                "thought": "通用办公回答：不基于企业知识库",
                "is_general": True,
                "is_empty": True,
                "action_failed": True,
                "error_code": "empty_response",
            }
            yield full_response
            return
        self._last_meta = {
            "content": full_response,
            "sources": [],
            "thought": "通用办公回答：不基于企业知识库",
            "is_general": True,
        }
