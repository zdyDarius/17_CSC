"""知识 Provider：FAQ / RAG（BM25）/ 业务 API。

- 每个 Provider 独立查询，返回资料条目列表；失败降级返回空列表并记日志。
- 独立 Provider 可并行（registry 层 asyncio.gather return_exceptions=True）。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import jieba

from edu_assistant.domain.message import MessageObject
from edu_assistant.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class KnowledgeMaterial:
    """一条检索资料。"""

    source: str  # faq.edu / rag.edu / api.series / api.cohort
    title: str
    content: str
    meta: dict = field(default_factory=dict)


class BaseProvider:
    name: str = "base"

    async def fetch(
        self, query: str, focused_object: MessageObject | None, user_id: str
    ) -> list[KnowledgeMaterial]:
        raise NotImplementedError


# ============ FAQ ============


class FaqProvider(BaseProvider):
    """faq.edu：FAQ JSON 关键词匹配。"""

    name = "faq.edu"

    def __init__(self, faq_path: Path) -> None:
        self.entries: list[dict] = []
        try:
            data = json.loads(faq_path.read_text(encoding="utf-8"))
            self.entries = data if isinstance(data, list) else []
        except FileNotFoundError:
            logger.warning("FAQ 文件不存在: %s", faq_path)
        except json.JSONDecodeError as exc:
            logger.error("FAQ JSON 解析失败: %s", exc)

    async def fetch(
        self, query: str, focused_object: MessageObject | None, user_id: str
    ) -> list[KnowledgeMaterial]:
        if not query:
            return []
        scored: list[tuple[int, KnowledgeMaterial]] = []
        for entry in self.entries:
            keywords = [kw for kw in (entry.get("keywords") or []) if kw and kw in query]
            if keywords:
                scored.append(
                    (
                        len(keywords),
                        KnowledgeMaterial(
                            source=self.name,
                            title=str(entry.get("question", "")),
                            content=str(entry.get("answer", "")),
                            meta={"id": entry.get("id", "")},
                        ),
                    )
                )
        # 关键词命中数降序（稳定排序，保序）
        scored.sort(key=lambda pair: -pair[0])
        return [material for _, material in scored[:3]]


# ============ RAG（Markdown 切片 + BM25） ============


def tokenize(text: str) -> list[str]:
    return [t.strip() for t in jieba.lcut(text) if t.strip()]


@dataclass
class DocChunk:
    doc_name: str
    section: str
    content: str
    tokens: list[str] = field(default_factory=list)


class RagProvider(BaseProvider):
    """rag.edu：Markdown 文档切片 + 中文分词 + BM25（MVP，无向量模型依赖）。"""

    name = "rag.edu"
    TOP_K = 3

    def __init__(self, docs_dir: Path) -> None:
        self.docs_dir = docs_dir
        self.chunks: list[DocChunk] = []
        self._load_docs()

    def _load_docs(self) -> None:
        if not self.docs_dir.is_dir():
            logger.warning("知识文档目录不存在: %s", self.docs_dir)
            return
        for path in sorted(self.docs_dir.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.error("读取文档失败 %s: %s", path, exc)
                continue
            self._split_doc(path.stem, text)

    def _split_doc(self, doc_name: str, text: str) -> None:
        """按二级/三级标题切片。"""
        current_section = "概述"
        buffer: list[str] = []

        def flush() -> None:
            content = "\n".join(buffer).strip()
            if content:
                self.chunks.append(
                    DocChunk(
                        doc_name=doc_name,
                        section=current_section,
                        content=content,
                        tokens=tokenize(content),
                    )
                )

        for line in text.splitlines():
            if line.startswith("## ") or line.startswith("### "):
                flush()
                buffer = []
                current_section = line.lstrip("# ").strip()
            buffer.append(line)
        flush()

    async def fetch(
        self, query: str, focused_object: MessageObject | None, user_id: str
    ) -> list[KnowledgeMaterial]:
        if not query or not self.chunks:
            return []
        return self._bm25_search(query)

    def _bm25_search(self, query: str) -> list[KnowledgeMaterial]:
        query_tokens = set(tokenize(query))
        if not query_tokens:
            return []
        n_docs = len(self.chunks)
        avg_len = sum(len(c.tokens) for c in self.chunks) / n_docs
        k1, b = 1.5, 0.75

        doc_freq: dict[str, int] = {}
        for chunk in self.chunks:
            for token in set(chunk.tokens):
                doc_freq[token] = doc_freq.get(token, 0) + 1

        scored: list[tuple[float, DocChunk]] = []
        for chunk in self.chunks:
            score = 0.0
            token_count: dict[str, int] = {}
            for token in chunk.tokens:
                token_count[token] = token_count.get(token, 0) + 1
            for token in query_tokens:
                tf = token_count.get(token, 0)
                if tf == 0:
                    continue
                idf = math.log(1 + (n_docs - doc_freq[token] + 0.5) / (doc_freq[token] + 0.5))
                score += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * len(chunk.tokens) / avg_len))
            if score > 0:
                scored.append((score, chunk))

        scored.sort(key=lambda x: -x[0])
        return [
            KnowledgeMaterial(
                source=self.name,
                title=f"{chunk.doc_name} · {chunk.section}",
                content=chunk.content,
                meta={"score": round(score, 3)},
            )
            for score, chunk in scored[: self.TOP_K]
        ]
