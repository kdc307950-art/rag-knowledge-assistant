"""面向人工整理知识实体的结构化渐进式对话."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from ..config import RUNTIME_DATA_DIR
from ..core.state import get_state, set_state
from ..llm.client import generate_answer_stream
from ..llm.schema import AIResponse
from ..utils.cleaner import clean_llm_output


ENTITY_STORE_PATH = RUNTIME_DATA_DIR / "entity_blocks.json"
DETAIL_KEYWORDS = ("详细", "具体", "深入", "多说", "展开", "仔细", "再讲", "更多")
SPECIFIC_KEYWORDS = ("为什么", "怎么", "如何", "关系", "区别", "对比", "时间线", "原因", "影响")
LEVEL_PRIORITY = {"detail": 0, "relation": 1, "event": 2, "supplement": 3}
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KnowledgeBlock:
    block_id: str
    level: str
    tags: tuple[str, ...]
    content: str
    sensitivity: str | None = None
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class KnowledgeEntity:
    entity_id: str
    names: tuple[str, ...]
    entity_type: str
    auto_trigger: bool
    blocks: tuple[KnowledgeBlock, ...]


@dataclass(frozen=True)
class EntityPlan:
    entity: KnowledgeEntity
    intent: str
    selected_blocks: tuple[KnowledgeBlock, ...]
    remaining_blocks: tuple[KnowledgeBlock, ...]


def _normalize(text: str) -> str:
    return re.sub(r"[\s，。！？、,.!?~～：:；;（）()]", "", (text or "").lower())


def load_entities(path: Path | None = None) -> tuple[KnowledgeEntity, ...]:
    """加载并校验人工维护的轻量实体存储库"""
    store_path = path or ENTITY_STORE_PATH
    if not store_path.exists():
        return ()
    try:
        with store_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("实体知识块文件不可用：%s", exc)
        return ()

    entities: list[KnowledgeEntity] = []
    for raw_entity in payload.get("entities", []):
        entity_id = str(raw_entity.get("entity_id", "")).strip()
        names = tuple(str(name).strip() for name in raw_entity.get("names", []) if str(name).strip())
        raw_blocks = raw_entity.get("blocks", [])
        if not entity_id or not names or not isinstance(raw_blocks, list):
            continue

        seen_block_ids: set[str] = set()
        blocks: list[KnowledgeBlock] = []
        for raw_block in raw_blocks:
            block_id = str(raw_block.get("block_id", "")).strip()
            level = str(raw_block.get("level", "")).strip().lower()
            content = str(raw_block.get("content", "")).strip()
            if not block_id or block_id in seen_block_ids or not content:
                continue
            if level not in {"overview", "detail", "relation", "event", "supplement"}:
                continue
            seen_block_ids.add(block_id)
            blocks.append(
                KnowledgeBlock(
                    block_id=block_id,
                    level=level,
                    tags=tuple(str(tag).strip() for tag in raw_block.get("tags", []) if str(tag).strip()),
                    content=content,
                    sensitivity=raw_block.get("sensitivity") or None,
                    depends_on=tuple(raw_block.get("depends_on", [])),
                )
            )
        if blocks:
            entities.append(
                KnowledgeEntity(
                    entity_id=entity_id,
                    names=names,
                    entity_type=str(raw_entity.get("type", "实体")),
                    auto_trigger=bool(raw_entity.get("auto_trigger", False)),
                    blocks=tuple(blocks),
                )
            )
    return tuple(entities)


def find_entity(question: str, active_entity_id: str | None = None) -> KnowledgeEntity | None:
    """优先使用别名；仅明确跟进提问可复用当前激活实体."""
    normalized = _normalize(question)
    entities = tuple(entity for entity in load_entities() if entity.auto_trigger)
    matches: list[tuple[int, KnowledgeEntity]] = []
    for entity in entities:
        longest_alias = max((len(_normalize(name)) for name in entity.names if _normalize(name) in normalized), default=0)
        if longest_alias:
            matches.append((longest_alias, entity))
    if matches:
        return max(matches, key=lambda item: item[0])[1]

    if active_entity_id and (
        any(keyword in normalized for keyword in DETAIL_KEYWORDS)
        or (any(pronoun in normalized for pronoun in ("他", "她", "它")) and any(keyword in normalized for keyword in SPECIFIC_KEYWORDS))
    ):
        return next((entity for entity in entities if entity.entity_id == active_entity_id), None)
    return None


def classify_entity_intent(question: str, coverage: dict) -> str:
    normalized = _normalize(question)
    if any(keyword in normalized for keyword in SPECIFIC_KEYWORDS):
        return "specific"
    if coverage.get("presented_blocks") and any(keyword in normalized for keyword in DETAIL_KEYWORDS):
        return "detail"
    return "intro"


def _lexical_score(question: str, block: KnowledgeBlock) -> float:
    query_chars = set(_normalize(question))
    candidate_chars = set(_normalize(" ".join((*block.tags, block.content))))
    if not query_chars or not candidate_chars:
        return 0.0
    overlap = len(query_chars & candidate_chars) / len(query_chars)
    tag_bonus = sum(0.5 for tag in block.tags if _normalize(tag) in _normalize(question))
    return overlap + tag_bonus


def rank_blocks(question: str, blocks: tuple[KnowledgeBlock, ...], top_k: int = 2) -> tuple[KnowledgeBlock, ...]:
    """优先使用已配置的嵌入模型，不可用时降级采用词法排序算法."""
    if not blocks:
        return ()
    try:
        from ..storage.embedding import get_embedding_model

        vectors = get_embedding_model().encode(
            [question, *[block.content for block in blocks]], normalize_embeddings=True
        ).tolist()
        query_vector = vectors[0]
        scored = [
            (sum(left * right for left, right in zip(query_vector, vector)), block)
            for vector, block in zip(vectors[1:], blocks)
        ]
    except Exception:
        scored = [(_lexical_score(question, block), block) for block in blocks]
    scored.sort(key=lambda item: item[0], reverse=True)
    return tuple(block for _, block in scored[:top_k])


def select_blocks(entity: KnowledgeEntity, intent: str, presented_blocks: set[str], question: str) -> tuple[KnowledgeBlock, ...]:
    if intent == "intro":
        selected = [block for block in entity.blocks if block.level == "overview"]
        non_sensitive_detail = next(
            (
                block
                for block in entity.blocks
                if block.level == "detail" and block.sensitivity is None and block.block_id not in presented_blocks
            ),
            None,
        )
        if len(selected) < 2 and non_sensitive_detail:
            selected.append(non_sensitive_detail)
        return tuple(selected)

    if intent == "detail":
        candidates = [
            block
            for block in entity.blocks
            if block.block_id not in presented_blocks and block.level in LEVEL_PRIORITY
        ]
        candidates.sort(key=lambda block: LEVEL_PRIORITY[block.level])
        return tuple(candidates[:3])

    return rank_blocks(question, entity.blocks, top_k=2)


def build_followup(plan: EntityPlan) -> str:
    if not plan.remaining_blocks:
        return "我已经把这个实体最核心的内容讲完了。你想听哪部分？也可以挑一个角度我展开。😊"

    tags: list[str] = []
    for block in plan.remaining_blocks:
        for tag in block.tags:
            if tag not in tags:
                tags.append(tag)
            if len(tags) == 3:
                break
        if len(tags) == 3:
            break
    if not tags:
        return "还可以继续了解它的其他细节。你想听哪部分？也可以挑一个角度我展开。😊"
    prompts = [f"想了解「{tag}」吗？" for tag in tags]
    return "或者".join(prompts) + " 你想听哪部分？😊"


class EntityConversationService:
    """Plan entity conversations without changing the standard RAG path for other queries."""

    def plan(self, question: str) -> EntityPlan | None:
        coverage_map = get_state("entity_coverage", {}) or {}
        entity = find_entity(question, get_state("active_entity_id", None))
        if entity is None:
            return None

        coverage = coverage_map.get(entity.entity_id, {})
        presented = set(coverage.get("presented_blocks", []))
        intent = classify_entity_intent(question, coverage)
        selected = select_blocks(entity, intent, presented, question)
        selected_ids = {block.block_id for block in selected}
        remaining = tuple(
            block for block in entity.blocks if block.block_id not in presented | selected_ids
        )
        return EntityPlan(entity, intent, selected, remaining)

    @staticmethod
    def _fallback_response(plan: EntityPlan) -> str:
        if not plan.selected_blocks:
            return build_followup(plan)
        sections = []
        if any(block.sensitivity == "spoiler" for block in plan.selected_blocks):
            sections.append("⚠️ **剧透提醒**：以下内容包含重要情节或结论。")
        sections.extend(block.content for block in plan.selected_blocks)
        sections.append(build_followup(plan))
        return "\n\n".join(sections)

    @staticmethod
    def _system_prompt(plan: EntityPlan) -> str:
        context = "\n\n".join(block.content for block in plan.selected_blocks)
        spoiler_rule = (
            "先输出“⚠️ 剧透提醒”，再回答。" if any(block.sensitivity == "spoiler" for block in plan.selected_blocks) else ""
        )
        return f"""你是友好、专业的办公知识助手。请仅根据以下结构化知识块回答用户。
