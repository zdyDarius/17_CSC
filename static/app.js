/* 教育智能客服演示台：黑板对话 × 学籍卡档案 */
const state = {
  userId: () => document.getElementById("userSel").value,
  sessionId: null,
  pending: false,
  msgSeq: 0,
  context: null,
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

/* ============ 对话渲染 ============ */

function addMsg(role, text, meta, cls) {
  const div = document.createElement("div");
  div.className = "msg " + role + (cls ? " " + cls : "");
  const who = role === "user" ? "学员" : role === "system" ? "系统" : "小度";
  const avatar = role === "user" ? "学" : "✦";
  div.innerHTML = `<div class="avatar"></div>` +
    `<div class="body"><div class="who"></div><div class="bubble"></div>` +
    (meta ? `<div class="meta"></div>` : "") + `</div>`;
  div.querySelector(".avatar").textContent = avatar;
  div.querySelector(".who").textContent = who;
  div.querySelector(".bubble").textContent = text;
  if (meta) div.querySelector(".meta").textContent = meta;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

function addBot(text, meta, degraded) {
  // 降级/系统提示走警示样式；其余按 bot 呈现
  const isWarn = degraded || /⚠️/.test(text);
  const isSystem = /^(好的|已为你|继续刚才|你还有暂停|已取消)/.test(text.trim());
  const cls = isWarn ? "warn" : isSystem ? "system" : "";
  const div = addMsg(isSystem ? "system" : "bot", text, meta, cls);
  return div;
}

function setConn(text, ok) {
  const el = $("connState");
  el.textContent = text;
  const wrap = document.getElementById("connWrap");
  if (wrap) wrap.className = "conn" + (ok ? " ok" : "");
}

function showTyping(on) { $("typing").hidden = !on; }

/* ============ 会话 ============ */

async function createSession() {
  setConn("创建会话中…");
  try {
    const resp = await fetch("/api/sessions", {
      method: "POST",
      headers: { "X-User-Id": state.userId() },
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ message: resp.statusText }));
      setConn("创建失败：" + (err.message || ""));
      return;
    }
    const data = await resp.json();
    state.sessionId = data.sessionId;
    messagesEl.innerHTML = "";
    addBot(data.welcomeText, "欢迎引导 · 不占用流程栈", false);
    setConn(`已连接 · ${data.sessionId.slice(0, 8)}… · 学员 ${state.userId()}`, true);
    $("archiveBox").innerHTML = `<div class="sec-empty">发送消息后，任务状态与处理轨迹会显示在这里。</div>`;
    loadContext();
  } catch (e) {
    setConn("服务不可达：" + e.message);
  }
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
  addMsg("user", text);
  showTyping(true);
  try {
    if ($("sseMode") && $("sseMode").checked) {
      await sendStream(text, messageId);
    } else {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-User-Id": state.userId() },
        body: JSON.stringify({ sessionId: state.sessionId, messageId, text }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.message || "请求失败");
      renderResult(data, "非流式");
    }
  } catch (e) {
    addBot("请求失败：" + e.message, null, true);
  } finally {
    showTyping(false);
    state.pending = false;
    $("sendBtn").disabled = false;
  }
}

function renderResult(data, mode) {
  const tags = [
    data.track && `<span class="track">${data.track}</span>`,
    mode,
    data.duplicated && "重复消息（返回首次结果）",
    data.degraded && "降级",
  ].filter(Boolean).join(" · ");
  (data.messages || []).forEach((m, i) => {
    const meta = i === 0 ? `${tags} · ${data.requestId.slice(0, 8)}` : "";
    addBot(m.text || "（无文本回复）", meta, data.degraded);
  });
  if (!data.messages || !data.messages.length) addBot("（无回复）", null, true);
}

/* ============ SSE ============ */

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
  const chunk = (payload) => {
    if (!bubble) bubble = addBot("", `${track || "…"} · 流式`);
    bubble._text = (bubble._text || "") + (payload.text || "");
    bubble.querySelector(".bubble").textContent = bubble._text;
    messagesEl.scrollTop = messagesEl.scrollHeight;
  };
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
        requestId = payload.requestId || "";
        track = payload.track || "";
        bubble = addBot("", `${track} · 流式 · ${requestId.slice(0, 8)}`, payload.degraded);
        bubble._text = "";
      } else if (event === "delta") {
        chunk(payload);
      } else if (event === "done") {
        if (bubble) {
          bubble.querySelector(".meta").textContent =
            `${track} · ${requestId.slice(0, 8)} · ${payload.messageCount} 条消息已持久化`;
        }
      } else if (event === "error") {
        addBot("处理失败：" + (payload.error || ""), null, true);
      }
    }
  }
}

