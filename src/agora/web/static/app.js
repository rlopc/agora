"use strict";

const state = {
  human: null,
  participants: new Map(),
  lastId: 0,
  replyTo: null,
  paused: false,
  unseen: 0,
};

const $ = (id) => document.getElementById(id);
const COLORS = { claude: "var(--claude)", codex: "var(--codex)" };

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).detail || detail; } catch { /* no JSON body */ }
    throw new Error(detail);
  }
  return response.json();
}

function notify(text, kind = "error") {
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.textContent = text;
  $("toasts").append(node);
  setTimeout(() => node.remove(), 6000);
}

function colorOf(id) {
  if (id === state.human) return "var(--user)";
  return COLORS[id] || "var(--other)";
}

function nameOf(id) {
  return state.participants.get(id)?.name || id;
}

/** Text with known mentions highlighted, without interpreting HTML. */
function body(text) {
  const fragment = document.createDocumentFragment();
  const pattern = /(^|[^\w@])@([a-z0-9][a-z0-9_-]*)/gi;
  let from = 0;
  for (const m of text.matchAll(pattern)) {
    const id = m[2].toLowerCase();
    if (!state.participants.has(id)) continue;
    const start = m.index + m[1].length;
    fragment.append(text.slice(from, start));
    const span = document.createElement("span");
    span.className = "mention";
    span.textContent = `@${m[2]}`;
    fragment.append(span);
    from = start + m[2].length + 1;
  }
  fragment.append(text.slice(from));
  return fragment;
}

