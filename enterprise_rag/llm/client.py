"""兼容 OpenAI 的客户端，以及安全的查询改写与流式生成工具。"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Mapping

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from ..config import API_KEY, BASE_URL, LLM_MODEL, LLM_STREAM_USAGE_MODE
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
def _stream_usage_enabled() -> bool:
    mode = LLM_STREAM_USAGE_MODE
    if mode in {"on", "true", "1"}:
        return True
    if mode in {"off", "false", "0"}:
        return False
    return "dashscope.aliyuncs.com" in BASE_URL.lower()


def _call_llm(messages, stream=False, *, include_usage: bool | None = None):
    started = time.perf_counter()
    mode = "stream" if stream else "request"
    if include_usage is None:
        include_usage = stream and _stream_usage_enabled()
    try:
        request = {
            "model": LLM_MODEL,
            "messages": messages,
            "stream": stream,
        }
        if stream and include_usage:
            request["stream_options"] = {"include_usage": True}
        try:
            response = get_llm().chat.completions.create(**request)
        except Exception as exc:
            # Some OpenAI-compatible gateways reject the optional usage
            # parameter. At this point no stream iterator exists and no token
            # could have been emitted, so one parameter-only fallback is safe.
            if stream and include_usage and _is_stream_usage_option_error(exc):
                logger.warning("流式 usage 参数不受支持，降级为无 usage 流式请求")
                request.pop("stream_options", None)
                response = get_llm().chat.completions.create(**request)
            else:
                raise
        # Stream usage is recorded exactly once from its terminal chunk by
        # ``generate_answer_stream``. Some SDKs expose the same usage on the
        # stream object as well, which would otherwise double-count tokens.
        response_usage = None if stream else _extract_usage(response)
        _record_llm(mode, time.perf_counter() - started, response_usage)
        return response
    except Exception as exc:
        _record_llm(mode, time.perf_counter() - started)
        logger.error("LLM 调用失败: %s", exc, exc_info=True)
        # 保留原始 OpenAI 异常类型，供 tenacity 准确判断是否属于可恢复错误。
        raise


def _is_stream_usage_option_error(exc: BaseException) -> bool:
    """Return True only for a provider rejection of ``stream_options``."""
    status_code = getattr(exc, "status_code", None)
    if status_code != 400 and not isinstance(exc, TypeError):
        return False
    parts = [str(exc)]
    body = getattr(exc, "body", None)
    if body is not None:
        parts.append(str(body))
    message = " ".join(parts).lower()
    if "stream_options" in message or "include_usage" in message:
        return True
    return isinstance(exc, TypeError) and (
        "unexpected keyword" in message or "keyword argument" in message
    )


def _extract_usage(value) -> dict[str, int | float] | None:
    """Extract provider-reported prompt/completion counts without guessing totals."""
    if value is None:
        return None
    if isinstance(value, Mapping):
        usage = value.get("usage")
    else:
        usage = getattr(value, "usage", None)
    if usage is None:
        return None

    def read(*names):
        for name in names:
            candidate = usage.get(name) if isinstance(usage, Mapping) else getattr(usage, name, None)
            if candidate is None:
                continue
            try:
                number = float(candidate)
            except (TypeError, ValueError):
                continue
            if number >= 0 and number == number and number != float("inf"):
                return int(number) if number.is_integer() else number
        return None

    result = {}
    input_tokens = read("prompt_tokens", "input_tokens")
    output_tokens = read("completion_tokens", "output_tokens")
    if input_tokens is not None:
        result["input"] = input_tokens
    if output_tokens is not None:
        result["output"] = output_tokens
    return result or None


def _field(value, name: str, default=None):
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _record_llm_tokens(mode: str, usage: Mapping[str, int | float] | None) -> None:
    if not usage:
        return
    try:
        from backend.observability.metrics import mark_llm_tokens
    except Exception:
        logger.debug("记录 LLM token 指标失败", exc_info=True)
        return
    for token_type, amount in usage.items():
        try:
            mark_llm_tokens(token_type, amount, mode)
        except Exception:
            logger.debug("记录 LLM token 指标失败", exc_info=True)


def _record_llm(
    mode: str,
    duration: float,
    usage: Mapping[str, int | float] | None = None,
) -> None:
    """Keep the core package usable without importing the FastAPI app eagerly."""
    try:
        from backend.observability.metrics import mark_llm_call

        mark_llm_call(mode, duration)
    except Exception:
        logger.debug("记录 LLM 指标失败", exc_info=True)
    if usage:
        _record_llm_tokens(mode, usage)


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
    stream_started = False
    usage = None

    def consume(current_response):
        nonlocal stream_started, usage
        for chunk in current_response:
            # A chunk, even one without visible text, means the provider has
            # started the stream; retrying with different parameters then risks
            # duplicate generation.
            stream_started = True
            chunk_usage = _extract_usage(chunk)
            if chunk_usage:
                usage = chunk_usage
            choices = _field(chunk, "choices", None) or []
            if not choices:
                continue
            choice = choices[0]
            delta = _field(choice, "delta", None)
            delta_content = _field(delta, "content", None)
            if delta_content:
                yield delta_content

    try:
        response = _call_llm(messages, stream=True)
        try:
            yield from consume(response)
        except Exception as exc:
            # A lazy-compatible client may reject stream_options on iteration
            # rather than at create(). Retry once only before any chunk arrived.
            if stream_started or not _is_stream_usage_option_error(exc):
                raise
            close = getattr(response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    logger.debug("关闭不兼容模型流失败", exc_info=True)
            response = _call_llm(messages, stream=True, include_usage=False)
            yield from consume(response)
    except Exception as exc:
        logger.error("答案生成失败: %s", exc, exc_info=True)
        raise LLMException(f"答案生成失败: {exc}") from exc
    finally:
        _record_llm_tokens("stream", usage)
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logger.debug("关闭模型流失败", exc_info=True)
