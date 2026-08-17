# LLM 输出解析器：清洗输出、提取 Markdown、识别来源、安全截断
import re

# 需要从 LLM 输出中剔除的危险/冗余模式
_UNWANTED_PATTERNS = [
    re.compile(r"```(?:html|xml)\s*", re.I),
    re.compile(r"</?(script|style|html|body)[^>]*>", re.I),
    re.compile(r"\n{3,}"),
]

# 引用来源识别：形如「来源：xxx」或「参考：xxx」的片段
_SOURCE_PATTERN = re.compile(
    r"(?:来源|参考|引用|出处)[：:]\s*([^\n，。；;,]+)",
    re.I
)


def clean_raw_output(text: str) -> str:
    """清洗 LLM 原始输出：移除危险标签、压缩多余换行。"""
    if not text:
        return ""
    cleaned = text
    for pat in _UNWANTED_PATTERNS:
        cleaned = pat.sub("", cleaned)
    return cleaned.strip()


def extract_markdown_content(text: str) -> str:
    """提取并规范化 Markdown 正文内容，供界面渲染。"""
    if not text:
        return ""
    return clean_raw_output(text)


def parse_sources(text: str) -> list:
    """从 LLM 输出中识别引用的来源名称，返回去重后的列表。"""
    if not text:
        return []
    matches = _SOURCE_PATTERN.findall(text)
    seen = set()
    result = []
    for m in matches:
        name = m.strip()
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


def safe_truncate(text: str, max_len: int) -> str:
    """安全截断文本，避免截断到半个字符。"""
    if not text or len(text) <= max_len:
        return text
    return text[:max_len].rstrip()