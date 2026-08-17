"""兼容 OpenAI 的客户端，以及安全的查询改写与流式生成工具。"""

from __future__ import annotations

import logging
import re
import threading

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from ..config import API_KEY, BASE_URL, LLM_MODEL
from ..core.exceptions import LLMException
from .prompts import REWRITE_QUERY_TEMPLATE

logger = logging.getLogger(__name__)

_CONTEXTUAL_QUERY_PREFIX = re.compile(
    r"^(?:这|那|他|她|它|这些|那些|上述|上面|前面|刚才|之前|继续|然后|另外|再)"
    r"(?:个|些|位|件|条|项|份)?"
)
_SHORT_FOLLOW_UP = re.compile(
    r"^(?:详细|详细介绍|多说点|再说说|继续|怎么|如何|为什么|多少|哪些|哪个|是否|能否|可以吗|什么意思)"
    r"[？?！!。,.，、\s]*$"
)
_INDEPENDENT_QUESTION = re.compile(
    r"^(?:什么是|谁(?:是)?|哪[个些]|为什么|如何|怎样|多少|何时|哪里)"
)
_PRONOUNS = re.compile(r"(?:他|她|它|这|那|上述|上面|前面)")


def _is_retryable_llm_error(exc: BaseException) -> bool:
    """只重试临时网络或服务端错误；认证和参数错误必须立即反馈。"""
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    return False


_client = None
_client_lock = threading.Lock()


def get_llm():
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    return _client


@retry(
    retry=retry_if_exception(_is_retryable_llm_error),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
)
def _call_llm(messages, stream=False):
    try:
        return get_llm().chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            stream=stream,
        )
    except Exception as exc:
        logger.error("LLM 调用失败: %s", exc, exc_info=True)
        # 保留原始 OpenAI 异常类型，供 tenacity 准确判断是否属于可恢复错误。
        raise


def needs_history_rewrite(history: str, question: str) -> bool:
    """Return True only when the current question explicitly depends on history."""
    if not history or not question:
        return False
    normalized = question.strip()

    # 完整的实体或事实问题不继承上一轮主题，例如“云天明是谁”。
    if _INDEPENDENT_QUESTION.match(normalized) and not _PRONOUNS.search(normalized):
        return False

    return bool(
        _CONTEXTUAL_QUERY_PREFIX.match(normalized)
        or _SHORT_FOLLOW_UP.match(normalized)
    )


def rewrite_query(history: str, question: str) -> str:
    if not needs_history_rewrite(history, question):
        return question

    # 只有依赖指代的追问才调用模型改写；失败或异常时始终回退到用户原问。
    prompt = REWRITE_QUERY_TEMPLATE.format(history=history, question=question)
    try:
        response = _call_llm([{"role": "user", "content": prompt}])
        rewritten = (response.choices[0].message.content or "").strip()
        if not rewritten:
            return question
        # 过长的改写通常表示模型偏离原意，直接放弃以避免污染检索条件。
        if len(rewritten) > max(len(question) * 3, len(question) + 80):
            logger.warning("查询改写结果异常偏长，已回退原问题")
            return question
        logger.debug(
            "历史查询改写已应用: original_length=%d, rewritten_length=%d",
            len(question),
            len(rewritten),
        )
        return rewritten
    except Exception as exc:
        logger.error("查询改写失败，回退原问题: %s", exc, exc_info=True)
        return question


def generate_answer_stream(system_prompt: str, chat_history: list):
    # 服务层只消费文本增量；跳过工具调用或空 delta，保持统一的流式契约。
    messages = [{"role": "system", "content": system_prompt}] + chat_history
    response = None
    try:
        response = _call_llm(messages, stream=True)
        for chunk in response:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta_content = getattr(choices[0].delta, "content", None)
            if delta_content:
                yield delta_content
    except Exception as exc:
        logger.error("答案生成失败: %s", exc, exc_info=True)
        raise LLMException(f"答案生成失败: {exc}") from exc
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logger.debug("关闭模型流失败", exc_info=True)
