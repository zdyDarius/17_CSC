"""E2E 场景测试：对运行中的客服服务（默认 127.0.0.1:18082）执行官方验收场景。

前置：edu-data(8000) 与客服服务(18082) 已启动；edu-data 中存在演示用户 1001-1003。
运行：.venv/bin/python -m pytest tests/e2e -q
服务不可达时自动跳过（CI/无环境不误报）。
"""

from __future__ import annotations

import uuid

import httpx
import pytest

BASE = "http://127.0.0.1:18082"
USER = "1001"
# 演示用户 1001 的真实订单（已支付、单明细、无退款记录，可走完整退款流程）
DEMO_ORDER_NO = "ORD0000037816"


def _client() -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=60)


def _service_up() -> bool:
    try:
        return httpx.get(f"{BASE}/health", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(not _service_up(), reason="客服服务未启动（先运行 main.py）")


def _new_session(client: httpx.Client) -> str:
    resp = client.post("/api/sessions", headers={"X-User-Id": USER})
    assert resp.status_code == 200, resp.text
    return resp.json()["sessionId"]


def _chat(client: httpx.Client, session_id: str, text: str, message_id: str | None = None) -> dict:
    resp = client.post(
        "/api/chat",
        headers={"X-User-Id": USER},
        json={
            "sessionId": session_id,
            "messageId": message_id or f"e2e-{uuid.uuid4().hex[:12]}",
            "text": text,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _bot_text(result: dict) -> str:
    return "\n".join(m.get("text") or "" for m in result.get("messages", []))


# ============ 验收 1：课程咨询（知识轨道，实时业务数据） ============


def test_course_consult_knowledge_track():
    with _client() as client:
        session = _new_session(client)
        result = _chat(client, session, "Python 全栈课程大概是什么情况")
        assert result["track"] == "knowledge"
        text = _bot_text(result)
        assert "课程" in text or "班次" in text


# ============ 验收 2：订单查询（任务轨道 + 真实业务数据） ============


def test_order_query_with_order_no():
    with _client() as client:
        session = _new_session(client)
        result = _chat(client, session, f"帮我查下订单 {DEMO_ORDER_NO}")
        assert result["track"] == "task"
        text = _bot_text(result)
        assert DEMO_ORDER_NO in text


# ============ 验收 3：学习进度查询（多维度汇总） ============


def test_learning_progress_query():
    with _client() as client:
        session = _new_session(client)
        result = _chat(client, session, "我在 Python 全栈第5期的学习进度怎么样")
        assert result["track"] == "task"
        text = _bot_text(result)
        assert ("考勤" in text or "视频" in text) and "进度" in text or "班次" in text


# ============ 验收 4：退款申请全流程（真实落业务库） ============


def test_refund_flow_end_to_end():
    with _client() as client:
        session = _new_session(client)
        r1 = _chat(client, session, "我要退款")
        assert "订单号" in _bot_text(r1)
        r2 = _chat(client, session, DEMO_ORDER_NO)
        r3 = _chat(client, session, "没时间学了")
        r4 = _chat(client, session, "1")  # 个人原因
        text4 = _bot_text(r4)
        assert "退款金额" in text4 or "¥" in text4
        r5 = _chat(client, session, "确认")
        text5 = _bot_text(r5)
        assert "退款编号" in text5, f"退款未成功: {text5}"


# ============ 验收 5：工单提交全流程（真实落业务库） ============


def test_ticket_flow_end_to_end():
    with _client() as client:
        session = _new_session(client)
        r1 = _chat(client, session, "第三章的视频一直加载不出来，我要提交售后工单")
        text1 = _bot_text(r1)
        # 可能先问类型或订单号
        if "订单号" not in text1 and "类型" not in text1:
            pass
        # 循环推进直到提交成功（最多 8 步）
        text = text1
        for _ in range(8):
            if "工单编号" in text:
                break
            if "工单类型" in text or "售后咨询" in text:
                reply = "1"
            elif "订单号" in text:
                reply = DEMO_ORDER_NO
            elif "序号" in text and "报名明细" in text:
                reply = "1"
            elif "描述" in text:
                reply = "第三章视频加载不出来"
            elif "确认" in text:
                reply = "确认"
            else:
                reply = "确认"
            text = _bot_text(_chat(client, session, reply))
        assert "工单编号" in text, f"工单未提交成功: {text}"


# ============ 管控：知识插问不打断任务 + 流程切换/恢复 ============


def test_knowledge_interleave_keeps_task():
    with _client() as client:
        session = _new_session(client)
        _chat(client, session, "我要退款")
        _chat(client, session, "ORD20240401005")
        r = _chat(client, session, "对了，退款多久能到账")
        assert r["track"] == "knowledge"
        # 任务未被打断：状态里活跃任务仍是退款
        state = client.get(
            f"/api/sessions/{session}/state", headers={"X-User-Id": USER}
        ).json()
        assert state["activeTask"]["flowId"] == "refund_request"


def test_switch_pause_and_resume():
    with _client() as client:
        session = _new_session(client)
        _chat(client, session, "我要退款")
        r = _chat(client, session, f"帮我查下订单 {DEMO_ORDER_NO}")  # 切换
        assert r["track"] == "task"
        state = client.get(
            f"/api/sessions/{session}/state", headers={"X-User-Id": USER}
        ).json()
        assert state["activeTask"]["flowId"] == "order_status_query"
        assert any(t["flowId"] == "refund_request" for t in state["pausedTasks"])
        r2 = _chat(client, session, "继续刚才的退款")
        assert r2["track"] == "task"
        state2 = client.get(
            f"/api/sessions/{session}/state", headers={"X-User-Id": USER}
        ).json()
        assert state2["activeTask"]["flowId"] == "refund_request"


# ============ 防重：同一 messageId 重复提交返回首次结果 ============


def test_message_id_dedup():
    with _client() as client:
        session = _new_session(client)
        mid = f"e2e-dedup-{uuid.uuid4().hex[:12]}"
        first = _chat(client, session, "退款政策是什么", message_id=mid)
        second = _chat(client, session, "退款政策是什么", message_id=mid)
        assert second["duplicated"] is True
        assert _bot_text(first) == _bot_text(second)


# ============ SSE：meta/delta/done 与非流式一致性 ============


def test_sse_matches_non_stream():
    with _client() as client:
        session = _new_session(client)
        mid = f"e2e-sse-{uuid.uuid4().hex[:12]}"
        non_stream = _chat(client, session, "平台怎么查看作业成绩", message_id=f"{mid}-ns")
        events: list[tuple[str, dict]] = []
        with client.stream(
            "POST",
            "/api/chat/stream",
            headers={"X-User-Id": USER},
            json={"sessionId": session, "messageId": f"{mid}-sse", "text": "那报名流程呢"},
        ) as resp:
            assert resp.status_code == 200
            event = ""
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    import json

                    events.append((event, json.loads(line[5:])))
        names = [e for e, _ in events]
        assert "meta" in names and "delta" in names and "done" in names
        deltas = "".join(d.get("text", "") for e, d in events if e == "delta")
        assert len(deltas) > 0
        # SSE 最终完整回复与非流式同源（同一 service 处理链路）
        done_payload = next(d for e, d in events if e == "done")
        sse_texts = [m.get("text") for m in done_payload.get("messages", [])]
        ns_texts = [m.get("text") for m in non_stream.get("messages", [])]
        assert sse_texts == ns_texts


# ============ 会话持久化：历史与状态接口 + 越权防护 ============


def test_history_state_and_isolation():
    with _client() as client:
        session = _new_session(client)
        _chat(client, session, "退款政策是什么")
        history = client.get(
            f"/api/sessions/{session}/history", headers={"X-User-Id": USER}
        ).json()
        assert len(history["messages"]) >= 3  # 欢迎 + user + assistant
        # 其他用户访问该会话 → 404
        other = client.get(
            f"/api/sessions/{session}/history", headers={"X-User-Id": "1002"}
        )
        assert other.status_code == 404
        # trace 按 request_id 查询（取历史里的 requestId）
        chat_result = _chat(client, session, "平台使用指南")
        request_id = chat_result["requestId"]
        trace = client.get(
            f"/api/traces/{request_id}", headers={"X-User-Id": USER}
        )
        assert trace.status_code == 200
        assert trace.json()["requestId"] == request_id
        trace_other = client.get(
            f"/api/traces/{request_id}", headers={"X-User-Id": "1002"}
        )
        assert trace_other.status_code == 404
