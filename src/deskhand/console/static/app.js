"use strict";
/* deskhand console. Plain DOM, no dependencies. Every string from the desktop, a model or a
   saved file is put on the page as text (textContent), never as markup. */

const TOKEN = document.querySelector('meta[name="deskhand-token"]').content;
const DESK = document.querySelector('meta[name="deskhand-desk"]').content;
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const VERBS = ["PRESS", "OPEN", "MENU", "TYPE", "SET", "DRAG", "KEY", "CHORD", "SCROLL", "WAIT", "DONE"];
const ON_TARGET = new Set(["PRESS", "OPEN", "MENU", "TYPE", "SET", "DRAG"]);
const VERB_NAMES = {
  PRESS: "按下", OPEN: "打开", MENU: "菜单", TYPE: "输入", SET: "设值", DRAG: "拖到",
  KEY: "按键", CHORD: "组合键", SCROLL: "滚动", WAIT: "等待", DONE: "结束",
};
const EXPECT_MODES = [
  ["selected", "处于选中"], ["value", "值等于"], ["present", "存在"], ["absent", "不存在"], ["none", "不声明"],
];

/* ------------------------------------------------------------------ helpers */

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

async function api(path, { method = "GET", body } = {}) {
  const init = { method, headers: { "X-Deskhand-Token": TOKEN } };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(path, init);
  if (res.status === 401) {
    // Each launch mints a new token: this tab was opened against an earlier one.
    staleToken();
    throw new Error("控制台已经重启，这个页面需要刷新");
  }
  const data = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
  return data;
}

function staleToken() {
  if ($("#stale")) return;
  document.body.prepend(h("div", { id: "stale", class: "stale" },
    "控制台已经重启，这个页面用的是上一次的凭据。",
    h("button", { class: "primary small", text: "刷新页面", onclick: () => location.reload() }),
  ));
}

let toastTimer;
function toast(text, bad = false) {
  const el = $("#toast");
  el.textContent = text;
  el.className = "toast show" + (bad ? " bad" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.className = "toast"), bad ? 6000 : 2600);
}

