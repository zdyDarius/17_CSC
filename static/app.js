/* 教育智能客服演示台 */
const state = {
  userId: () => document.getElementById("userSel").value,
  sessionId: null,
  pending: false,
  msgSeq: 0,
};

const $ = (id) => document.getElementById(id);
const messagesEl = $("messages");

const QUICK_SCENES = [
  ["查订单", "帮我查下订单 ORD0000037816"],
  ["查进度", "我在考研数学冲刺模考班的学习进度怎么样"],
  ["考试分数", "看一下我的考试分数"],
  ["课程咨询", "考研数学课程大概是什么情况"],
  ["退款政策", "退款政策是什么"],
  ["我要退款", "我要退款"],
  ["退款转工单", "退款进度太慢了，我要投诉退款处理"],
  ["提交工单", "第三章视频一直加载不出来，我要提交售后工单"],
  ["插问", "（任务进行中）对了，退款多久到账"],
  ["恢复任务", "继续刚才的任务"],
  ["取消任务", "取消当前任务"],
  ["闲聊", "今天好累啊"],
];

function addUser(text, tag) {
  const div = document.createElement("div");
  div.className = "msg user";
  div.innerHTML = `<div class="bubble"></div><div class="meta"></div>`;
  div.querySelector(".bubble").textContent = text;
  if (tag) div.querySelector(".meta").textContent = tag;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function addBot(text, meta, degraded) {
  const div = document.createElement("div");
  div.className = "msg bot" + (degraded ? " degraded" : "");
  div.innerHTML = `<div class="bubble"></div><div class="meta"></div>`;
  div.querySelector(".bubble").textContent = text;
  if (meta) div.querySelector(".meta").textContent = meta;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

function setConn(text) { $("connState").textContent = text; }

async function createSession() {
  const resp = await fetch("/api/sessions", {
    method: "POST",
    headers: { "X-User-Id": state.userId() },
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ message: resp.statusText }));
    setConn("创建会话失败: " + (err.message || ""));
    alert("创建会话失败：" + (err.message || "请检查 edu-data 是否启动"));
    return;
  }
  const data = await resp.json();
  state.sessionId = data.sessionId;
  setConn(`已连接 · 会话 ${data.sessionId.slice(0, 8)}… · 用户 ${state.userId()}`);
  messagesEl.innerHTML = "";
  addBot(data.welcomeText, "欢迎引导（不占用流程栈）");
  $("stateBox").innerHTML = `<div class="hint">会话已创建。发送消息开始演示。</div>`;
}

function newMessageId() {
  state.msgSeq += 1;
  return `m-${Date.now()}-${state.msgSeq}`;
}

async function sendText(text) {
  if (state.pending) return;
  if (!text || !text.trim()) return;
  if (!state.sessionId) { await createSession(); if (!state.sessionId) return; }
  state.pending = true;
  $("sendBtn").disabled = true;
  const messageId = newMessageId();
  addUser(text);
  try {
    if ($("sseMode").checked) {
      await sendStream(text, messageId);
    } else {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-User-Id": state.userId() },
        body: JSON.stringify({ sessionId: state.sessionId, messageId, text }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.message || "请求失败");
      const tags = [data.track, data.duplicated && "重复消息", data.degraded && "降级"]
        .filter(Boolean).join(" · ");
      (data.messages || []).forEach((m) => addBot(m.text, `${tags ? tags + " · " : ""}${data.requestId.slice(0, 8)}`, data.degraded));
      if (!data.messages || !data.messages.length) addBot("（无回复）");
    }
  } catch (e) {
    addBot("请求失败：" + e.message, null, true);
  } finally {
    state.pending = false;
    $("sendBtn").disabled = false;
  }
}