不要编造知识块以外的事实，不要列出 block_id 或字段名。将内容整合成清晰的 Markdown 段落，可适度使用 😊✨。
{spoiler_rule}

实体：{plan.entity.names[0]}（{plan.entity.entity_type}）
本轮意图：{plan.intent}
知识块：
{context}
"""

    def _commit_coverage(self, plan: EntityPlan) -> None:
        coverage_map = dict(get_state("entity_coverage", {}) or {})
        previous = dict(coverage_map.get(plan.entity.entity_id, {}))
        presented = list(previous.get("presented_blocks", []))
        for block in plan.selected_blocks:
            if block.block_id not in presented:
                presented.append(block.block_id)
        coverage_map[plan.entity.entity_id] = {
            "presented_blocks": presented,
            "last_intent": plan.intent,
        }
        set_state("entity_coverage", coverage_map)
        set_state("active_entity_id", plan.entity.entity_id)

    def answer_stream(self, question: str, plan: EntityPlan):
        if not plan.selected_blocks:
            content = self._fallback_response(plan)
            self._last_meta = {"content": content, "sources": [], "thought": "实体知识块已全部展示"}
            yield content
            return

        prefix = ""
        if any(block.sensitivity == "spoiler" for block in plan.selected_blocks):
            prefix = "⚠️ **剧透提醒**：以下内容包含重要情节或结论。\n\n"
            yield prefix

        generated = ""
        try:
            for chunk in generate_answer_stream(self._system_prompt(plan), [{"role": "user", "content": question}]):
                generated += chunk
                yield chunk
            generated = clean_llm_output(generated, force=True)
            if not generated.strip():
                raise ValueError("实体回答为空")
            content = prefix + generated + "\n\n" + build_followup(plan)
        except Exception:
            fallback_content = self._fallback_response(plan)
            if prefix:
                yield fallback_content[len(prefix):] if fallback_content.startswith(prefix) else fallback_content
            else:
                yield fallback_content
            content = fallback_content

        self._commit_coverage(plan)
        self._last_meta = {
            "content": content,
            "sources": [f"{plan.entity.names[0]} | {block.block_id}" for block in plan.selected_blocks],
            "thought": f"实体结构化回答：{plan.intent}，使用 {len(plan.selected_blocks)} 个知识块",
        }

    def answer(self, question: str, plan: EntityPlan) -> AIResponse:
        content = "".join(self.answer_stream(question, plan))
        meta = self._last_meta
        return AIResponse(content=meta.get("content") or content, sources=meta["sources"], thought=meta["thought"])