const store = {
  get(key, fallback) { try { const v = localStorage.getItem("deskhand:" + key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem("deskhand:" + key, JSON.stringify(value)); } catch { /* private window */ } },
};

/* A target with no verbs of its own is briefed with the desktop-wide ones (KEY, SCROLL...);
   those are not things it can do, so the console shows only what aims at it. */
function aimable(t) { return (t.actions || []).filter((v) => ON_TARGET.has(v)); }
function nameOf(t) { return t.label || (t.also_called || [])[0] || ""; }
function layerOf(t) { return t.visual ? "pixel" : nameOf(t) ? "named" : "unnamed"; }
function when(iso) { return iso ? iso.replace("T", " ").slice(5, 16) : ""; }

/* -------------------------------------------------------------------- state */

const state = {
  doctor: null, apps: [], app: store.get("app", ""), pixels: store.get("pixels", "auto"),
  view: null, shotBox: null, picked: null, filter: "",
  task: null, taskName: "", tasks: [], runs: [], run: null, runTimer: null, autoTimer: null,
};

/* --------------------------------------------------------------------- tabs */

function showTab() {
  const tab = (location.hash || "#view").slice(1);
  const known = ["view", "tasks", "runs", "setup"].includes(tab) ? tab : "view";
  for (const sec of $$(".tab")) sec.hidden = sec.id !== "tab-" + known;
  for (const a of $$(".tabs a")) a.classList.toggle("on", a.dataset.tab === known);
  if (known === "runs") loadRuns();
  if (known === "tasks") loadTasks();
  if (known === "setup") renderSetup();
}

/* ------------------------------------------------------------------- header */

function renderStatus() {
  const d = state.doctor;
  const box = $("#status");
  box.replaceChildren();
  box.append(h("span", { class: "chip " + (DESK === "demo" ? "warn" : "info"), text: DESK === "demo" ? "演示桌面" : "本机 macOS" }));
  if (!d) return;
  const p = d.permissions || {};
  box.append(
    h("span", { class: "chip " + (p.accessibility ? "ok" : "bad"), text: (p.accessibility ? "✓ " : "✕ ") + "辅助功能" }),
    h("span", { class: "chip " + (p.screen_recording ? "ok" : "warn"), text: (p.screen_recording ? "✓ " : "– ") + "屏幕录制" }),
    h("span", { class: "chip " + (d.model && d.model.configured ? "ok" : ""), text: d.model && d.model.configured ? "模型 " + d.model.program : "未配置模型" }),
  );
}

async function loadState() {
  try {
    const s = await api("/api/state");
    state.doctor = s.doctor;
    state.home = s.home;
    renderStatus();
  } catch (e) { toast("读取状态失败：" + e.message, true); }
}

/* ---------------------------------------------------------------- live view */

async function loadApps() {
  try { state.apps = await api("/api/apps"); } catch (e) { state.apps = []; toast(e.message, true); }
  for (const sel of [$("#app"), $("#t-app")]) {
    const keep = sel.value || state.app;
    sel.replaceChildren(h("option", { value: "", text: DESK === "demo" ? "演示应用" : "当前前台应用" }));
    const count = {};
    for (const a of state.apps) count[a.name] = (count[a.name] || 0) + 1;
    for (const a of state.apps) {
      // Two applications can share a name; then only the pid says which one is meant.
      const twin = count[a.name] > 1;
      sel.append(h("option", { value: twin ? "pid:" + a.pid : a.name, text: a.name + (twin ? ` #${a.pid}` : "") + (a.front ? "（前台）" : "") }));
    }
    sel.value = Array.from(sel.options).some((o) => o.value === keep) ? keep : "";
  }
}

async function readView() {
  const btn = $("#refresh");
  btn.disabled = true;
  $("#view-meta").textContent = "读取中…";
  const q = new URLSearchParams({ app: state.app, pixels: state.pixels });
  try {
    // The picture is taken of the window the view described, so they line up.
    const view = await api("/api/view?" + q);
    const shot = await loadShot(view);
    state.view = view;
    if (state.picked && !view.targets.some((t) => t.id === state.picked)) state.picked = null;
    renderStage(shot);
    renderTargets();
    renderDetail();
    const n = view.notes || {};
    const read = n.pixels_used ? `，像素识别 ${n.pixels} 处` : "";
    $("#view-meta").textContent = `${view.app} · ${view.window || "（无标题）"} · ${view.targets.length} 个目标${read} · ${view.ms} ms`;
  } catch (e) {
    $("#view-meta").textContent = "读取失败";
    toast(e.message, true);
  } finally { btn.disabled = false; }
}

async function loadShot(view) {
  if (DESK === "demo") return null;
  const q = new URLSearchParams({ app: state.app, window: view.window || "", frame: (view.frame || []).join(",") });
  const res = await fetch("/api/shot?" + q, { headers: { "X-Deskhand-Token": TOKEN } });
  if (res.status !== 200) return null;
  const box = JSON.parse(res.headers.get("X-Deskhand-Box") || "null");
  const url = URL.createObjectURL(await res.blob());
  return { url, box };
}

function renderStage(shot) {
  const stage = $("#stage");
  const view = state.view;
  if (stage.dataset.url) URL.revokeObjectURL(stage.dataset.url);
  stage.replaceChildren();
  const frame = (shot && shot.box) || view.frame;
  if (!frame || !frame[2] || !frame[3]) {
    stage.append(h("div", { class: "empty", text: "这个窗口没有可用的几何信息" }));
    return;
  }
  const [fx, fy, fw, fh] = frame;
  stage.style.aspectRatio = `${fw} / ${fh}`;
  stage.dataset.ratio = String(fw / fh);
  fitStage();
  stage.classList.toggle("shot", !!shot);
  stage.classList.toggle("wire", !shot);
  stage.style.backgroundImage = shot ? `url(${shot.url})` : "";
  if (shot) stage.dataset.url = shot.url; else delete stage.dataset.url;
  // Biggest first, so a small control is on top of the group that contains it.
  const drawn = view.targets.filter((t) => t.box).sort((a, b) => b.box[2] * b.box[3] - a.box[2] * a.box[3]);
  for (const t of drawn) {
    const [x, y, w, bh] = t.box;
    const layer = layerOf(t);
    const el = h("div", {
      class: `box ${layer}${t.enabled === false ? " off" : ""}${t.id === state.picked ? " picked" : ""}`,
      title: `${nameOf(t) || "（无名称）"} · ${t.kind} · ${aimable(t).join(" ") || "—"}`,
      dataset: { id: t.id },
      onclick: (ev) => { ev.stopPropagation(); pick(t.id); },
    }, h("span", { text: nameOf(t) || t.kind }));
    el.style.left = `${((x - fx) / fw) * 100}%`;
    el.style.top = `${((y - fy) / fh) * 100}%`;
    el.style.width = `${(w / fw) * 100}%`;
    el.style.height = `${(bh / fh) * 100}%`;
    stage.append(el);
  }
  applyLayers();
}

/* The whole window in one screen: as wide as the column allows, never taller than the viewport. */
function fitStage() {
  const stage = $("#stage");
  const ratio = Number(stage.dataset.ratio || 0);
  if (!ratio) { stage.style.width = ""; return; }
  const room = stage.parentElement.clientWidth;
  const tall = Math.max(240, window.innerHeight - stage.getBoundingClientRect().top - 56);
  stage.style.width = `${Math.min(room, tall * ratio)}px`;
}

function applyLayers() {
  const stage = $("#stage");
  for (const box of $$(".legend input[data-layer]")) stage.classList.toggle("hide-" + box.dataset.layer, !box.checked);
  stage.classList.toggle("labels", $("#labels").checked || stage.classList.contains("wire"));
}

function matches(t, words) {
  const hay = [t.id, t.kind, t.label, ...(t.also_called || []), ...(t.actions || [])].join(" ").toLowerCase();
  return words.every((w) => hay.includes(w));
}

function renderTargets() {
  const list = $("#targets");
  const view = state.view;
  list.replaceChildren();
  if (!view) return;
  const words = state.filter.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = view.targets.filter((t) => matches(t, words));
  for (const t of shown.slice(0, 400)) {
    list.append(h("li", { class: t.id === state.picked ? "picked" : "", onclick: () => pick(t.id) },
      h("i", { class: "dot " + layerOf(t) }),
      h("span", { class: "name" }, nameOf(t) || h("em", { text: "（无名称）" }), " ", h("span", { class: "kind", text: t.kind })),
      h("span", { class: "kind", text: aimable(t).join(" ") || "—" }),
    ));
  }
  $("#count").textContent = `显示 ${Math.min(shown.length, 400)} / ${view.targets.length}` + (shown.length > 400 ? "（筛选以缩小范围）" : "");
}

function pick(id) {
  state.picked = id;
  for (const el of $$(".box")) el.classList.toggle("picked", el.dataset.id === id);
  renderTargets();
  renderDetail();
  const row = $("#targets li.picked");
  if (row) row.scrollIntoView({ block: "nearest" });
}

function renderDetail() {
  const card = $("#detail");
  const t = state.view && state.view.targets.find((x) => x.id === state.picked);
  if (!t) {
    card.replaceChildren(h("p", { class: "hint", text: "点选界面上的一个框，查看它能做什么，或把它加进任务。" }));
    return;
  }
  const flags = [];
  if (t.visual) flags.push("来自像素");
  if (t.selected) flags.push("已选中");
  if (t.focused) flags.push("有焦点");
  if (t.enabled === false) flags.push("不可用");
  if (t.value !== undefined && t.value !== "") flags.push("值 = " + JSON.stringify(t.value));
  if (t.note) flags.push(t.note);
  const label = nameOf(t);
  const verbs = aimable(t);
  card.replaceChildren(
    h("div", { class: "detail-head" }, h("h2", { text: label || "（无名称）" }), h("span", { class: "mono", text: t.kind })),
    h("dl", { class: "kv" },
      h("dt", { text: "id" }), h("dd", { class: "mono", text: t.id }),
      (t.also_called || []).length ? [h("dt", { text: "别名" }), h("dd", {}, ...(t.also_called || []).map((n) => h("span", { class: "tag", text: n })))] : null,
      h("dt", { text: "动作" }), h("dd", { text: aimable(t).join(" ") || "—（不能直接操作）" }),
      h("dt", { text: "状态" }), h("dd", { text: flags.join("，") || "—" }),
    ),
    h("div", { class: "meta", text: "加到当前任务：" }),
    h("div", { class: "verbs" },
      ...verbs.map((v) => h("button", { class: "small", text: `${VERB_NAMES[v]} ${v}`, onclick: () => addStepFromTarget(t, v) })),
      h("button", { class: "small", text: "作为成功条件", onclick: () => addCheckFromTarget(t) }),
    ),
  );
}

/* -------------------------------------------------------------------- tasks */

function blankTask() {
  return { goal: "", checks: [], steps: [], inputs: [], limits: { max_steps: 8, max_ms: null, settle_ms: 2500 } };
}

function fromPayload(p) {
  const expect = p.expect || {};
  const checks = (p.checks || []).map((text) => {
    const e = expect[text];
    if (!e) return { text, label: "", mode: "none", value: "" };
    const mode = e.absent ? "absent" : e.selected === true ? "selected" : e.value !== undefined ? "value" : "present";
    return { text, label: e.label || "", mode, value: e.value === undefined ? "" : String(e.value) };
  });
  const steps = (p.steps || []).map((s) => (s.finish ? { verb: s.finish === "DONE" ? "DONE" : s.finish, why: s.why || "" } : { ...s }));
  const inputs = Object.entries(p.inputs || {}).map(([k, v]) => ({ k, v: String(v) }));
  const l = p.limits || {};
  return {
    goal: p.goal || "", checks, steps, inputs,
    limits: { max_steps: l.max_steps || 8, max_ms: l.max_ms ?? null, settle_ms: l.settle_ms ?? 2500 },
    notes: p.notes || [],
  };
}

function toPayload(t) {
  const checks = t.checks.map((c) => c.text.trim()).filter(Boolean);
  const expect = {};
  for (const c of t.checks) {
    const text = c.text.trim();
    if (!text || c.mode === "none" || !c.label.trim()) continue;
    const e = { label: c.label.trim() };
    if (c.mode === "selected") e.selected = true;
    if (c.mode === "absent") e.absent = true;
    if (c.mode === "value") e.value = c.value;
    expect[text] = e;
  }
  const steps = [];
  for (const s of t.steps) {
    if (s.verb === "DONE" || s.verb === "ESCALATE" || s.verb === "STUCK") {
      steps.push(s.verb === "DONE" ? { finish: "DONE", says: checks, why: s.why || "所有步骤已完成" } : { finish: s.verb, why: s.why || "" });
      continue;
    }
    const out = { verb: s.verb };
    for (const k of ["target", "target_label", "onto_label", "key", "chord", "scroll", "value_from", "why"]) {
      if (s[k] !== undefined && String(s[k]).trim() !== "") out[k] = String(s[k]).trim();
    }
    steps.push(out);
  }
  if (steps.length && !steps[steps.length - 1].finish && checks.length) {
    steps.push({ finish: "DONE", says: checks, why: "所有步骤已完成" });
  }
  const payload = { goal: t.goal.trim(), checks };
  if (Object.keys(expect).length) payload.expect = expect;
  if (t.notes && t.notes.length) payload.notes = t.notes;
  const inputs = {};
  for (const i of t.inputs) if (i.k.trim()) inputs[i.k.trim()] = i.v;
  if (Object.keys(inputs).length) payload.inputs = inputs;
  const limits = {};
  if (t.limits.max_steps) limits.max_steps = Number(t.limits.max_steps);
  if (t.limits.max_ms) limits.max_ms = Number(t.limits.max_ms);
  if (t.limits.settle_ms !== null && t.limits.settle_ms !== "") limits.settle_ms = Number(t.limits.settle_ms);
  payload.limits = limits;
  if (steps.length) payload.steps = steps;
  return payload;
}

function ensureTask() {
  if (!state.task) { state.task = blankTask(); state.taskName = ""; }
  return state.task;
}

function addStepFromTarget(t, verb) {
  const task = ensureTask();
  const label = nameOf(t);
  const same = state.view.targets.filter((x) => nameOf(x) && nameOf(x) === label).length;
  const step = { verb, why: "" };
  // A name that is unique in this view is the portable choice; an id only holds for this layout.
  if (label && same === 1) step.target_label = label; else step.target = t.id;
  if (verb === "TYPE" || verb === "SET") step.value_from = (task.inputs[0] && task.inputs[0].k) || "";
  const done = task.steps.findIndex((s) => s.verb === "DONE");
  if (done >= 0) task.steps.splice(done, 0, step); else task.steps.push(step);
  renderEditor();
  toast(`已加入步骤：${VERB_NAMES[verb]} ${label || t.id}`);
}

function addCheckFromTarget(t) {
  const task = ensureTask();
  const label = nameOf(t);
  if (!label) { toast("这个控件没有名称，无法按名称判定；换一个有名称的控件", true); return; }
  let mode = "present", value = "", text = `${label} 存在`;
  if (t.selected) { mode = "selected"; text = `${label} 已选中`; }
  else if (t.value !== undefined && t.value !== "") { mode = "value"; value = String(t.value); text = `${label} 的值为 ${value}`; }
  task.checks.push({ text, label, mode, value });
  renderEditor();
  toast(`已加入成功条件：${text}`);
}

async function loadTasks() {
  try { state.tasks = await api("/api/tasks"); } catch (e) { toast(e.message, true); state.tasks = []; }
  const list = $("#task-list");
  list.replaceChildren();
  if (!state.tasks.length) list.append(h("li", { class: "s", text: "还没有保存的任务" }));
  for (const t of state.tasks) {
    list.append(h("li", { class: t.name === state.taskName ? "on" : "", onclick: () => openTask(t.name) },
      h("div", { class: "t", text: t.goal || t.name }),
      h("div", { class: "s", text: `${t.builtin ? "内置 · " : ""}${t.name} · ${t.steps} 步 · ${t.checks} 条件` }),
    ));
  }
  if (!state.task) {
    if (state.tasks.length) await openTask(state.tasks[0].name); else { state.task = blankTask(); renderEditor(); }
  } else renderEditor();
}

async function openTask(name) {
  try {
    const payload = await api("/api/tasks/" + encodeURIComponent(name));
    state.task = fromPayload(payload);
    state.taskName = name;
    $("#rehearsal").hidden = true;
    renderEditor();
    for (const li of $$("#task-list li")) li.classList.toggle("on", li.querySelector(".s") && li.querySelector(".s").textContent.includes(name));
  } catch (e) { toast(e.message, true); }
}

function input(value, oninput, attrs = {}) {
  return h("input", { value: value ?? "", oninput: (e) => { oninput(e.target.value); syncJson(); }, ...attrs });
}
function select(options, value, onchange, attrs = {}) {
  const el = h("select", { onchange: (e) => { onchange(e.target.value); syncJson(); }, ...attrs },
    ...options.map(([v, text]) => h("option", { value: v, text })));
  el.value = value;
  return el;
}
function tools(list, index) {
  const move = (d) => { const j = index + d; if (j < 0 || j >= list.length) return; [list[index], list[j]] = [list[j], list[index]]; renderEditor(); };
  return h("div", { class: "tools" },
    h("button", { class: "ghost", title: "上移", text: "↑", onclick: () => move(-1) }),
    h("button", { class: "ghost", title: "下移", text: "↓", onclick: () => move(1) }),
    h("button", { class: "ghost danger", title: "删除", text: "✕", onclick: () => { list.splice(index, 1); renderEditor(); } }),
  );
}

function stepExtra(s) {
  const keys = state.task.inputs.map((i) => i.k).filter(Boolean);
  switch (s.verb) {
    case "TYPE": case "SET":
      return keys.length
        ? select([["", "选择输入…"], ...keys.map((k) => [k, "输入：" + k])], s.value_from || "", (v) => (s.value_from = v))
        : h("span", { class: "meta", text: "先在“输入”里声明一个值" });
    case "KEY": return input(s.key, (v) => (s.key = v.toUpperCase()), { placeholder: "按键，如 RETURN" });
    case "CHORD": return input(s.chord, (v) => (s.chord = v.toUpperCase()), { placeholder: "如 CMD+A" });
    case "SCROLL": return select([["DOWN", "向下"], ["UP", "向上"], ["LEFT", "向左"], ["RIGHT", "向右"]], s.scroll || "DOWN", (v) => (s.scroll = v));
    case "DRAG": return input(s.onto_label, (v) => (s.onto_label = v), { placeholder: "拖到哪个控件（名称）" });
    default: return h("span");
  }
}

function renderEditor() {
  const t = ensureTask();
  $("#t-name").value = state.taskName;
  $("#t-goal").value = t.goal;
  $("#l-steps").value = t.limits.max_steps ?? "";
  $("#l-ms").value = t.limits.max_ms ?? "";
  $("#l-settle").value = t.limits.settle_ms ?? "";

  const checks = $("#checks");
  checks.replaceChildren();
  if (!t.checks.length) checks.append(h("li", { class: "meta", text: "还没有成功条件。没有条件的任务无法判定是否完成。" }));
  t.checks.forEach((c, i) => {
    checks.append(h("li", {},
      input(c.text, (v) => (c.text = v), { placeholder: "条件，如：外观为深色" }),
      input(c.label, (v) => (c.label = v), { placeholder: "控件名称" }),
      select(EXPECT_MODES, c.mode, (v) => { c.mode = v; renderEditor(); }),
      c.mode === "value" ? input(c.value, (v) => (c.value = v), { placeholder: "期望的值" }) : h("span"),
      tools(t.checks, i),
    ));
  });

  const steps = $("#steps");
  steps.replaceChildren();
  if (!t.steps.length) steps.append(h("li", { class: "meta", text: "还没有步骤。可以在“实时界面”里点选控件添加。" }));
  t.steps.forEach((s, i) => {
    const isFinish = s.verb === "DONE";
    steps.append(h("li", {},
      select(VERBS.map((v) => [v, `${VERB_NAMES[v]} ${v}`]), s.verb, (v) => { s.verb = v; renderEditor(); }),
      ON_TARGET.has(s.verb)
        ? input(s.target_label || s.target, (v) => { if (s.target && !s.target_label) s.target = v; else { s.target_label = v; delete s.target; } },
          { placeholder: "控件名称", title: s.target && !s.target_label ? "按 id 指定" : "按名称指定" })
        : h("span", { class: "meta", text: isFinish ? "声明完成，交给成功条件判定" : "不需要目标" }),
      stepExtra(s),
      input(s.why, (v) => (s.why = v), { placeholder: "为什么（可选）" }),
      tools(t.steps, i),
    ));
  });

  const inputs = $("#inputs");
  inputs.replaceChildren();
  t.inputs.forEach((row, i) => {
    inputs.append(h("li", {},
      input(row.k, (v) => (row.k = v), { placeholder: "名称，如 query" }),
      input(row.v, (v) => (row.v = v), { placeholder: "值" }),
      tools(t.inputs, i),
    ));
  });
  syncJson();
}

function syncJson() {
  if (!state.task) return;
  $("#t-json").value = JSON.stringify(toPayload(state.task), null, 2);
}

function taskPayload() {
  const t = ensureTask();
  t.goal = $("#t-goal").value;
  t.limits = { max_steps: $("#l-steps").value, max_ms: $("#l-ms").value, settle_ms: $("#l-settle").value };
  return toPayload(t);
}

async function saveTask() {
  const name = $("#t-name").value.trim();
  if (!name) { toast("先给任务起个名字", true); $("#t-name").focus(); return; }
  try {
    const saved = await api("/api/tasks", { method: "POST", body: { name, task: taskPayload() } });
    state.taskName = saved.name;
    $("#task-msg").textContent = "已保存到 " + saved.path;
    toast("已保存");
    loadTasks();
  } catch (e) { toast("没有保存：" + e.message, true); }
}

async function deleteTask() {
  if (!state.taskName || !confirm(`删除任务“${state.taskName}”？`)) return;
  try {
    await api("/api/tasks/" + encodeURIComponent(state.taskName), { method: "DELETE" });
    state.task = null; state.taskName = "";
    toast("已删除");
    loadTasks();
  } catch (e) { toast(e.message, true); }
}

async function rehearseTask() {
  const panel = $("#rehearsal");
  panel.hidden = false;
  panel.replaceChildren(h("p", { class: "meta", text: "演练中：读取一次界面，逐步检查，不执行任何动作…" }));
  try {
    const r = await api("/api/rehearse", { method: "POST", body: { task: taskPayload(), app: $("#t-app").value, pixels: state.pixels } });
    panel.replaceChildren(
      h("div", { class: "row" }, h("h2", { text: "演练结果" }), h("span", { class: "meta", text: `${r.app} · ${r.window || ""} · 什么都没有执行` })),
      ...r.findings.map((f) => h("div", { class: "finding" },
        h("span", { class: "mono", text: f.step }),
        h("span", { class: f.ok ? "ok" : f.later ? "later" : "no", text: f.ok ? "可行" : f.later ? "待定" : "不行" }),
        h("span", { class: "mono", text: f.what }),
        h("span", { text: f.detail }),
      )),
      h("p", { class: "verdict " + (r.blocked ? "no" : "ok"), text: r.verdict }),
    );
  } catch (e) { panel.replaceChildren(h("p", { class: "banner bad", text: e.message })); }
}

function askToRun() {
  const payload = taskPayload();
  const app = $("#t-app").value;
  const hasSteps = (payload.steps || []).some((s) => !s.finish);
  const model = state.doctor && state.doctor.model && state.doctor.model.configured;
  $("#c-summary").textContent = `在“${app || (DESK === "demo" ? "演示应用" : "当前前台应用")}”上执行：${payload.goal || "（未写目标）"}。共 ${(payload.steps || []).length} 步，${payload.checks.length} 个成功条件。`;
  $("#c-focus").checked = false;
  $("#c-focus").disabled = !app || DESK === "demo";
  $("#c-model-row").hidden = !model;
  $("#c-model").checked = !hasSteps && !!model;
  const dialog = $("#confirm");
  dialog.returnValue = "";
  dialog.showModal();
  dialog.onclose = async () => {
    if (dialog.returnValue !== "go") return;
    try {
      const run = await api("/api/runs", {
        method: "POST",
        body: { task: payload, app, pixels: state.pixels, focus: $("#c-focus").checked, model: $("#c-model").checked, confirm: true },
      });
      state.run = run.id;
      location.hash = "#runs";
    } catch (e) { toast("没有开始：" + e.message, true); }
  };
}

/* --------------------------------------------------------------------- runs */

async function loadRuns() {
  try { state.runs = await api("/api/runs"); } catch (e) { toast(e.message, true); return; }
  const list = $("#run-list");
  list.replaceChildren();
  if (!state.runs.length) list.append(h("li", { class: "s", text: "还没有运行过任务" }));
  for (const r of state.runs) {
    list.append(h("li", { class: r.id === state.run ? "on" : "", onclick: () => { state.run = r.id; loadRuns(); } },
      h("div", { class: "row" }, h("span", { class: "t", text: r.goal || r.id }), h("span", { class: "pill " + (r.status || r.state), text: r.status || stateName(r.state) })),
      h("div", { class: "s", text: `${when(r.started)} · ${r.app || "前台应用"} · ${r.steps_taken} 步` }),
    ));
  }
  if (!state.run && state.runs.length) state.run = state.runs[0].id;
  if (state.run) showRun(state.run);
}

function stateName(s) { return { running: "运行中", finished: "已结束", failed: "出错" }[s] || s; }

async function showRun(id) {
  clearTimeout(state.runTimer);
  let r;
  try { r = await api("/api/runs/" + encodeURIComponent(id)); } catch (e) { toast(e.message, true); return; }
  if (state.run !== id) return;
  const box = $("#run-detail");
  const head = h("div", { class: "card" },
    h("div", { class: "row" },
      h("h2", { text: r.goal || r.id }),
      h("span", { class: "pill " + (r.status || r.state), text: r.status || stateName(r.state) }),
      h("span", { class: "grow" }),
      r.state === "running" ? h("button", { class: "danger", text: "停止", onclick: () => cancelRun(id) }) : null,
      r.report ? h("a", { href: `/runs/${encodeURIComponent(id)}.html?t=${encodeURIComponent(TOKEN)}`, target: "_blank", rel: "noopener", text: "在新窗口打开报告" }) : null,
    ),
    h("div", { class: "meta", text: `${r.id} · ${when(r.started)} · ${r.app || "前台应用"}` }),
    r.notice ? h("div", { class: "banner warn", text: r.notice }) : null,
    r.error ? h("div", { class: "banner bad", text: r.error }) : null,
  );
  const kids = [head];
  if (r.report) {
    kids.push(h("iframe", { class: "report-frame", src: `/runs/${encodeURIComponent(id)}.html?t=${encodeURIComponent(TOKEN)}`, title: "运行报告", sandbox: "allow-scripts" }));
  } else {
    kids.push(h("div", { class: "card" },
      h("h2", { text: r.state === "running" ? "进行中" : "步骤" }),
      h("ul", { class: "live" }, ...(r.steps || []).map(stepRow)),
      (r.steps || []).length ? null : h("p", { class: "meta", text: "等待第一步…" }),
    ));
  }
  box.replaceChildren(...kids);
  if (r.state === "running") state.runTimer = setTimeout(() => { if (location.hash === "#runs") loadRuns(); }, 800);
}

function stepRow(s) {
  const c = s.choice || {};
  const what = c.verb || c.finish || (s.failed ? "FAIL" : "?");
  const label = s.target_label || c.target_label || c.target || c.key || c.chord || c.scroll || "";
  const ms = s.ms ? Object.values(s.ms).reduce((a, b) => a + b, 0) : 0;
  return h("li", {},
    h("span", { class: "mono", text: s.n }),
    h("span", { class: "v" + (s.failed ? " bad" : ""), text: what }),
    h("span", { text: s.failed ? `${label ? label + "：" : ""}${s.why}` : label }),
    h("span", { class: "r", text: s.failed ? s.failed : `${s.via || ""} ${ms} ms` }),
  );
}

async function cancelRun(id) {
  try { await api(`/api/runs/${encodeURIComponent(id)}/cancel`, { method: "POST", body: {} }); toast("已请求停止：当前这一步做完后停下"); }
  catch (e) { toast(e.message, true); }
}

/* -------------------------------------------------------------------- setup */

function renderSetup() {
  const d = state.doctor || {};
  const p = d.permissions || {};
  const m = d.model || {};
  const grid = $("#setup");
  const item = (label, ok, okText, badText) => h("li", {}, h("span", { text: label }), h("span", { class: "chip " + (ok ? "ok" : "bad"), text: ok ? okText : badText }));
  grid.replaceChildren(
    h("div", { class: "card" },
      h("h2", { text: "权限" }),
      DESK === "demo" ? h("p", { class: "hint", text: "演示桌面不需要任何权限。" }) : h("p", { class: "hint", text: "权限记在启动控制台的程序上（通常是你的终端）。" }),
      h("ul", { class: "checklist" },
        item("辅助功能（读取和操作控件）", p.accessibility, "已授权", "未授权"),
        item("屏幕录制（截图和文字识别）", p.screen_recording, "已授权", "未授权"),
      ),
      DESK === "demo" ? null : h("pre", { class: "cmd", text: "uv run deskhand permit --open   # 打开对应的系统设置页面" }),
    ),
    h("div", { class: "card" },
      h("h2", { text: "模型" }),
      h("p", { class: "hint", text: "模型是一条命令：从标准输入读提示，向标准输出写一个 JSON 对象。控制台不保存任何密钥。" }),
      h("ul", { class: "checklist" }, item(m.env || "DESKHAND_MODEL_COMMAND", m.configured, m.program || "已配置", "未设置")),
      h("pre", { class: "cmd", text: `export ${m.env || "DESKHAND_MODEL_COMMAND"}="ollama run llama3.1"\nuv run deskhand app` }),
    ),
    h("div", { class: "card" },
      h("h2", { text: "环境" }),
      h("ul", { class: "checklist" },
        h("li", {}, h("span", { text: "Python" }), h("span", { class: "mono", text: d.python || "?" })),
        h("li", {}, h("span", { text: "平台" }), h("span", { class: "mono", text: d.platform || "?" })),
        ...Object.entries(d.modules || {}).map(([k, v]) => item("pyobjc " + k, v, "可用", "缺失")),
        h("li", {}, h("span", { text: "数据目录" }), h("span", { class: "mono", text: state.home || "" })),
      ),
    ),
    h("div", { class: "card" },
      h("h2", { text: "命令行对照" }),
      h("p", { class: "hint", text: "控制台里做的每件事，命令行都能做：" }),
      h("pre", { class: "cmd", text: [
        "uv run deskhand probe --app 系统设置        # 读取界面，不抢焦点",
        "uv run deskhand run --task t.json --dry-run  # 演练",
        "uv run deskhand run --task t.json --app 系统设置 --report run.html",
        "uv run deskhand report run.json             # 保存的结果转成网页",
      ].join("\n") }),
      DESK === "demo" ? h("div", { class: "row end" }, h("button", { text: "重置演示桌面", onclick: async () => { await api("/api/demo-reset", { method: "POST", body: {} }); toast("演示桌面已重置"); } })) : null,
    ),
  );
}

/* --------------------------------------------------------------------- wire */

function wire() {
  window.addEventListener("hashchange", showTab);
  window.addEventListener("resize", fitStage);
  $("#app").addEventListener("change", (e) => { state.app = e.target.value; store.set("app", state.app); $("#t-app").value = state.app; readView(); });
  $("#app").addEventListener("focus", loadApps);
  for (const b of $$("#pixels button")) {
    b.classList.toggle("on", b.dataset.v === state.pixels);
    b.addEventListener("click", () => {
      state.pixels = b.dataset.v; store.set("pixels", state.pixels);
      for (const x of $$("#pixels button")) x.classList.toggle("on", x === b);
      readView();
    });
  }
  $("#refresh").addEventListener("click", readView);
  $("#auto").addEventListener("change", (e) => {
    clearInterval(state.autoTimer);
    if (e.target.checked) state.autoTimer = setInterval(() => { if (location.hash === "#view" || !location.hash) readView(); }, 3000);
  });
  $("#filter").addEventListener("input", (e) => { state.filter = e.target.value; renderTargets(); });
  for (const box of $$(".legend input")) box.addEventListener("change", applyLayers);
  $("#stage").addEventListener("click", () => pick(null));

  $("#new-task").addEventListener("click", () => { state.task = blankTask(); state.taskName = ""; $("#rehearsal").hidden = true; renderEditor(); loadTasks(); });
  $("#add-check").addEventListener("click", () => { ensureTask().checks.push({ text: "", label: "", mode: "selected", value: "" }); renderEditor(); });
  $("#add-step").addEventListener("click", () => { ensureTask().steps.push({ verb: "PRESS", target_label: "", why: "" }); renderEditor(); });
  $("#add-input").addEventListener("click", () => { ensureTask().inputs.push({ k: "", v: "" }); renderEditor(); });
  $("#t-name").addEventListener("input", (e) => (state.taskName = e.target.value));
  $("#t-goal").addEventListener("input", (e) => { ensureTask().goal = e.target.value; syncJson(); });
  for (const [id, key] of [["#l-steps", "max_steps"], ["#l-ms", "max_ms"], ["#l-settle", "settle_ms"]]) {
    $(id).addEventListener("input", (e) => { ensureTask().limits[key] = e.target.value; syncJson(); });
  }
  $("#apply-json").addEventListener("click", () => {
    try { state.task = fromPayload(JSON.parse($("#t-json").value)); renderEditor(); toast("已应用 JSON"); }
    catch (e) { toast("JSON 有误：" + e.message, true); }
  });
  $("#save-task").addEventListener("click", saveTask);
  $("#delete-task").addEventListener("click", deleteTask);
  $("#rehearse").addEventListener("click", rehearseTask);
  $("#run-task").addEventListener("click", askToRun);
}

(async function start() {
  wire();
  renderStatus();
  await loadState();
  await loadApps();
  showTab();
  if (DESK === "demo" || state.app) readView();
})();
