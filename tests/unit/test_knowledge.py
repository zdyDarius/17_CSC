"""知识检索测试：FAQ 关键词与 BM25 RAG。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from edu_assistant.conf.config import get_settings
from edu_assistant.knowledge.provider import FaqProvider, RagProvider


@pytest.fixture(scope="module")
def faq() -> FaqProvider:
    return FaqProvider(get_settings().faq_path)


@pytest.fixture(scope="module")
def rag() -> RagProvider:
    return RagProvider(get_settings().docs_path)


def test_faq_matches_refund(faq):
    materials = asyncio.run(faq.fetch("退款政策是什么", None, "1001"))
    assert materials
    assert any("退款" in m.title or "退款" in m.content for m in materials)


def test_faq_matches_change_class(faq):
    materials = asyncio.run(faq.fetch("可以换班吗", None, "1001"))
    assert materials
    assert any("换班" in m.content for m in materials)


def test_faq_no_match(faq):
    materials = asyncio.run(faq.fetch("量子力学速成班", None, "1001"))
    assert materials == []


def test_rag_chunks_loaded(rag):
    assert rag.chunks, "文档切片应加载"
    joined = "\n".join(c.content for c in rag.chunks)
    assert "退款类型" in joined
    assert "换班" in joined


def test_rag_bm25_relevant(rag):
    materials = asyncio.run(rag.fetch("退款多久能到账", None, "1001"))
    assert materials
    assert any("退款" in m.content for m in materials[:2])


def test_rag_bm25_enroll(rag):
    materials = asyncio.run(rag.fetch("开班时间在哪里看", None, "1001"))
    assert materials


def test_rag_no_result_for_garbage(rag):
    materials = asyncio.run(rag.fetch("阿巴阿巴阿巴", None, "1001"))
    assert materials == []


def test_rag_score_ordering(rag):
    materials = asyncio.run(rag.fetch("退款金额怎么算", None, "1001"))
    scores = [m.meta.get("score", 0) for m in materials]
    assert scores == sorted(scores, reverse=True)
