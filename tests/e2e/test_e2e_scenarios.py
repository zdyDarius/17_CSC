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
    return httpx.Client(base_url=BASE, timeout=120)


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
    """退款全流程：订单号 → 直接选类型（原因由原话/类型名生成）→ 确认 → 提交。

    兼容两种正确结果：提交成功（退款编号）；或业务库已有该明细的
    处理中退款申请（防重复退款，来自历史 E2E/演示操作）。
    """
    with _client() as client:
        session = _new_session(client)
        r1 = _chat(client, session, "我要退款")
        assert "订单号" in _bot_text(r1)
        _chat(client, session, DEMO_ORDER_NO)
        r3 = _chat(client, session, "时间安排冲突，无法继续学习")  # 直接作为类型输入
        text3 = _bot_text(r3)
        assert "退款金额" in text3 or "¥" in text3, text3[:200]
        assert "退款原因" in text3  # 原话进入确认页
        r5 = _chat(client, session, "确认")
        text5 = _bot_text(r5)
        success = "退款编号" in text5
        duplicate_guarded = "存在处理中的退款申请" in text5 or "已有退款申请" in text5
        assert success or duplicate_guarded, f"退款结果异常: {text5}"


# ============ 验收 5：工单提交全流程（真实落业务库） ============


def test_ticket_flow_end_to_end():
    """工单全流程：类型 → 订单 → 描述 → 确认 → 提交。

    兼容两种正确结果：提交成功（工单编号）；或 POST 结果不明时
    按确定性标题查询核对到已有工单（防重复建单）。
    """
    with _client() as client:
        session = _new_session(client)
        r1 = _chat(client, session, "第三章的视频一直加载不出来，我要提交售后工单")
        text = _bot_text(r1)
        # 循环推进直到出结果（最多 8 步）
        for _ in range(8):
            if "工单编号" in text or "疑似已提交的工单" in text:
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
        submitted = "工单编号" in text or "疑似已提交的工单" in text
        assert submitted, f"工单未提交成功: {text}"


# ============ 管控：知识插问不打断任务 + 流程切换/恢复 ============


def test_knowledge_interleave_keeps_task():
    with _client() as client:
        session = _new_session(client)
        _chat(client, session, "我要退款")
        _chat(client, session, DEMO_ORDER_NO)
        r = _chat(client, session, "对了，退款多久到账")
        assert r["track"] == "knowledge"
        # 任务未被打断：状态里活跃任务仍是退款
        state = client.get(
            f"/api/sessions/{session}/state", headers={"X-User-Id": USER}
        ).json()
        assert state["activeTask"]["flowId"] == "refund_request"


def test_switch_pause_and_resume():
    """切换 + 自动恢复（4.3）：新任务单轮完成 → 自动弹栈顶退款任务继续。"""
    with _client() as client:
        session = _new_session(client)
        r1 = _chat(client, session, "我要退款")  # 退款开始，等订单号
        assert "订单号" in _bot_text(r1)
        # 插入一个能立即完成的新任务 → 触发切换压栈
        r = _chat(client, session, "帮我查一下考研数学冲刺模考班的学习进度")
        assert r["track"] == "task"
        text = _bot_text(r)
        # 切换提示 + 新任务结果同轮出现
        assert any(k in text for k in ("已为你切换", "考勤", "视频", "进度")), text[:200]
        # 新任务完成 → 自动恢复栈顶退款任务（4.3）
        state = client.get(
            f"/api/sessions/{session}/state", headers={"X-User-Id": USER}
        ).json()
        assert state["activeTask"]["flowId"] == "refund_request"
        assert state["pausedTasks"] == []
        assert any("继续刚才" in m["text"] for m in r["messages"])
        # 恢复后的退款流程继续推进：给订单号 → 问原因
        r2 = _chat(client, session, DEMO_ORDER_NO)
        text2 = _bot_text(r2)
        assert "原因" in text2 or "订单" in text2, text2[:200]


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
        # 一致性：done 携带的完整回复 == 历史中持久化的同消息回复（只落库一次）
        done_payload = next(d for e, d in events if e == "done")
        sse_texts = [m.get("text") for m in done_payload.get("messages", [])]
        history = client.get(
            f"/api/sessions/{session}/history", headers={"X-User-Id": USER}
        ).json()["messages"]
        sse_user_idx = next(
            i for i, m in enumerate(history) if m.get("clientMessageId") == f"{mid}-sse"
        )
        persisted = [
            m.get("text")
            for m in history[sse_user_idx + 1 :]
            if m.get("role") == "assistant"
        ]
        assert sse_texts == persisted
        # 非流式重放同一 messageId：防重生效（duplicated=true），结果与首次一致
        # （同一 messageId 已被 SSE 处理过，重放返回持久化内容）
        replay = _chat(client, session, "那报名流程呢", message_id=f"{mid}-sse")
        assert replay["duplicated"] is True
        assert [m.get("text") for m in replay.get("messages", [])] == sse_texts


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


# ============ 退款/工单进度查询（回归：原误启动提交流程） ============


def test_refund_status_query():
    with _client() as client:
        session = _new_session(client)
        result = _chat(client, session, "退款到哪一步了")
        assert result["track"] == "task"
        text = _bot_text(result)
        assert "退款申请" in text or "RF" in text, text[:200]


def test_refund_status_query_by_no():
    """用户 1002 有一笔 pending 的 RF0000016325。"""
    with _client() as client:
        session = _new_session(client)
        result = _chat(client, session, "查一下 RF0000016325 的退款进度")
        assert result["track"] == "task"
        assert "RF0000016325" in _bot_text(result)


def test_ticket_status_query():
    with _client() as client:
        session = _new_session(client)
        result = _chat(client, session, "查询我的工单进度")
        assert result["track"] == "task"
        text = _bot_text(result)
        assert "工单" in text and ("TK" in text or "没有" in text), text[:200]


def test_ticket_query_regression_not_submit_flow():
    """回归用户踩到的 bug：“查询该工单进度”被误规划成提交工单要订单号。"""
    with _client() as client:
        session = _new_session(client)
        _chat(client, session, "取消RF0000016325 这个工单")  # 先制造插问语境
        result = _chat(client, session, "查询该工单进度")
        assert result["track"] == "task"
        text = _bot_text(result)
        assert "订单号" not in text, f"误启动提交流程: {text[:200]}"
        assert "工单" in text


def test_cancel_business_record_does_not_submit():
    """取消退款/工单属平台人工审批边界：不得误启动任何提交流程。"""
    with _client() as client:
        session = _new_session(client)
        result = _chat(client, session, "取消RF0000016325 这个工单")
        text = _bot_text(result)
        # 不应开始收集订单号/描述等提交动作
        assert "订单号（形如" not in text and "请选择工单类型" not in text, text[:200]