function time(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** The button is offered only while the browser has not been asked yet. */
function renderAlerts() {
  $("alerts").hidden = !("Notification" in window) || Notification.permission !== "default";
}

async function enableAlerts() {
  const permission = await Notification.requestPermission();
  renderAlerts();
  if (permission === "denied") notify("Notifications are blocked; allow them in the browser's site settings", "info");
}

/** Browser notification for a mention of the user while the page is not in front. */
function alertMention(m) {
  if (m.author === state.human || !m.mentions.includes(state.human)) return;
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  if (document.visibilityState === "visible" && document.hasFocus()) return;
  const text = m.text.length > 200 ? `${m.text.slice(0, 200)}…` : m.text;
  const shown = new Notification(`${nameOf(m.author)} mentioned you`, { body: text, tag: `agora-m${m.id}` });
  shown.addEventListener("click", () => {
    window.focus();
    $(`m${m.id}`)?.scrollIntoView({ block: "center" });
    shown.close();
  });
}

function renderMessage(m, { highlight = false, alert = false } = {}) {
  if (m.id <= state.lastId) return;
  state.lastId = m.id;
  $("empty").hidden = true;

  const art = document.createElement("article");
  art.className = "message";
  art.id = `m${m.id}`;
  art.style.setProperty("--author-color", colorOf(m.author));
  if (m.author === state.human) art.classList.add("mine");
  if (m.mentions.includes(state.human)) art.classList.add("for-me");
  if (highlight) art.classList.add("highlighted");

  const meta = document.createElement("div");
  meta.className = "meta";
  const author = document.createElement("span");
  author.className = "author";
  author.textContent = nameOf(m.author);
  const details = document.createElement("span");
  details.textContent = `#${m.id} · ${time(m.created)}`;
  meta.append(author, details);
  if (m.reply_to) {
    const re = document.createElement("a");
    re.href = `#m${m.reply_to}`;
    re.className = "link";
    re.textContent = `re #${m.reply_to}`;
    meta.append(re);
  }
  if (m.hops > 0) {
    const hops = document.createElement("span");
    hops.className = "hops";
    hops.title = "Consecutive messages between agents since your last message";
    hops.textContent = `hop ${m.hops}`;
    meta.append(hops);
  }
  const reply = document.createElement("button");
  reply.type = "button";
  reply.className = "link";
  reply.textContent = "reply";
  reply.addEventListener("click", () => setReply(m));
  meta.append(reply);

  const text = document.createElement("div");
  text.className = "body";
  text.append(body(m.text));

  art.append(meta, text);
  const list = $("messages");
  const atBottom = isAtBottom();
  list.append(art);
  if (atBottom || m.author === state.human) {
    art.scrollIntoView({ block: "end" });
  } else {
    state.unseen += 1;
    renderNew();
  }
  if (alert) alertMention(m);
}

function isAtBottom() {
  const list = $("messages");
  return list.scrollHeight - list.scrollTop - list.clientHeight < 80;
}

/** Indicator of messages that arrived while reading further up. */
function renderNew() {
  const button = $("new-messages");
  button.hidden = state.unseen === 0;
  button.textContent = state.unseen === 1 ? "1 new message ↓" : `${state.unseen} new messages ↓`;
}

function scrollToBottom() {
  const list = $("messages");
  const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  list.scrollTo({ top: list.scrollHeight, behavior: smooth ? "smooth" : "auto" });
}

function setReply(m) {
  state.replyTo = m ? m.id : null;
  $("reply").hidden = !m;
  if (m) {
    $("reply-text").textContent = `Replying to #${m.id} from ${nameOf(m.author)}`;
    if (m.author !== state.human && !$("text").value.includes(`@${m.author}`)) {
      $("text").value = `@${m.author} ${$("text").value}`;
    }
    $("text").focus();
  }
}

function renderState(s) {
  state.paused = s.paused;
  const button = $("pause");
  button.setAttribute("aria-pressed", String(s.paused));
  button.textContent = s.paused ? "Resume wakes" : "Pause wakes";

  const pill = $("status");
  const agents = [...state.participants.values()].filter((p) => p.type === "agent");
  const withError = agents.filter((p) => s.participants[p.id]?.error);
  const waiting = agents.filter((p) => s.participants[p.id]?.pending > 0);
  pill.className = "status";
  if (s.paused) {
    pill.textContent = "Paused: nobody wakes up";
    pill.classList.add("paused");
  } else if (s.limit_reached) {
    pill.textContent = `Limit of ${s.max_hops} hops: waiting for your reply`;
    pill.classList.add("limit");
  } else if (withError.length) {
    pill.textContent = `Could not wake ${withError.map((p) => "@" + p.id).join(", ")}`;
    pill.classList.add("error");
  } else if (waiting.length) {
    pill.textContent = `Waiting for ${waiting.map((p) => "@" + p.id).join(", ")}`;
  } else {
    pill.textContent = "Up to date";
  }

  const list = $("participants");
  list.replaceChildren();
  for (const p of state.participants.values()) {
    const info = s.participants[p.id] || {};
    const li = document.createElement("li");
    li.className = "participant";
    if (info.error) li.classList.add("has-error");

    const name = document.createElement("span");
    name.className = "name";
    name.style.color = colorOf(p.id);
    name.textContent = p.id === state.human ? `${p.name} (you)` : `@${p.id}`;
    if (info.listening) {
      name.textContent += " · listening";
      li.title = "Watching the room: gets wakes instantly";
    }
    name.title = "Insert mention";
    name.addEventListener("click", () => insertMention(p.id));
    li.append(name);

    if (p.type === "agent" && info.pending > 0) {
      const counter = document.createElement("span");
      counter.className = "counter";
      counter.title = "Messages pending to be read";
      counter.textContent = info.pending;
      li.append(counter);
    }
    if (info.error) {
      const failure = document.createElement("span");
      failure.className = "failure";
      failure.title = info.error;
      failure.textContent = "wake failed";
      li.append(failure);
    }
    if (p.type === "agent" && info.pending > 0) {
      const wake = document.createElement("button");
      wake.type = "button";
      wake.className = "button mini";
      wake.textContent = info.error ? "retry" : "wake";
      wake.title = `Wake @${p.id} now about their pending messages`;
      wake.addEventListener("click", () => wakeNow(p.id));
      li.append(wake);
    }
    list.append(li);
  }
}

function insertMention(id) {
  const field = $("text");
  const prefix = field.value && !field.value.endsWith(" ") ? " " : "";
  field.value += `${prefix}@${id} `;
  field.focus();
}

async function wakeNow(id) {
  try {
    await api(`/api/wake/${encodeURIComponent(id)}`, { method: "POST" });
    notify(`Wake sent to @${id}`, "info");
  } catch (e) {
    notify(`Could not wake @${id}: ${e.message}`);
  }
}

async function send(event) {
  event.preventDefault();
  const field = $("text");
  const text = field.value.trim();
  if (!text) return;
  field.disabled = true;
  try {
    const r = await api("/api/messages", {
      method: "POST",
      body: JSON.stringify({ text, reply_to: state.replyTo }),
    });
    field.value = "";
    setReply(null);
    renderMessage(r.message);
    if (r.unknown.length) {
      notify(`Unknown mentions: ${r.unknown.map((u) => "@" + u).join(", ")}`, "info");
    }
  } catch (e) {
    notify(`Could not send: ${e.message}`);
  } finally {
    field.disabled = false;
    field.focus();
  }
}

async function catchUp() {
  const fresh = await api(`/api/messages?since=${state.lastId}&limit=1000`);
  fresh.forEach((m) => renderMessage(m, { alert: true }));
  renderState(await api("/api/state"));
}

function connect() {
  const source = new EventSource("/api/events");
  // On (re)connect, recover whatever was published while there was no subscription.
  source.addEventListener("open", () => catchUp().catch(() => {}));
  source.addEventListener("error", () => {
    const pill = $("status");
    pill.className = "status error";
    pill.textContent = "No connection to the room; retrying…";
  });
  source.addEventListener("message", (e) => renderMessage(JSON.parse(e.data), { highlight: true, alert: true }));
  source.addEventListener("state", (e) => renderState(JSON.parse(e.data)));
  source.addEventListener("wake", (e) => {
    const w = JSON.parse(e.data);
    if (!w.ok) notify(`Could not wake @${w.participant}: ${w.error}`);
  });
}

async function start() {
  const info = await api("/api/participants");
  state.human = info.human;
  info.participants.forEach((p) => state.participants.set(p.id, p));
  const history = await api("/api/history?limit=200");
  history.forEach((m) => renderMessage(m));
  renderState(await api("/api/state"));
  connect();
}

$("form").addEventListener("submit", send);
$("new-messages").addEventListener("click", scrollToBottom);
$("messages").addEventListener("scroll", () => {
  if (state.unseen && isAtBottom()) {
    state.unseen = 0;
    renderNew();
  }
});
const header = document.querySelector(".header");
new ResizeObserver(() => {
  document.documentElement.style.setProperty("--header-height", `${header.offsetHeight}px`);
}).observe(header);
$("text").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $("form").requestSubmit();
  }
});
$("reply-cancel").addEventListener("click", () => setReply(null));
$("alerts").addEventListener("click", () => enableAlerts().catch((e) => notify(`Could not enable notifications: ${e.message}`)));
renderAlerts();
$("pause").addEventListener("click", async () => {
  try {
    renderState(await api("/api/pause", { method: "POST", body: JSON.stringify({ paused: !state.paused }) }));
  } catch (e) {
    notify(`Could not change the pause: ${e.message}`);
  }
});

// "listening" expires without an event when the watch ends: the state is refreshed.
setInterval(() => api("/api/state").then(renderState).catch(() => {}), 15000);

start().catch((e) => {
  const pill = $("status");
  pill.className = "status error";
  pill.textContent = `Could not load the room: ${e.message}`;
});