async function sendStream(text, messageId) {
  const resp = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-User-Id": state.userId() },
    body: JSON.stringify({ sessionId: state.sessionId, messageId, text }),
  });
  if (!resp.ok || !resp.body) {
    const err = await resp.json().catch(() => ({ message: "流式请求失败" }));
    throw new Error(err.message || "流式请求失败");
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let bubble = null;
  let requestId = "";
  let track = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index;
    while ((index = buffer.indexOf("\n\n")) >= 0) {
      const rawEvent = buffer.slice(0, index);
      buffer = buffer.slice(index + 2);
      let event = "message", data = "";
      for (const line of rawEvent.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (!data) continue;
      let payload;
      try { payload = JSON.parse(data); } catch { continue; }
      if (event === "meta") {
        requestId = payload.requestId;
        track = payload.track || "";
        bubble = addBot("", `${track} · 流式`);
        bubble._text = "";
      } else if (event === "delta" && bubble) {
        bubble._text = (bubble._text || "") + payload.text;
        bubble.querySelector(".bubble").textContent = bubble._text;
        messagesEl.scrollTop = messagesEl.scrollHeight;
      } else if (event === "done") {
        if (bubble) bubble.querySelector(".meta").textContent =
          `${track} · ${requestId.slice(0, 8)} · ${payload.messageCount} 条消息已持久化`;
      } else if (event === "error") {
        addBot("处理失败：" + (payload.error || ""), null, true);
      }
    }
  }
}

async function loadHistory() {
  if (!state.sessionId) return;
  const resp = await fetch(`/api/sessions/${state.sessionId}/history`, {
    headers: { "X-User-Id": state.userId() },
  });
  const data = await resp.json();
  if (!resp.ok) { alert("历史查询失败：" + data.message); return; }
  messagesEl.innerHTML = "";
  (data.messages || []).forEach((m) => {
    if (m.role === "user") addUser(m.text, `turn ${m.turnNo}`);
    else addBot(m.text || JSON.stringify(m.object), `turn ${m.turnNo} · 历史回溯`);
  });
}

async function loadState() {
  if (!state.sessionId) return;
  const resp = await fetch(`/api/sessions/${state.sessionId}/state`, {
    headers: { "X-User-Id": state.userId() },
  });
  const data = await resp.json();
  if (!resp.ok) { alert("状态查询失败：" + data.message); return; }
  renderState(data);
}

async function loadTraces() {
  if (!state.sessionId) return;
  const resp = await fetch(`/api/sessions/${state.sessionId}/traces`, {
    headers: { "X-User-Id": state.userId() },
  });
  const data = await resp.json();
  if (!resp.ok) { alert("轨迹查询失败：" + data.message); return; }
  const rows = (data || []).map((t) => ({
    turn: t.turnNo,
    track: t.track,
    plan: t.planSummary,
    status: t.status + (t.errorCode ? "/" + t.errorCode : ""),
    durationMs: t.durationMs,
  }));
  $("stateBox").innerHTML = `<h2>处理轨迹（最近 ${rows.length} 轮）</h2>` +
    `<pre>${JSON.stringify(rows, null, 2)}</pre>`;
}

function renderState(data) {
  const active = data.activeTask
    ? {
        flow: data.activeTask.flowId,
        name: data.activeTask.flowName,
        step: data.activeTask.stepId,
        slots: data.activeTask.slots,
      }
    : null;
  const paused = (data.pausedTasks || []).map((t) => ({
    flow: t.flowId,
    step: t.stepId,
    slots: t.slots,
    waitRefundLink: t.waitRefundLink,
  }));
  $("stateBox").innerHTML = `<h2>当前状态</h2>
    <div class="card"><b>活跃任务</b><pre>${JSON.stringify(active, null, 2)}</pre>
    <b>暂停栈</b><pre>${JSON.stringify(paused, null, 2)}</pre>
    <b>关注对象</b><pre>${JSON.stringify(data.focusedObject, null, 2)}</pre>
    <div class="hint">stateVersion=${data.stateVersion}</div></div>`;
}

// —— 初始化 ——
const quickEl = $("quick");
QUICK_SCENES.forEach(([label, text]) => {
  const btn = document.createElement("button");
  btn.className = "ghost";
  btn.textContent = label;
  btn.onclick = () => sendText(text);
  quickEl.appendChild(btn);
});

$("sendBtn").onclick = () => {
  const input = $("input");
  sendText(input.value);
  input.value = "";
};
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("sendBtn").click();
  }
});
$("newSessionBtn").onclick = createSession;
$("userSel").onchange = createSession;

createSession();
