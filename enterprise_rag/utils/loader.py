"""解析 PDF、DOCX、TXT 与 Markdown，并保留页码或段落元数据。"""
import logging
import re
from pypdf import PdfReader
from docx import Document

from ..core.exceptions import DocumentException

logger = logging.getLogger(__name__)

def _read_result(text: str, segments: list[dict], return_metadata: bool):
    """按调用约定返回纯文本，或返回正文与来源分段的结构化结果。"""
    if return_metadata:
        return {"text": text.strip(), "segments": segments}
    return text.strip()


def read_file(file, show_error=True, return_metadata=False):
    """读取上传文件；失败时记录日志并返回 None，不支持的格式返回空串。"""
    # 统一产出正文和页码/段落元数据，后续检索可展示可追溯的来源位置。
    try:
        # UploadedFile/BytesIO 可能被上游读取过；统一回到起点，避免二次解析
        # 静默得到空内容。
        if hasattr(file, "seek"):
            file.seek(0)
        ext = file.name.split(".")[-1].lower()
        if ext in {"txt", "md"}:
            # 兼容 UTF-8 与常见中文编码，避免文本文件因编码不同而静默入库失败。
            data = file.read()
            for encoding in ("utf-8-sig", "utf-8", "gb18030"):
                try:
                    text = data.decode(encoding)
                    segments = [
                        {"paragraph": index, "text": paragraph.strip()}
                        for index, paragraph in enumerate(re.split(r"\n\s*\n", text), start=1)
                        if paragraph.strip()
                    ]
                    return _read_result(text, segments, return_metadata)
                except UnicodeDecodeError:
                    continue
            raise UnicodeDecodeError("text", data, 0, len(data), "unsupported encoding")
        elif ext == "pdf":
            # 逐页保留元数据，方便在回答中标注 PDF 页码。
            reader = PdfReader(file)
            page_segments = []
            for index, page in enumerate(reader.pages, start=1):
                page_text = page.extract_text()
                if page_text:
                    page_segments.append({"page": index, "text": page_text.strip()})
            return _read_result(
                "\n".join(segment["text"] for segment in page_segments),
                page_segments,
                return_metadata,
            )
        elif ext == "docx":
            # 段落与表格行统一转为可检索文本，并记录段落序号。
            doc = Document(file)
            full_text = []
            segments = []
            # 段落文本
            for para in doc.paragraphs:
                if para.text:
                    full_text.append(para.text)
                    segments.append({"paragraph": len(segments) + 1, "text": para.text.strip()})
            # 表格内容：按行转文本，单元格用制表符分隔，保留表格结构
            for table in doc.tables:
                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    row_text = "\t".join(cells)
                    if row_text:
                        full_text.append(row_text)
                        segments.append({"paragraph": len(segments) + 1, "text": row_text})
            return _read_result("\n".join(full_text), segments, return_metadata)
        else:
            return ""
    except Exception as e:
        logger.error(f"文件解析失败 {file.name}: {e}", exc_info=True)
        if show_error:
            raise DocumentException(f"文件 `{file.name}` 解析失败，请检查格式。") from e
        return None