/* ============ 学籍卡 ============ */

const STAMP_CLASS = {
  "已支付": "g", "已完成": "g", "已通过": "g", "在读": "g",
  "待支付": "y", "待审批": "y", "待处理": "y", "处理中": "y",
  "已取消": "r", "已驳回": "r", "已退费": "r", "已退款": "b",
  "待支付/待处理": "y",
};

function stamp(status) {
  if (!status) return "";
  const cls = STAMP_CLASS[status] || "b";
  return `<span class="pill ${cls}">${status}</span>`;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

/* 迷你操作按钮图标（线性，currentColor） */
const ICON = {
  search: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>`,
  refund: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-15.6-6.4L3 13"/></svg>`,
  chart: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 14l4-4 3.5 3 5.5-6.5"/></svg>`,
  book: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>`,
};

async function loadContext() {
  const userId = state.userId();
  $("profileBox").innerHTML = `<div class="sec-empty">正在调取…</div>`;
  $("ordersBox").innerHTML = `<div class="sec-empty">正在调取…</div>`;
  $("cohortsBox").innerHTML = `<div class="sec-empty">正在调取…</div>`;
  $("refundsBox").innerHTML = `<div class="sec-empty">正在调取…</div>`;
  try {
    const resp = await fetch("/api/demo/context", { headers: { "X-User-Id": userId } });
    if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).message || "查询失败");
    const ctx = await resp.json();
    state.context = ctx;
    renderProfile(ctx.profile);
    renderOrders(ctx.orders || []);
    renderCohorts(ctx.cohorts || []);
    renderRefunds(ctx.refunds || []);
  } catch (e) {
    $("profileBox").innerHTML = `<div class="sec-empty">业务数据暂不可用（${esc(e.message)}），请确认 edu-data 已启动。</div>`;
    $("ordersBox").innerHTML = "";
    $("cohortsBox").innerHTML = "";
    $("refundsBox").innerHTML = "";
  }
}

function renderProfile(p) {
  const box = $("profileBox");
  if (!p) { box.innerHTML = `<div class="sec-empty">未取到学员档案。</div>`; $("profileHint").textContent = ""; return; }
  $("profileHint").textContent = `studentId ${p.studentId ?? "-"}`;
  const tags = [p.identity, p.goal, p.education].filter(Boolean)
    .map((t) => `<span>${esc(t)}</span>`).join("");
  box.innerHTML = `
    <div class="profile-card">
      <div class="top">
        <div class="face">学</div>
        <div>
          <div class="name">学员 ${esc(state.userId())}</div>
          <div class="sid">studentId ${esc(p.studentId ?? "-")}</div>
        </div>
      </div>
      <div class="tags">${tags || `<div class="sec-empty">档案资料待补充</div>`}</div>
    </div>`;
}

function renderOrders(orders) {
  const box = $("ordersBox");
  if (!orders.length) { box.innerHTML = `<div class="sec-empty">该学员暂无订单。</div>`; return; }
  box.innerHTML = orders.map((o, idx) => `
    <div class="order">
      <div class="head">
        ${stamp(o.status)}
        <span class="no">${esc(o.orderNo)}</span>
        <span class="amt">¥${esc(o.amount)}</span>
      </div>
      ${(o.items || []).map((it) => `
        <div class="items"><div class="item">
          <em>${esc(it.name)}</em><span>${esc(it.status)} · ¥${esc(it.amount)}</span>
        </div></div>`).join("")}
      <div class="acts">
        <button class="chip" onclick="sendText('帮我查下订单 ${esc(o.orderNo)}')">${ICON.search}查这单</button>
        <button class="chip amber" onclick="sendText('我要退款，订单号是 ${esc(o.orderNo)}')">${ICON.refund}要退款</button>
      </div>
    </div>`).join("");
}

function renderCohorts(cohorts) {
  const box = $("cohortsBox");
  if (!cohorts.length) { box.innerHTML = `<div class="sec-empty">该学员暂无在读班次。</div>`; return; }
  box.innerHTML = cohorts.map((c) => `
    <div class="cohort">
      <div class="head" style="display:flex;align-items:center;gap:8px">
        <span class="name" style="flex:1">${esc(c.cohortName)}</span>
        ${stamp(c.status)}
      </div>
      ${c.seriesName && c.seriesName !== c.cohortName ? `<div class="series">课程：${esc(c.seriesName)}</div>` : ""}
      ${c.progress && Object.keys(c.progress).length ? `
        <div class="prog">
          <div class="cell"><b>${esc(c.progress.attendance || "-")}</b><span>考勤</span></div>
          <div class="cell"><b>${esc(c.progress.video || "-")}</b><span>视频</span></div>
          <div class="cell"><b>${esc(c.progress.homework || "-")}</b><span>作业</span></div>
          <div class="cell"><b>${esc(c.progress.exam || "-")}</b><span>考试</span></div>
        </div>` : ""}
      <div class="acts">
        <button class="chip" onclick="sendText('我在${esc(c.cohortName)}的学习进度怎么样')">${ICON.chart}查进度</button>
        <button class="chip" onclick="sendText('${esc(c.seriesName || c.cohortName)}课程大概是什么情况')">${ICON.book}课程咨询</button>
      </div>
    </div>`).join("");
}

function renderRefunds(refunds) {
  const box = $("refundsBox");
  if (!refunds.length) { box.innerHTML = `<div class="sec-empty">暂无退款申请。</div>`; return; }
  box.innerHTML = refunds.map((r) => `
    <div class="order">
      <div class="head">
        ${stamp(r.status)}
        <span class="no">${esc(r.refundNo)}</span>
        <span class="amt">¥${esc(r.amount)}</span>
      </div>
      <div class="series" style="font-size:11px;color:var(--ink-soft);margin-top:4px">关联订单 ${esc(r.orderNo)}</div>
    </div>`).join("");
}

/* ============ 会话操作 ============ */

async function loadHistory() {
  if (!state.sessionId) return;
  const resp = await fetch(`/api/sessions/${state.sessionId}/history`, {
    headers: { "X-User-Id": state.userId() },
  });
  const data = await resp.json();
  if (!resp.ok) { addBot("历史查询失败：" + data.message, null, true); return; }
  messagesEl.innerHTML = "";
  (data.messages || []).forEach((m) => {
    if (m.role === "user") addMsg("user", m.text || JSON.stringify(m.object), `turn ${m.turnNo}`);
    else addBot(m.text || JSON.stringify(m.object), `turn ${m.turnNo} · 历史回溯`);
  });
}

function renderArchive(title, obj) {
  $("archiveBox").innerHTML = `<div style="font-size:12px;color:var(--ink);margin-bottom:6px">${esc(title)}</div>
    <pre>${esc(JSON.stringify(obj, null, 2))}</pre>`;
}

async function loadState() {
  if (!state.sessionId) return;
  const resp = await fetch(`/api/sessions/${state.sessionId}/state`, {
    headers: { "X-User-Id": state.userId() },
  });
  const data = await resp.json();
  if (!resp.ok) { addBot("状态查询失败：" + data.message, null, true); return; }
  renderArchive("当前状态（活跃任务 / 暂停栈 / 关注对象）", data);
}

async function loadTraces() {
  if (!state.sessionId) return;
  const resp = await fetch(`/api/sessions/${state.sessionId}/traces`, {
    headers: { "X-User-Id": state.userId() },
  });
  const data = await resp.json();
  if (!resp.ok) { addBot("轨迹查询失败：" + data.message, null, true); return; }
  const rows = (data || []).map((t) => ({
    turn: t.turnNo,
    track: t.track,
    plan: t.planSummary,
    status: t.status + (t.errorCode ? "/" + t.errorCode : ""),
    durationMs: t.durationMs,
  }));
  renderArchive(`处理轨迹（最近 ${rows.length} 轮，按 request_id 留痕）`, rows);
}

/* ============ 初始化 ============ */

const quickEl = $("quick");
QUICK_SCENES.forEach(([label, text]) => {
  const btn = document.createElement("button");
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
