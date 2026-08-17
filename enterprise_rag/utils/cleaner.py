# 输出清洗：清理 LLM 回答中的 HTML/脚本标签、多余换行
import re
from functools import lru_cache

MAX_CLEANED_CACHE_SIZE = 200

def escape_html(text: str) -> str:
    import html
    return html.escape(text)

def _clean_text(original: str) -> str:
    """对单条文本执行确定性清洗；不依赖任何外部状态。"""
    text = original
    text = re.sub(r"```(?:html|xml)\s*", "", text, flags=re.I)
    text = re.sub(r"</?(script|style|html|body)[^>]*>", "", text, flags=re.I)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

# 清洗缓存只优化重复 Markdown 渲染；lru_cache 自带线程安全与有界淘汰，
# 使用有界进程级缓存，避免把清洗结果绑定到 UI 会话。
@lru_cache(maxsize=MAX_CLEANED_CACHE_SIZE)
def _clean_cached(original: str) -> str:
    return _clean_text(original)

def clean_llm_output(text: str, force: bool = False) -> str:
    if not text:
        return ""

    original = text.strip()
    if force:
        # 强制重新清洗，绕过缓存读取；清洗为纯函数，结果与缓存值一致。
        return _clean_text(original)
    return _clean_cached(original)

def render_markdown(text: str) -> str:
    if not text:
        return ""
    return clean_llm_output(text)
