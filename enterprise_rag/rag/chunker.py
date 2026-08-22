# 文本分块：章节切分与父子分块（父块保留上下文，子块用于检索）
import re
from langchain_text_splitters import RecursiveCharacterTextSplitter
from enterprise_rag.config import PARENT_CHUNK_SIZE, PARENT_OVERLAP, CHILD_CHUNK_SIZE, CHILD_OVERLAP

def split_by_chapters(text: str):
    # 优先按章节保留业务结构；没有明显章节时再按全文递归切分。
    # 注意：法条内列举项「（一）物证；」以分号/句号结尾，不能误判为章节标题；
    # 真正的节标题（如「（一）一般规定」）不以标点结尾。
    pattern = r"""
    (
    ^第[一二三四五六七八九十\d]+[章节].*
    |
    ^[一二三四五六七八九十]+、.*
    |
    ^\d+\..*
    |
    ^（[一二三四五六七八九十]+）[^；。！？]*$
    )
    """
    lines = text.split("\n")
    chapters = []
    current_title = "前言"
    current_content = []

    for line in lines:
        if re.match(pattern, line.strip(), re.X):
            if current_content:
                chapters.append((current_title, "\n".join(current_content).strip()))
            current_title = line.strip()
            current_content = []
        else:
            current_content.append(line)

    if current_content:
        chapters.append((current_title, "\n".join(current_content).strip()))

    if len(chapters) == 1 and chapters[0][0] == "前言":
        return [("全文", text)]
    return chapters

child_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHILD_CHUNK_SIZE,
    chunk_overlap=CHILD_OVERLAP,
    separators=["\n\n", "\n", "。", "！", "？", "；", "，", "、", " ", ""],
    keep_separator=True
)

parent_splitter = RecursiveCharacterTextSplitter(
    chunk_size=PARENT_CHUNK_SIZE,
    chunk_overlap=PARENT_OVERLAP,
    separators=["\n\n", "\n", "。", "！", "？", "；", "，", "、", " ", ""],
    keep_separator=True
)

def create_parent_child_chunks(text: str, file_id: str = ""):
    # 父块保存完整语境，子块用于精细召回，二者通过 parent_id 关联。
    parents = parent_splitter.split_text(text)
    all_children = []
    parent_meta_list = []
    for idx, parent in enumerate(parents):
        children = child_splitter.split_text(parent)
        for child in children:
            all_children.append(child)
            parent_meta_list.append({
                "parent_id": f"{file_id}_parent_{idx}" if file_id else f"parent_{idx}",
                "parent_text": parent,
                "parent_index": idx
            })
    return all_children, parent_meta_list
