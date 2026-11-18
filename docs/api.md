# 客服接口与 SSE 事件说明

统一约定：

- 需要身份的接口通过请求头 `X-User-Id`（数字用户 ID）标识用户；会话创建后与服务端绑定，其他用户访问返回 404。
- 响应包裹：`{"code": 0, "message": "ok", "data": ...}`；错误：`{"code": "<ERROR_CODE>", "message": "...", "data": null}`，HTTP 状态码 400/401/404/409/500/503。
- 文本与业务对象消息二选一：`text` 或 `object`；对象类型白名单 `order / series / cohort / refund_request`。

## POST /api/sessions

创建会话并绑定 `X-User-Id`；返回欢迎语（欢迎引导不占用流程栈）。

```bash
curl -X POST http://127.0.0.1:18082/api/sessions -H "X-User-Id: 1001"
```

```json
{ "sessionId": "…", "userId": "1001", "welcomeText": "你好，我是教育平台智能助理小度……", "requestId": "…" }
```

## POST /api/chat

非流式聊天。请求体：

```json
{
  "sessionId": "…",
  "messageId": "m-0001",
  "text": "帮我查下订单 ORD20240401005"
}
```

业务对象消息示例：

```json
{
  "sessionId": "…",
  "messageId": "m-0002",
  "object": { "type": "order", "id": "ORD20240401005", "title": "我的订单" }
}
```

响应：

```json
{
  "requestId": "…",
  "sessionId": "…",
  "messageId": "m-0001",
  "duplicated": false,
  "degraded": false,
  "track": "task",
  "messages": [{ "text": "订单 ORD20240401005（已支付）：…", "object": null }]
}
```

- `duplicated=true`：同一 `messageId` 重复提交，返回首次处理结果，不重复执行 Action。
- `degraded=true`：状态写库失败等降级，写类任务结果请以业务库为准并稍后核对。
- `track`：本轮轨道 `task / knowledge / chitchat / clarify / error`。

## POST /api/chat/stream（SSE）

同一消息处理逻辑的流式版本。事件序列：

```text
event: meta
data: {"requestId":"…","sessionId":"…","messageId":"m-0003","track":"task","degraded":false}

event: delta
data: {"text":"订单 ORD20240401005"}

event: delta
data: {"text":"（已支付）：……"}

event: done
data: {"requestId":"…","messageId":"m-0003","messageCount":1,"messages":[…]}
```

- 完整回复先在服务端生成并持久化（仅一次），再切片流式发送；`done` 携带 requestId 与全部消息。
- 处理失败：`event: error`，`data: {"error":"…"}`。
- 客户端断开不会回滚已完成的退款/工单动作（业务写操作已提交）。

## GET /api/sessions/{id}/history

会话历史（校验归属）：

```bash
curl http://127.0.0.1:18082/api/sessions/{sid}/history -H "X-User-Id: 1001"
```

## GET /api/sessions/{id}/state

当前任务状态（非敏感槽位）：

```json
{
  "sessionId": "…",
  "activeTask": { "taskId": "…", "flowId": "refund_request", "flowName": "退款申请", "stepId": "ask_refund_reason", "slots": { "order_no": "ORD20240401005" } },
  "pausedTasks": [ { "taskId": "…", "flowId": "ticket_submit", "flowName": "工单提交", "stepId": "check_refund", "slots": {}, "waitRefundLink": true } ],
  "focusedObject": null,
  "stateVersion": 7
}
```

服务重启后从 `dialogue_state` 恢复，多轮任务可继续。

## GET /api/sessions/{id}/traces

处理轨迹（验收第 4 条「提问 → 意图识别 → 查询/流程 → 回复 → 状态更新」闭环证据）：

```json
[
  {
    "requestId": "…", "turnNo": 3, "track": "task",
    "planSummary": "task: order_status_query,set_slots",
    "actions": [{ "action": "action_resolve_order" }],
    "status": "ok", "errorCode": "", "durationMs": 812, "createdAt": "2026-11-18 10:00:00"
  }
]
```

## 主要稳定错误码

| 错误码 | 场景 |
| --- | --- |
| MISSING_USER_ID / INVALID_USER_ID | X-User-Id 缺失/非数字 |
| SESSION_NOT_FOUND | 会话不存在或不属于当前用户（防越权） |
| ORDER_NOT_FOUND | 订单号不存在或不属于当前用户 |
| ORDER_ITEM_NOT_REFUNDABLE / REFUND_IN_PROGRESS / REFUND_AMOUNT_EXCEEDED | 明细不可退 / 已有处理中申请 / 金额超限（透传 edu-data） |
| MISSING_REFUND_REQUEST / REFUND_MISMATCH | 退款类工单缺退款申请 / 申请与明细不匹配 |
| EDU_API_UNAVAILABLE | edu-data 超时/网络错误（写操作不自动重试） |
| STATE_WRITE_FAILED | 状态写库失败（degraded=true） |
| FLOW_INFINITE_LOOP | 流程推进死循环保护 |
| PLAN_FAILED | LLM 规划失败（进入澄清） |
| INTERNAL_ERROR | 未处理异常（不泄露堆栈） |
