/* ============================================================
 * H3 DIGITAL HUMAN · 数字人工作室
 * 原生 ES Module，零构建、无第三方依赖、不 import MV 工作台模块。
 * 后端契约：/avatar/api/*，所有 JSON 响应 ok:true。
 * ============================================================ */

const API = "/avatar/api";
const LAST_KEY = "h3avatar:lastProject";
const RUN_ACTIVE = new Set(["queued", "running"]);
const RUN_TERMINAL = new Set(["completed", "failed", "cancelled"]);

const MODE_LABEL = { 1: "音频驱动", 2: "参考音色", 3: "无音频参考" };
const RUN_LABEL = {
  idle: "空闲",
  queued: "排队中",
  running: "生成中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已停止",
};
const STAGE_LABEL = {
  idle: "未开始",
  queued: "排队等待 GPU",
  prepare: "准备素材",
  upload: "上传素材",
  prompt: "生成提示词",
  queue: "提交 ComfyUI",
  sampling: "采样中",
  assemble: "拼接成片",
  done: "已完成",
  cancelled: "已停止",
  error: "出错",
};
const PROMPT_SOURCE_LABEL = { llm: "LLM", fallback: "内置模板", manual: "手动编辑" };
const ROLE_ITEMS = [
  ["performer", "人物"],
  ["environment", "场景"],
  ["appearance", "服装 / 造型"],
  ["subject", "道具 / 主体"],
  ["other", "其他"],
];
const ROLE_LABEL = Object.fromEntries(ROLE_ITEMS);

const state = {
  health: null,
  options: {},
  generationDefaults: {},
  prompts: null,
  projects: [],
  project: null,
  run: null,
  mode: 2,
  pendingVoice: null,
  pollTimer: null,
  pollCount: 0,
  polling: false,
  finalKey: "",
  backendReady: false,
  busy: 0,
};

/* ---------------- 基础工具 ---------------- */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[ch]);

function num(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function pad(n, len = 2) {
  return String(n).padStart(len, "0");
}

function fmtWhen(value) {
  if (!value) return "—";
  if (typeof value === "number") {
    const d = new Date(value < 1e12 ? value * 1000 : value);
    if (!Number.isNaN(d.getTime())) return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    return String(value);
  }
  const d = new Date(value);
  if (!Number.isNaN(d.getTime())) return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return String(value);
}

function fmtSec(value) {
  return `${num(value).toFixed(1)}s`;
}

function fmtClock(value) {
  const total = Math.max(0, num(value));
  const minutes = Math.floor(total / 60);
  const seconds = total - minutes * 60;
  return `${pad(minutes)}:${seconds.toFixed(1).padStart(4, "0")}`;
}

function fmtBytes(value) {
  const bytes = num(value);
  if (!bytes) return "—";
  const mb = bytes / 1048576;
  if (mb >= 1024) return `${(mb / 1024).toFixed(2)} GB`;
  if (mb >= 1) return `${mb.toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function fmtTodayName() {
  const d = new Date();
  return `数字人作品 ${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function encodePath(path) {
  return String(path || "")
    .split("/")
    .filter(Boolean)
    .map((part) => encodeURIComponent(part))
    .join("/");
}

function mediaUrl(projectId, path, stamp) {
  if (!projectId || !path) return "";
  const suffix = stamp ? `?t=${encodeURIComponent(stamp)}` : "";
  return `${API}/projects/${encodeURIComponent(projectId)}/media/${encodePath(path)}${suffix}`;
}

function aspectRatioCss(aspect) {
  const match = String(aspect || "").match(/(\d+(?:\.\d+)?)\s*[:x×]\s*(\d+(?:\.\d+)?)/);
  if (match) return `${match[1]} / ${match[2]}`;
  return "9 / 16";
}

function isRunActive(run) {
  return !!run && RUN_ACTIVE.has(run.status || "");
}

function runStatusMeta(status) {
  const map = {
    idle: ["待生成", ""],
    running: ["生成中", "status-running"],
    completed: ["已完成", "status-completed"],
    failed: ["失败", "status-failed"],
  };
  return map[status] || map.idle;
}

function optionValue(item) {
  if (item === null || item === undefined) return "";
  if (typeof item === "string" || typeof item === "number") return String(item);
  return String(item.value ?? item.id ?? item.key ?? item.name ?? item.label ?? "");
}

function optionLabel(item) {
  if (item === null || item === undefined) return "";
  if (typeof item === "string" || typeof item === "number") return String(item);
  return String(item.label ?? item.title ?? item.name ?? item.value ?? item.id ?? item.key ?? "");
}

function fillSelect(select, items, current, placeholder) {
  if (!select) return;
  const list = Array.isArray(items) ? items : [];
  const html = list.map(
    (item) => `<option value="${esc(optionValue(item))}">${esc(optionLabel(item))}</option>`
  );
  if (!html.length && placeholder) html.push(`<option value="">${esc(placeholder)}</option>`);
  select.innerHTML = html.join("");
  setSelectValue(select, current);
}

function setSelectValue(select, value) {
  if (!select) return;
  const raw = value === null || value === undefined || value === "" ? "" : String(value);
  if (raw) {
    const exists = [...select.options].some((opt) => opt.value === raw);
    if (!exists) {
      const opt = document.createElement("option");
      opt.value = raw;
      opt.textContent = raw;
      select.insertBefore(opt, select.firstChild);
    }
  }
  select.value = raw;
  if (!select.value && select.options.length) select.selectedIndex = 0;
}

function itemList(key) {
  const list = state.options?.[key];
  return Array.isArray(list) ? list : [];
}

/* ---------------- Toast / Busy / Confirm ---------------- */

function toast(message, kind = "info", ms = 4200) {
  const stack = $("#toasts");
  if (!stack || !message) return;
  const text = String(message);
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = text.length > 320 ? `${text.slice(0, 320)}…` : text;
  el.title = text;
  stack.appendChild(el);
  window.setTimeout(() => {
    el.style.transition = "opacity .22s ease, transform .22s ease";
    el.style.opacity = "0";
    el.style.transform = "translateX(10px)";
    window.setTimeout(() => el.remove(), 240);
  }, ms);
}

function showBusy(text = "处理中…") {
  state.busy += 1;
  $("#busy-text").textContent = text;
  $("#busy-overlay").hidden = false;
}

function hideBusy() {
  state.busy = Math.max(0, state.busy - 1);
  if (!state.busy) $("#busy-overlay").hidden = true;
}

async function withBusy(button, task) {
  const el = typeof button === "string" ? $(button) : button;
  if (el) {
    el.disabled = true;
    el.classList.add("is-busy");
  }
  try {
    return await task();
  } finally {
    if (el) {
      el.disabled = false;
      el.classList.remove("is-busy");
    }
    syncButtons();
  }
}

function confirmDialog(title, text, confirmLabel = "确认") {
  return new Promise((resolve) => {
    const modal = $("#modal-confirm");
    const yes = $("#btn-confirm-yes");
    $("#confirm-title").textContent = title;
    $("#confirm-text").textContent = text;
    yes.textContent = confirmLabel;
    modal.hidden = false;

    const shots = $$("[data-close]", modal);
    const done = (value) => {
      modal.hidden = true;
      yes.removeEventListener("click", onYes);
      shots.forEach((btn) => btn.removeEventListener("click", onNo));
      document.removeEventListener("keydown", onKey);
      resolve(value);
    };
    const onYes = () => done(true);
    const onNo = () => done(false);
    const onKey = (event) => {
      if (event.key === "Escape") done(false);
    };
    yes.addEventListener("click", onYes);
    shots.forEach((btn) => btn.addEventListener("click", onNo));
    document.addEventListener("keydown", onKey);
  });
}

/* ---------------- HTTP 封装 ---------------- */

async function parseResponse(response) {
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!response.ok) {
    let message = data?.detail ?? data?.error ?? `请求失败（${response.status}）`;
    if (Array.isArray(message)) {
      message = message.map((item) => item?.msg || JSON.stringify(item)).join("；");
    } else if (message && typeof message === "object") {
      message = JSON.stringify(message);
    }
    const err = new Error(message || "请求失败");
    err.status = response.status;
    throw err;
  }
  if (data && data.ok === false) throw new Error(data.error || data.detail || "请求失败");
  return data;
}

async function request(path, { method = "GET", body, form } = {}) {
  const init = { method, headers: {} };
  if (form instanceof FormData) {
    init.body = form;
  } else if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  return parseResponse(await fetch(path, init));
}

function formData(fields, fileField, file) {
  const fd = new FormData();
  Object.entries(fields || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null) fd.append(key, value);
  });
  if (file && fileField) fd.append(fileField, file);
  return fd;
}

const api = {
  health: () => request(`${API}/health`),
  options: () => request(`${API}/options`),
  prompts: () => request(`${API}/prompts`),
  savePrompts: (body) => request(`${API}/prompts`, { method: "PATCH", body }),
  projects: () => request(`${API}/projects`),
  createProject: (fields, file) =>
    request(`${API}/projects`, { method: "POST", form: formData(fields, "voice", file) }),
  project: (id) => request(`${API}/projects/${encodeURIComponent(id)}`),
  patchProject: (id, body) => request(`${API}/projects/${encodeURIComponent(id)}`, { method: "PATCH", body }),
  deleteProject: (id) => request(`${API}/projects/${encodeURIComponent(id)}`, { method: "DELETE" }),
  uploadVoice: (id, file) =>
    request(`${API}/projects/${encodeURIComponent(id)}/voice`, { method: "POST", form: formData({}, "voice", file) }),
  deleteVoice: (id) => request(`${API}/projects/${encodeURIComponent(id)}/voice`, { method: "DELETE" }),
  addRef: (id, file, role, title, description) =>
    request(`${API}/projects/${encodeURIComponent(id)}/refs`, {
      method: "POST",
      form: formData({ role, title, description }, "image", file),
    }),
  patchRef: (id, index, body) =>
    request(`${API}/projects/${encodeURIComponent(id)}/refs/${index}`, { method: "PATCH", body }),
  deleteRef: (id, index) => request(`${API}/projects/${encodeURIComponent(id)}/refs/${index}`, { method: "DELETE" }),
  plan: (id, body) => request(`${API}/projects/${encodeURIComponent(id)}/plan`, { method: "POST", body }),
  patchSegment: (id, index, body) =>
    request(`${API}/projects/${encodeURIComponent(id)}/segments/${index}`, { method: "PATCH", body }),
  segmentPrompt: (id, index) =>
    request(`${API}/projects/${encodeURIComponent(id)}/segments/${index}/prompt`, { method: "POST", body: {} }),
  promptsAll: (id) => request(`${API}/projects/${encodeURIComponent(id)}/prompts`, { method: "POST", body: {} }),
  generate: (id, body) => request(`${API}/projects/${encodeURIComponent(id)}/generate`, { method: "POST", body }),
  regenerate: (id, index) =>
    request(`${API}/projects/${encodeURIComponent(id)}/segments/${index}/regenerate`, { method: "POST", body: {} }),
  cancel: (id) => request(`${API}/projects/${encodeURIComponent(id)}/cancel`, { method: "POST", body: {} }),
  run: (id) => request(`${API}/projects/${encodeURIComponent(id)}/run`),
  selectTake: (id, index, takeId) =>
    request(
      `${API}/projects/${encodeURIComponent(id)}/segments/${index}/takes/${encodeURIComponent(takeId)}/select`,
      { method: "POST", body: {} }
    ),
  deleteTake: (id, index, takeId) =>
    request(`${API}/projects/${encodeURIComponent(id)}/segments/${index}/takes/${encodeURIComponent(takeId)}`, {
      method: "DELETE",
    }),
  assemble: (id) => request(`${API}/projects/${encodeURIComponent(id)}/assemble`, { method: "POST", body: {} }),
  downloadUrl: (id) => `${API}/projects/${encodeURIComponent(id)}/download`,
};

/* ---------------- 表单读写 ---------------- */

function readForm() {
  let cps = num($("#f-cps").value, 5);
  cps = Math.min(12, Math.max(1, cps));
  let target = Math.min(15, Math.max(3, num($("#f-target").value, 8)));
  let max = Math.min(15, Math.max(5, num($("#f-max").value, 12)));
  if (max < target) max = target;
  return {
    name: $("#f-name").value.trim(),
    mode: state.mode,
    script: $("#f-script").value.replace(/\s+$/g, "").replace(/^\s+/g, ""),
    voice_description: $("#f-voice-desc").value.trim(),
    chars_per_second: Number(cps.toFixed(2)),
    target_seconds: target,
    max_seconds: max,
  };
}

function readGeneration() {
  const gen = { ...(state.generationDefaults || {}) };
  gen.aspect_ratio = $("#f-aspect").value || gen.aspect_ratio || "9:16";
  const mp = Number($("#f-mp").value);
  if (Number.isFinite(mp)) gen.megapixels = mp;
  const seed = Number($("#f-seed").value);
  gen.seed = Number.isFinite(seed) ? Math.max(0, Math.round(seed)) : 0;

  const pickNumber = (selector, key) => {
    const el = $(selector);
    const value = Number(el?.value);
    if (el && el.value !== "" && Number.isFinite(value)) gen[key] = value;
  };
  const pickText = (selector, key) => {
    const value = $(selector)?.value;
    if (value) gen[key] = value;
  };
  pickNumber("#a-steps", "steps");
  pickNumber("#a-cfg", "cfg");
  pickNumber("#a-lora-strength", "lora_strength");
  pickText("#a-unet", "unet");
  pickText("#a-clip", "clip");
  pickText("#a-vae", "vae_video");
  pickText("#a-lora", "lora");
  pickText("#a-sampler", "sampler");
  pickText("#a-scheduler", "scheduler");
  return gen;
}

function fillGeneration(gen) {
  const g = gen || {};
  setSelectValue($("#f-aspect"), g.aspect_ratio || "9:16");
  setSelectValue($("#f-mp"), g.megapixels !== undefined && g.megapixels !== null ? String(Number(g.megapixels)) : "1");
  $("#f-seed").value = g.seed ?? 0;
  setSelectValue($("#a-unet"), g.unet);
  setSelectValue($("#a-clip"), g.clip);
  setSelectValue($("#a-vae"), g.vae_video);
  setSelectValue($("#a-lora"), g.lora);
  setSelectValue($("#a-sampler"), g.sampler);
  setSelectValue($("#a-scheduler"), g.scheduler);
  $("#a-steps").value = g.steps ?? "";
  $("#a-cfg").value = g.cfg ?? "";
  $("#a-lora-strength").value = g.lora_strength ?? "";
}

function updateRangeLabels() {
  $("#target-label").textContent = `${num($("#f-target").value, 8).toFixed(1)}s`;
  $("#max-label").textContent = `${num($("#f-max").value, 12).toFixed(1)}s`;
}

function updateScriptCount() {
  const count = ($("#f-script").value || "").replace(/\s+/g, "").length;
  $("#script-count").textContent = `${count} 字`;
}

function clampRanges(changed) {
  const target = num($("#f-target").value, 8);
  const max = num($("#f-max").value, 12);
  if (changed === "target" && max < target) $("#f-max").value = String(target);
  if (changed === "max" && max < target) $("#f-target").value = String(max);
  updateRangeLabels();
}

function fillForm(project) {
  $("#f-name").value = project.name || "";
  $("#f-script").value = project.script || "";
  $("#f-cps").value = project.chars_per_second ?? 5;
  $("#f-target").value = project.target_seconds ?? 8;
  $("#f-max").value = project.max_seconds ?? 12;
  $("#f-voice-desc").value = project.voice_description || "";
  fillGeneration(project.generation || {});
  setMode(project.mode === 3 ? 3 : 2, { quiet: true });
  updateRangeLabels();
  updateScriptCount();
  renderVoice();
}

function resetForm() {
  $("#f-name").value = "";
  $("#f-script").value = "";
  $("#f-cps").value = "5.0";
  $("#f-target").value = "8";
  $("#f-max").value = "12";
  $("#f-voice-desc").value = "";
  $("#f-seed").value = "0";
  fillGeneration(state.generationDefaults || {});
  updateRangeLabels();
  updateScriptCount();
  renderVoice();
}

/* ---------------- 模式 / 参考音频 ---------------- */

function setMode(mode, { quiet = false } = {}) {
  state.mode = Number(mode) === 3 ? 3 : 2;
  $$("[data-mode-card]").forEach((btn) => {
    btn.classList.toggle("active", Number(btn.dataset.modeCard) === state.mode);
  });
  $("#mode-form-2").classList.toggle("hidden", state.mode !== 2);
  $("#mode-form-3").classList.toggle("hidden", state.mode !== 3);
  const hint = $("#mode-hint");
  if (hint) {
    hint.textContent =
      state.mode === 2
        ? "参考音色：上传 2–15 秒参考音频，H3 逐段原生生成该音色说出台词的视频（自带音频）。"
        : "无音频参考：只用台词与音色描述，按约 5 字/秒规划每段时长，视频自带生成音频。";
  }
  if (state.project && state.project.mode !== state.mode && !quiet) {
    toast(`已切换为「${MODE_LABEL[state.mode]}」，点「保存设定」生效`, "info");
  }
  renderVoice();
}

function currentVoice() {
  if (state.project) return state.project.voice || null;
  if (state.pendingVoice) {
    return {
      filename: state.pendingVoice.name,
      duration: state.pendingVoice._probeDuration || 0,
      pending: true,
    };
  }
  return null;
}

function renderVoice() {
  const voice = currentVoice();
  const drop = $("#voice-drop");
  const row = $("#voice-row");
  const label = $("#voice-label");
  if (!drop || !row) return;
  if (!voice) {
    drop.classList.remove("hidden");
    row.classList.add("hidden");
    label.textContent = "";
    return;
  }
  drop.classList.add("hidden");
  row.classList.remove("hidden");
  $("#voice-name").textContent = voice.filename || "参考音频";
  const parts = [];
  if (num(voice.duration) > 0) parts.push(`${num(voice.duration).toFixed(1)} 秒`);
  if (voice.pending) parts.push("创建作品后上传");
  else if (voice.sample_rate) parts.push(`${num(voice.sample_rate)} Hz`);
  if (!parts.length) parts.push("已上传");
  const outOfRange = num(voice.duration) > 0 && (num(voice.duration) < 2 || num(voice.duration) > 15);
  const span = $("#voice-duration");
  span.textContent = parts.join(" · ") + (outOfRange ? " · 建议 2–15 秒" : num(voice.duration) > 0 ? " · 时长合适" : "");
  span.classList.toggle("warn", outOfRange);
  label.textContent = voice.filename || "";
}

function probeAudioDuration(file) {
  return new Promise((resolve) => {
    let settled = false;
    let url = "";
    const audio = document.createElement("audio");
    const finish = (value) => {
      if (settled) return;
      settled = true;
      if (url) URL.revokeObjectURL(url);
      audio.removeAttribute("src");
      audio.remove();
      resolve(Number.isFinite(value) && value > 0 ? value : 0);
    };
    try {
      url = URL.createObjectURL(file);
      audio.preload = "metadata";
      audio.onloadedmetadata = () => finish(audio.duration);
      audio.onerror = () => finish(0);
      window.setTimeout(() => finish(audio.duration), 5000);
      audio.src = url;
    } catch {
      finish(0);
    }
  });
}

async function handleVoiceFile(file) {
  if (!file) return;
  const duration = await probeAudioDuration(file);
  if (state.project) {
    await withBusy($("#btn-voice-replace"), async () => {
      const data = await api.uploadVoice(state.project.id, file);
      state.pendingVoice = null;
      applyProject(data.project, { fill: false, segments: false });
      toast("参考音频已上传", "ok");
    }).catch((err) => toast(`音频上传失败：${err.message}`, "err"));
    return;
  }
  state.pendingVoice = file;
  file._probeDuration = duration;
  renderVoice();
  toast("参考音频已选择，创建作品时一起上传", "info");
}

async function deleteVoice() {
  if (!state.project) {
    state.pendingVoice = null;
    renderVoice();
    toast("已取消待上传的参考音频", "info");
    return;
  }
  const ok = await confirmDialog("删除参考音频", "删除后模式 2 需要重新上传参考音频才能生成。继续？", "删除");
  if (!ok) return;
  await withBusy($("#btn-voice-del"), async () => {
    const data = await api.deleteVoice(state.project.id);
    applyProject(data.project, { fill: false, segments: false });
    toast("参考音频已删除", "ok");
  }).catch((err) => toast(`删除失败：${err.message}`, "err"));
}

/* ---------------- 系统状态 / 选项 / 提示词 ---------------- */

function renderHealth(data) {
  const setChip = (selector, cls, value, title) => {
    const chip = $(selector);
    if (!chip) return;
    chip.classList.remove("ok", "warn", "err");
    if (cls) chip.classList.add(cls);
    chip.querySelector("b").textContent = value;
    chip.title = title || "";
  };
  if (!data) {
    setChip("#chip-comfy", "err", "未连接", "无法访问 /avatar/api/health");
    setChip("#chip-ffmpeg", "err", "未知");
    setChip("#chip-llm", "err", "未知");
    return;
  }
  const comfy = data.comfy || {};
  const comfyOk = !!comfy.ok;
  const deviceShort = comfy.device
    ? String(comfy.device).replace(/^cuda:\d+\s*/i, "").split(":")[0].replace(/^NVIDIA\s+(GeForce\s+)?/i, "").trim()
    : "";
  const comfyValue = comfyOk ? (deviceShort || "已连接") : "未连接";
  setChip("#chip-comfy", comfyOk ? "ok" : "err", comfyValue, [comfy.version ? `v${comfy.version}` : "", comfy.device || ""].filter(Boolean).join(" · "));
  setChip("#chip-ffmpeg", data.ffmpeg ? "ok" : "err", data.ffmpeg ? "可用" : "缺失");
  setChip("#chip-llm", data.llm ? "ok" : "warn", data.llm ? "已启用" : "未启用");
}

async function loadHealth() {
  try {
    const data = await api.health();
    state.health = data;
    state.backendReady = true;
    renderHealth(data);
  } catch (err) {
    state.backendReady = false;
    renderHealth(null);
  }
}

function fillAdvancedOptions() {
  const g = state.generationDefaults || {};
  const current = (selector, fallback) => $(selector)?.value || fallback;
  fillSelect($("#a-unet"), itemList("unet"), current("#a-unet", g.unet), "默认");
  fillSelect($("#a-clip"), itemList("clip"), current("#a-clip", g.clip), "默认");
  fillSelect($("#a-vae"), itemList("vae"), current("#a-vae", g.vae_video), "默认");
  fillSelect($("#a-lora"), itemList("lora"), current("#a-lora", g.lora), "默认");
  fillSelect($("#a-sampler"), itemList("samplers"), current("#a-sampler", g.sampler), "默认");
  fillSelect($("#a-scheduler"), itemList("schedulers"), current("#a-scheduler", g.scheduler), "默认");
}

function fillAspectOptions() {
  const aspects = itemList("aspects");
  const fallback = ["9:16", "16:9", "1:1", "4:3", "3:4", "21:9"];
  const current = $("#f-aspect").value || state.generationDefaults?.aspect_ratio || "9:16";
  fillSelect($("#f-aspect"), aspects.length ? aspects : fallback, current);
}

async function loadOptions() {
  try {
    const data = await api.options();
    state.options = data.options || {};
    state.generationDefaults = data.defaults || {};
  } catch {
    state.options = {};
    state.generationDefaults = {};
  }
  fillAspectOptions();
  fillAdvancedOptions();
  if (!state.project) fillGeneration(state.generationDefaults || {});
}

function renderPrompts(data) {
  state.prompts = data?.prompts || {};
  const modes = [
    ["1", "模式 1 · 音频驱动"],
    ["2", "模式 2 · 参考音色"],
    ["3", "模式 3 · 无音频参考"],
  ];
  const box = $("#prompt-modes");
  if (!box) return;
  box.innerHTML = modes
    .map(([key, label]) => {
      const item = state.prompts[key] || {};
      const configured = !!item.configured;
      return `<div class="av-prompt-item">
        <details>
          <summary class="av-prompt-summary">
            <svg><use href="#i-chevron"/></svg>
            <strong>${label}</strong>
            <span class="grow"></span>
            <span class="pill ${configured ? "status-completed" : ""}">${configured ? "已配置" : "占位"}</span>
          </summary>
          <textarea rows="3" spellcheck="false" data-prompt-key="${key}" placeholder="留空 = 后端使用内置模板（source=fallback）">${esc(item.text || "")}</textarea>
        </details>
      </div>`;
    })
    .join("");
  const configuredCount = Object.values(state.prompts).filter((item) => item?.configured).length;
  $("#prompt-summary").textContent = configuredCount ? `已配置 ${configuredCount} / 3` : "占位";
  $("#btn-save-prompts").classList.remove("hidden");
}

async function loadPrompts() {
  try {
    renderPrompts(await api.prompts());
  } catch {
    $("#prompt-summary").textContent = "不可用";
    $("#btn-save-prompts").classList.add("hidden");
  }
}

async function savePrompts() {
  const body = {};
  $$("[data-prompt-key]").forEach((el) => {
    body[el.dataset.promptKey] = el.value;
  });
  await withBusy($("#btn-save-prompts"), async () => {
    renderPrompts(await api.savePrompts(body));
    toast("系统提示词已保存", "ok");
  }).catch((err) => toast(`提示词保存失败：${err.message}`, "err"));
}

/* ---------------- 作品列表 / 打开 / 删除 ---------------- */

function summaryOf(project) {
  return {
    id: project.id,
    name: project.name,
    updated: project.updated,
    mode: project.mode,
    segments: Array.isArray(project.segments) ? project.segments.length : num(project.segments),
    status: project.run?.status || "idle",
    final: !!project.final,
    thumb: project.final?.thumb || null,
  };
}

function updateSummary(project) {
  if (!project?.id) return;
  const row = summaryOf(project);
  const index = state.projects.findIndex((item) => item.id === project.id);
  if (index >= 0) state.projects[index] = { ...state.projects[index], ...row };
  else state.projects.push(row);
  renderProjectList();
}

function renderProjectList() {
  const box = $("#project-list");
  if (!box) return;
  const items = [...state.projects].sort((a, b) => num(b.updated) - num(a.updated));
  $("#project-count").textContent = `${items.length} 个`;
  if (!items.length) {
    box.innerHTML = `<div class="av-list-empty">暂无作品，先在左侧新建</div>`;
    return;
  }
  box.innerHTML = items
    .map((item) => {
      const active = state.project?.id === item.id;
      const count = Array.isArray(item.segments) ? item.segments.length : num(item.segments);
      const mode = Number(item.mode) === 3 ? 3 : 2;
      return `<div class="av-project ${active ? "active" : ""}" role="button" tabindex="0"
        data-action="open-project" data-id="${esc(item.id)}" title="打开作品">
        <span class="av-project-main">
          <strong>${esc(item.name || "未命名作品")}</strong>
          <span>${MODE_LABEL[mode]} · ${count} 段 · ${fmtWhen(item.updated)}</span>
        </span>
        <span class="av-project-side">
          <i class="av-project-dot mode${mode}" title="${MODE_LABEL[mode]}"></i>
          ${item.final ? '<span class="pill status-completed" title="已有成片">成片</span>' : ""}
          <button class="av-project-del" type="button" data-action="del-project" data-id="${esc(item.id)}" title="删除作品">
            <svg><use href="#i-trash"/></svg>
          </button>
        </span>
      </div>`;
    })
    .join("");
}

async function loadProjects() {
  try {
    const data = await api.projects();
    state.projects = Array.isArray(data.projects) ? data.projects : [];
  } catch {
    if (!state.projects.length) state.projects = [];
  }
  renderProjectList();
}

async function openProject(id) {
  if (!id) return;
  try {
    const data = await api.project(id);
    state.finalKey = "";
    applyProject(data.project, { fill: true });
    localStorage.setItem(LAST_KEY, data.project.id);
  } catch (err) {
    toast(`打开作品失败：${err.message}`, "err");
    localStorage.removeItem(LAST_KEY);
    if (state.project?.id === id) clearProject({ silent: true });
  }
}

async function removeProject(id, name) {
  const ok = await confirmDialog("删除作品", `确定删除「${name || id}」？参考图、分段与成片会一起删除，无法恢复。`, "删除");
  if (!ok) return;
  try {
    await api.deleteProject(id);
    state.projects = state.projects.filter((item) => item.id !== id);
    renderProjectList();
    if (state.project?.id === id) clearProject({ silent: true });
    toast("作品已删除", "ok");
  } catch (err) {
    toast(`删除失败：${err.message}`, "err");
  }
}

function clearProject({ silent = false } = {}) {
  stopPolling();
  state.project = null;
  state.run = null;
  state.pendingVoice = null;
  state.finalKey = "";
  localStorage.removeItem(LAST_KEY);
  resetForm();
  renderProjectContext();
  renderSegments();
  renderRefs();
  renderRun(null);
  renderFinal();
  renderProjectList();
  syncButtons();
  if (!silent) toast("已清空当前选择，可新建作品", "info");
}

/* ---------------- 创建 / 保存 / 分段 ---------------- */

async function createProject() {
  const form = readForm();
  form.name = form.name || fmtTodayName();
  const generation = readGeneration();
  const voiceFile = state.mode === 2 ? state.pendingVoice : null;
  await withBusy($("#btn-create"), async () => {
    const data = await api.createProject({ ...form, generation: JSON.stringify(generation) }, voiceFile);
    state.pendingVoice = null;
    applyProject(data.project, { fill: true });
    localStorage.setItem(LAST_KEY, data.project.id);
    await loadProjects();
    toast(`作品「${data.project.name}」已创建`, "ok");
    if ((data.project.script || "").trim()) {
      const planned = await planSegments({ silent: true });
      if (planned) toast(`已自动分成 ${(state.project?.segments || []).length} 段`, "ok");
    }
  }).catch((err) => toast(`创建失败：${err.message}`, "err", 6000));
}

async function saveSettings() {
  const p = state.project;
  if (!p) return createProject();
  const form = readForm();
  form.name = form.name || p.name || fmtTodayName();
  const generation = readGeneration();
  await withBusy($("#btn-create"), async () => {
    let project = (await api.patchProject(p.id, { ...form, generation })).project;
    if (state.pendingVoice && state.mode === 2) {
      project = (await api.uploadVoice(p.id, state.pendingVoice)).project;
      state.pendingVoice = null;
    }
    applyProject(project, { fill: false });
    await loadProjects();
    toast("设定已保存", "ok");
    if ((p.script || "") !== (form.script || "") && (p.segments || []).length) {
      toast("台词已变更，点「按台词分段」重新分段", "info", 5200);
    }
  }).catch((err) => toast(`保存失败：${err.message}`, "err", 6000));
}

async function planSegments({ silent = false } = {}) {
  const p = state.project;
  if (!p) {
    toast("请先新建作品", "err");
    return false;
  }
  const form = readForm();
  form.name = form.name || p.name;
  if (!silent && (p.segments || []).length) {
    const ok = await confirmDialog(
      "重新分段",
      "会按当前台词重建全部分段，已有的提示词与片段选择可能失效。继续？",
      "重新分段"
    );
    if (!ok) return false;
  }
  const button = silent ? null : $("#btn-plan2");
  try {
    await withBusy(button, async () => {
      await api.patchProject(p.id, { ...form, generation: readGeneration() });
      const data = await api.plan(p.id, {
        script: form.script,
        chars_per_second: form.chars_per_second,
        target_seconds: form.target_seconds,
        max_seconds: form.max_seconds,
      });
      applyProject(data.project, { fill: false });
      await loadProjects();
      if (!silent) {
        const warnings = data.warnings || [];
        toast(
          warnings.length
            ? `已分成 ${(data.project.segments || []).length} 段（${warnings.length} 条提示）`
            : `已分成 ${(data.project.segments || []).length} 段`,
          warnings.length ? "info" : "ok"
        );
      }
    });
  } catch (err) {
    toast(`分段失败：${err.message}`, "err", 6000);
    return false;
  }
  return true;
}

/* ---------------- 分段渲染 ---------------- */

function segTimeHtml(seg) {
  const frames = seg.frames !== undefined && seg.frames !== null ? `${num(seg.frames)} 帧` : "— 帧";
  return `约 ${num(seg.seconds).toFixed(1)}s<br>${frames}`;
}

function takesHtml(project, seg) {
  const takes = Array.isArray(seg.takes) ? seg.takes : [];
  if (!takes.length) {
    return `<span class="av-takes-empty">还没有片段 · 点「重生成」或「开始生成」</span>`;
  }
  return takes
    .map((take, takeIndex) => {
      const active = take.take_id === seg.selected_take;
      const thumb = take.thumb ? mediaUrl(project.id, take.thumb, take.created) : "";
      const fallback = `<div class="av-take-fallback"><svg><use href="#i-film"/></svg></div>`;
      return `<div class="av-take ${active ? "active" : ""}" role="button" tabindex="0"
        data-action="take-select" data-index="${seg.index}" data-take="${esc(take.take_id)}"
        title="${active ? "当前选用" : "点击选用这个片段"}${take.created ? ` · ${esc(fmtWhen(take.created))}` : ""}">
        ${thumb ? `<img src="${esc(thumb)}" alt="" loading="lazy">` : fallback}
        <span class="take-no">${takeIndex + 1}</span>
        ${active ? '<span class="take-picked">✓</span>' : `<button class="take-del" type="button" data-action="take-del" data-index="${seg.index}" data-take="${esc(take.take_id)}" title="删除这个片段"><svg><use href="#i-close"/></svg></button>`}
      </div>`;
    })
    .join("");
}

function promptBlockHtml(seg) {
  const prompt = (seg.prompt || "").trim();
  if (!prompt) return "";
  const sourceKey = seg.prompt_source || "fallback";
  const source = PROMPT_SOURCE_LABEL[sourceKey] || sourceKey;
  const sourceClass = sourceKey === "fallback" ? "fallback" : sourceKey === "manual" ? "manual" : "";
  return `<details class="av-seg-prompt">
    <summary>
      <svg><use href="#i-chevron"/></svg>
      <span>提示词</span>
      <span class="prompt-source ${sourceClass}">${source}</span>
      <span class="grow"></span>
      <span style="font-size:9px;color:var(--faint)">展开编辑</span>
    </summary>
    <div class="av-prompt-body">
      <textarea class="av-prompt-area" spellcheck="false" data-prompt-index="${seg.index}">${esc(seg.prompt)}</textarea>
      <div class="av-prompt-bar">
        <span style="font-size:9.5px;color:var(--faint)">保存后重新生成会用新提示词</span>
        <button class="btn ghost sm" type="button" data-action="save-prompt" data-index="${seg.index}">保存提示词</button>
      </div>
    </div>
  </details>`;
}

function warningsHtml(seg) {
  const warnings = Array.isArray(seg.prompt_warnings) ? seg.prompt_warnings : [];
  if (!warnings.length) return "";
  const text = warnings
    .slice(0, 3)
    .map((item) => esc(typeof item === "string" ? item : JSON.stringify(item)))
    .join("<br>");
  return `<div class="av-seg-warn">${text}</div>`;
}

function segCardHtml(project, seg) {
  const status = seg.status || "idle";
  const [statusText, statusClass] = runStatusMeta(status);
  return `<article class="av-seg ${esc(status)}" data-index="${seg.index}">
    <div class="av-seg-index">
      <span class="seg-num">${pad(seg.index + 1)}</span>
      <span class="av-seg-time">${segTimeHtml(seg)}</span>
    </div>
    <div class="av-seg-main">
      <div class="av-seg-top">
        <h3>第 ${seg.index + 1} 段</h3>
        <span class="pill seg-status ${statusClass}">${statusText}</span>
        <span class="pill" data-role="chars">${num(seg.chars)} 字</span>
        ${seg.prompt ? '<span class="pill vocal">已备提示词</span>' : ""}
      </div>
      <textarea class="av-seg-text" rows="2" spellcheck="false" data-action="seg-text"
        data-index="${seg.index}" data-fkey="seg-text-${seg.index}"
        placeholder="这一段的台词…">${esc(seg.text || "")}</textarea>
      ${seg.error ? `<div class="av-seg-error">${esc(seg.error)}</div>` : ""}
      ${warningsHtml(seg)}
      ${promptBlockHtml(seg)}
      <div class="av-takes">
        <span class="av-takes-label">选片</span>
        ${takesHtml(project, seg)}
      </div>
    </div>
    <div class="av-seg-actions">
      <button class="btn ghost sm" type="button" data-action="seg-prompt" data-index="${seg.index}">
        <svg><use href="#i-spark"/></svg>生成提示词
      </button>
      <button class="btn ghost sm" type="button" data-action="seg-regen" data-index="${seg.index}">
        <svg><use href="#i-refresh"/></svg>重生成
      </button>
    </div>
  </article>`;
}

function renderSegments() {
  const list = $("#seg-list");
  const empty = $("#stage-empty");
  const project = state.project;
  if (!project) {
    list.innerHTML = "";
    showEmpty("no-project");
    return;
  }
  const segments = project.segments || [];
  if (!segments.length) {
    list.innerHTML = "";
    showEmpty("no-segments");
    return;
  }
  empty.hidden = true;
  const openIndexes = $$(".av-seg-prompt[open]", list)
    .map((details) => Number(details.closest(".av-seg")?.dataset.index))
    .filter((index) => Number.isFinite(index));
  list.innerHTML = segments.map((seg) => segCardHtml(project, seg)).join("");
  openIndexes.forEach((index) => {
    const details = list.querySelector(`.av-seg[data-index="${index}"] .av-seg-prompt`);
    if (details) details.open = true;
  });
}

function showEmpty(kind) {
  const empty = $("#stage-empty");
  empty.hidden = false;
  if (kind === "no-segments") {
    empty.innerHTML = `
      <div class="empty-orb"><svg><use href="#i-spark"/></svg></div>
      <h3>这台戏还没有分段</h3>
      <p>台词已就绪。按标点切分后，后端会用你的语速估算每段的秒数与帧数。</p>
      <div class="inline" style="justify-content:center">
        <button class="btn primary" type="button" data-action="plan"><svg><use href="#i-spark"/></svg>按台词分段</button>
      </div>`;
    return;
  }
  empty.innerHTML = `
    <div class="empty-orb"><svg><use href="#i-human"/></svg></div>
    <h3>先创建一个数字人作品</h3>
    <p>模式 2 用参考音频定音色，模式 3 用文字描述音色；台词会按标点与语速自动分段。</p>
    <ol>
      <li><b>01</b>在左栏选模式，填作品名称与台词。</li>
      <li><b>02</b>模式 2 上传 2–15 秒参考音频；模式 3 填写音色描述。</li>
      <li><b>03</b>点「新建作品」，再点「按台词分段」。</li>
    </ol>
    <div class="inline" style="justify-content:center">
      <button class="btn primary" type="button" data-action="focus-create"><svg><use href="#i-plus"/></svg>去填写作品设定</button>
    </div>`;
}

function renderProjectContext() {
  const p = state.project;
  const segments = p?.segments || [];
  $("#stage-name").textContent = p ? p.name || "未命名作品" : "从一句台词开始";
  $("#stage-sub").textContent = p
    ? `模式 ${p.mode} · ${MODE_LABEL[p.mode] || ""} · ${segments.length} 段 · 目标 ${num(p.target_seconds, 8).toFixed(1)}s / 最长 ${num(p.max_seconds, 12).toFixed(1)}s${p.final ? " · 已有成片" : ""}`
    : "选模式、写台词、传参考，剩下交给 H3 逐段原生生成";
  $("#project-state").textContent = p ? `已保存 · ${segments.length} 段` : "未创建";
  $("#form-hint").textContent = p
    ? segments.length
      ? "改动台词后点「按台词分段」重建分段；只改参数点「保存设定」。"
      : "点「按台词分段」，按标点与语速切段。"
    : "填写台词后新建作品，再点「按台词分段」。";
}

/* ---------------- 右栏：运行 / 成片 ---------------- */

function renderLogs(logs) {
  const box = $("#run-log");
  if (!box) return;
  if (!logs.length) {
    box.innerHTML = `<div class="av-log-empty">运行日志会显示在这里</div>`;
    return;
  }
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 28;
  box.innerHTML = logs
    .slice(-120)
    .map((log) => {
      const level = String(log.level || "").toLowerCase();
      const cls = level === "error" || level === "err" ? "error" : level === "warn" || level === "warning" ? "warn" : "";
      return `<div class="log-line ${cls}"><time>${esc(log.time || "")}</time><span>${esc(log.message || "")}</span></div>`;
    })
    .join("");
  if (atBottom) box.scrollTop = box.scrollHeight;
}

function renderRun(run) {
  state.run = run || null;
  const data = state.run || {};
  const status = data.status || "idle";
  const active = isRunActive(data);
  const percent = Math.round(Math.max(0, Math.min(1, num(data.progress))) * 100);
  const pill = $("#run-state");
  pill.textContent = RUN_LABEL[status] || status;
  pill.className = `pill ${
    status === "completed"
      ? "status-completed"
      : status === "failed"
        ? "status-failed"
        : status === "running"
          ? "status-running"
          : status === "cancelled"
            ? "status-cancelled"
            : ""
  }`;
  $("#run-stage").textContent = STAGE_LABEL[data.stage] || data.stage || (active ? "生成中" : "未开始");
  $("#run-percent").textContent = `${percent}%`;
  $("#progress-bar").style.width = `${percent}%`;
  $("#run-message").textContent = data.message || (active ? "正在逐段生成…" : "等待任务");
  const segment = Number(data.segment);
  $("#run-segment").textContent = Number.isFinite(segment) && segment >= 0 ? `第 ${segment + 1} 段` : "";
  renderLogs(Array.isArray(data.logs) ? data.logs : []);
  syncButtons();
}

function renderFinal() {
  const p = state.project;
  const final = p?.final || null;
  const frame = $("#final-frame");
  const info = $("#final-info");
  const stale = $("#final-stale");
  const duration = $("#final-duration");
  if (!frame) return;
  if (!final) {
    state.finalKey = "";
    frame.classList.remove("video-frame");
    frame.removeAttribute("style");
    frame.innerHTML = `<div class="final-empty av-final-empty"><svg><use href="#i-film"/></svg><span>合成完成后在此预览</span><em>逐段生成并选片后，点「合成成片」</em></div>`;
    info.classList.add("hidden");
    stale.classList.add("hidden");
    duration.textContent = "—";
    return;
  }
  const key = `${final.path}|${final.created || ""}|${final.bytes || ""}`;
  if (key !== state.finalKey) {
    state.finalKey = key;
    const videoPath = final.path || "final/final.mp4";
    const src = mediaUrl(p.id, videoPath, final.created || p.updated);
    const cover = final.thumb ? mediaUrl(p.id, final.thumb, final.created) : "";
    frame.classList.add("video-frame");
    frame.style.aspectRatio = aspectRatioCss(p.generation?.aspect_ratio || $("#f-aspect").value);
    frame.innerHTML = `<video id="final-video" controls playsinline preload="metadata"${cover ? ` poster="${esc(cover)}"` : ""} src="${esc(src)}"></video>`;
    const finalSegCount = Array.isArray(final.segments) ? final.segments.length : num(final.segments);
    info.innerHTML = `
      <div>时长<b>${fmtClock(final.duration)}</b></div>
      <div>段数<b>${finalSegCount} 段</b></div>
      <div>大小<b>${fmtBytes(final.bytes)}</b></div>
      <div>帧率<b>${final.fps ? `${num(final.fps)} fps` : "—"}</b></div>`;
    info.classList.remove("hidden");
    duration.textContent = fmtClock(final.duration);
  }
  const download = api.downloadUrl(p.id);
  $("#btn-download").href = download;
  $("#btn-download-top").href = download;
  stale.classList.toggle("hidden", !final.stale);
  if (final.stale) {
    $("#final-stale-text").textContent = "选片或分段已有变更，成片需要重新合成";
  }
}

/* ---------------- 参考图 ---------------- */

function renderRefs() {
  const p = state.project;
  const grid = $("#ref-grid");
  const emptyEl = $("#ref-empty");
  const refs = p?.refs || [];
  $("#ref-count").textContent = `${refs.length} 张`;
  emptyEl.classList.toggle("hidden", refs.length > 0);
  if (!refs.length) {
    grid.innerHTML = "";
    return;
  }
  grid.innerHTML = refs
    .map((ref, index) => {
      const role = ref.role && ROLE_LABEL[ref.role] ? ref.role : "other";
      const src = mediaUrl(p.id, `refs/${ref.filename}`, ref.added);
      const title = ref.title || ref.filename || "参考图";
      return `<div class="ref-card av-ref-card">
        <img src="${esc(src)}" alt="${esc(title)}" loading="lazy">
        <span class="av-ref-badge ${esc(role)}">${ROLE_LABEL[role]}</span>
        <div class="ref-title" title="${esc(title)}">${esc(title)}</div>
        <button class="ref-del" type="button" data-action="del-ref" data-index="${index}" title="删除参考图">
          <svg><use href="#i-close"/></svg>
        </button>
        <select class="ref-role-select" data-action="role-ref" data-index="${index}" title="修改用途">
          ${ROLE_ITEMS.map(([value, label]) => `<option value="${value}" ${value === role ? "selected" : ""}>${label}</option>`).join("")}
        </select>
      </div>`;
    })
    .join("");
}

async function uploadRefs(files) {
  const p = state.project;
  if (!p) {
    toast("请先新建作品，再上传参考图", "err");
    return;
  }
  const role = $("#ref-role").value;
  await withBusy($("#btn-ref-upload"), async () => {
    let latest = p;
    for (const file of files) {
      try {
        const title = file.name.replace(/\.[^.]+$/, "").slice(0, 40);
        latest = (await api.addRef(p.id, file, role, title, "")).project;
      } catch (err) {
        toast(`「${file.name}」上传失败：${err.message}`, "err");
      }
    }
    applyProject(latest, { fill: false, segments: false });
    toast(`参考图已更新（${(latest.refs || []).length} 张）`, "ok");
  }).catch((err) => toast(`参考图上传失败：${err.message}`, "err"));
}

async function deleteRef(index) {
  const p = state.project;
  if (!p) return;
  const ok = await confirmDialog("删除参考图", "删除后这一段设定与之后生成的提示词都不再包含它。继续？", "删除");
  if (!ok) return;
  try {
    const data = await api.deleteRef(p.id, index);
    applyProject(data.project, { fill: false, segments: false });
    toast("参考图已删除", "ok");
  } catch (err) {
    toast(`删除失败：${err.message}`, "err");
  }
}

async function changeRefRole(index, role) {
  const p = state.project;
  if (!p) return;
  try {
    const data = await api.patchRef(p.id, index, { role });
    applyProject(data.project, { fill: false, segments: false });
    toast(`参考图用途已改为「${ROLE_LABEL[role] || role}」`, "ok");
  } catch (err) {
    toast(`修改失败：${err.message}`, "err");
    renderRefs();
  }
}

/* ---------------- 分段操作 ---------------- */

async function generateSegmentPrompt(index) {
  const p = state.project;
  if (!p) return;
  await withBusy(null, async () => {
    const data = await api.segmentPrompt(p.id, index);
    state.project = data.project;
    renderSegments();
    renderProjectContext();
    updateSummary(data.project);
    syncButtons();
    const source = PROMPT_SOURCE_LABEL[data.source] || data.source || "内置模板";
    toast(`第 ${index + 1} 段提示词已生成（${source}）`, "ok");
    const warnings = data.warnings || [];
    if (warnings.length) {
      toast(
        `提示：${warnings.slice(0, 2).map((item) => (typeof item === "string" ? item : JSON.stringify(item))).join("；")}`,
        "info",
        6000
      );
    }
    const card = document.querySelector(`.av-seg[data-index="${index}"]`);
    const details = card?.querySelector(".av-seg-prompt");
    if (details) {
      details.open = true;
      details.querySelector(".av-prompt-area")?.focus();
    }
  }).catch((err) => toast(`提示词生成失败：${err.message}`, "err", 6000));
}

async function saveSegmentPrompt(index, value) {
  const p = state.project;
  if (!p) return;
  const seg = (p.segments || []).find((item) => item.index === index);
  if (seg && (seg.prompt || "") === value) {
    toast("提示词没有变化", "info", 2400);
    return;
  }
  await withBusy(null, async () => {
    const data = await api.patchSegment(p.id, index, { prompt: value });
    state.project = data.project;
    renderSegments();
    updateSummary(data.project);
    syncButtons();
    toast("提示词已保存", "ok");
  }).catch((err) => toast(`提示词保存失败：${err.message}`, "err"));
}

async function saveSegmentText(index, value) {
  const p = state.project;
  if (!p) return;
  const seg = (p.segments || []).find((item) => item.index === index);
  if (!seg || (seg.text || "") === value.trim()) return;
  try {
    const data = await api.patchSegment(p.id, index, { text: value });
    state.project = data.project;
    renderSegments();
    renderProjectContext();
    updateSummary(data.project);
    syncButtons();
  } catch (err) {
    toast(`台词保存失败：${err.message}`, "err", 6000);
  }
}

function updateSegmentBadges() {
  const p = state.project;
  if (!p) return;
  (p.segments || []).forEach((seg) => {
    const card = document.querySelector(`.av-seg[data-index="${seg.index}"]`);
    if (!card) return;
    card.classList.remove("idle", "running", "completed", "failed");
    card.classList.add(seg.status || "idle");
    const pill = card.querySelector(".seg-status");
    if (pill) {
      const [text, cls] = runStatusMeta(seg.status);
      pill.textContent = text;
      pill.className = `pill seg-status ${cls}`;
    }
  });
}

async function generateAllPrompts() {
  const p = state.project;
  if (!p) return toast("请先新建作品", "err");
  if (!(p.segments || []).length) return toast("请先按台词分段", "err");
  await withBusy($("#btn-prompts-all"), async () => {
    const data = await api.promptsAll(p.id);
    state.project = data.project;
    renderSegments();
    renderProjectContext();
    updateSummary(data.project);
    syncButtons();
    const warnings = data.warnings || [];
    toast(
      warnings.length ? `提示词已重写（${warnings.length} 条提示）` : "全部提示词已生成",
      warnings.length ? "info" : "ok",
      6000
    );
  }).catch((err) => toast(`提示词生成失败：${err.message}`, "err", 6000));
}

async function generateRun() {
  const p = state.project;
  if (!p) return toast("请先新建作品", "err");
  if (!(p.segments || []).length) return toast("请先按台词分段", "err");
  await withBusy($("#btn-generate"), async () => {
    const data = await api.generate(p.id, { force: false, generation: readGeneration() });
    state.run = data.run || state.run;
    if (state.project) state.project.run = state.run;
    renderRun(state.run);
    state.pollCount = 0;
    startPolling();
    toast("已开始逐段生成，耗时长，请保持页面打开", "info", 6000);
  }).catch((err) => toast(`启动生成失败：${err.message}`, "err", 7000));
}

async function regenerateSegment(index) {
  const p = state.project;
  if (!p) return;
  await withBusy(null, async () => {
    const data = await api.regenerate(p.id, index);
    state.run = data.run || state.run;
    if (state.project) state.project.run = state.run;
    renderRun(state.run);
    state.pollCount = 0;
    startPolling();
    toast(`第 ${index + 1} 段开始重新生成`, "info");
  }).catch((err) => toast(`重生成失败：${err.message}`, "err", 6000));
}

async function cancelRun() {
  const p = state.project;
  if (!p) return;
  await withBusy($("#btn-cancel"), async () => {
    const data = await api.cancel(p.id);
    state.run = data.run || state.run;
    if (state.project) state.project.run = state.run;
    renderRun(state.run);
    toast("已请求停止生成", "info");
  }).catch((err) => toast(`停止失败：${err.message}`, "err"));
}

async function selectTake(index, takeId) {
  const p = state.project;
  if (!p) return;
  const seg = (p.segments || []).find((item) => item.index === index);
  if (!seg || !takeId) return;
  if (seg.selected_take === takeId) {
    toast("这已经是当前选用的片段", "info", 2400);
    return;
  }
  try {
    const data = await api.selectTake(p.id, index, takeId);
    applyProject(data.project, { fill: false });
    toast("已选用这个片段，记得重新合成成片", "ok");
  } catch (err) {
    toast(`选片失败：${err.message}`, "err");
  }
}

async function deleteTake(index, takeId) {
  const p = state.project;
  if (!p) return;
  const ok = await confirmDialog("删除片段", "删除这个已生成的片段？该操作无法恢复。", "删除");
  if (!ok) return;
  try {
    const data = await api.deleteTake(p.id, index, takeId);
    applyProject(data.project, { fill: false });
    toast("片段已删除", "ok");
  } catch (err) {
    toast(`删除片段失败：${err.message}`, "err");
  }
}

async function assembleFilm() {
  const p = state.project;
  if (!p) return;
  if (!(p.segments || []).length) return toast("请先按台词分段并生成片段", "err");
  showBusy("正在用 ffmpeg 合成成片…");
  try {
    const data = await api.assemble(p.id);
    applyProject(data.project, { fill: false });
    await loadProjects();
    const warnings = data.warnings || [];
    if (warnings.length) {
      toast(`合成完成，${warnings.length} 条提示：${warnings.slice(0, 2).map((w) => (typeof w === "string" ? w : JSON.stringify(w))).join("；")}`, "warn", 8000);
    } else {
      toast("成片已合成", "ok");
    }
  } catch (err) {
    toast(`合成失败：${err.message}`, "err", 8000);
  } finally {
    hideBusy();
  }
}

/* ---------------- 轮询 ---------------- */

function startPolling() {
  if (state.pollTimer) return;
  state.pollCount = 0;
  state.pollTimer = window.setInterval(pollRun, 1200);
  pollRun();
}

function stopPolling() {
  if (state.pollTimer) {
    window.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

async function pollRun() {
  if (state.polling) return;
  const p = state.project;
  if (!p) {
    stopPolling();
    return;
  }
  state.polling = true;
  try {
    const data = await api.run(p.id);
    state.run = data.run || null;
    if (state.project) state.project.run = state.run;
    renderRun(state.run);
    const status = state.run?.status || "idle";
    state.pollCount += 1;
    if (RUN_TERMINAL.has(status)) {
      stopPolling();
      await refreshProject({ refreshList: true });
    } else if (status === "idle") {
      if (state.pollCount >= 3) {
        stopPolling();
        await refreshProject({ refreshList: true });
      }
    } else if (state.pollCount % 3 === 0) {
      await refreshProject();
    }
  } catch (err) {
    stopPolling();
    toast(`获取运行状态失败：${err.message}`, "err", 6000);
  } finally {
    state.polling = false;
  }
}

function isEditingStage() {
  const el = document.activeElement;
  return !!(
    el &&
    el.closest &&
    el.closest("#seg-list") &&
    (el.tagName === "TEXTAREA" || el.tagName === "INPUT" || el.tagName === "SELECT")
  );
}

async function refreshProject({ refreshList = false } = {}) {
  const p = state.project;
  if (!p) return;
  try {
    const data = await api.project(p.id);
    const project = data.project;
    state.project = project;
    if (project.run !== undefined && project.run !== null) state.run = project.run;
    renderProjectContext();
    renderVoice();
    renderRefs();
    if (isEditingStage()) updateSegmentBadges();
    else renderSegments();
    renderRun(state.run);
    renderFinal();
    if (refreshList) await loadProjects();
    else updateSummary(project);
    syncButtons();
  } catch {
    /* 轮询中的项目刷新失败不打断主流程 */
  }
}

/* ---------------- 主渲染入口 ---------------- */

function applyProject(project, { fill = false, segments = true } = {}) {
  if (!project) return;
  state.project = project;
  if (project.run !== undefined && project.run !== null) state.run = project.run;
  if (fill) fillForm(project);
  renderProjectContext();
  renderVoice();
  renderRefs();
  if (segments) renderSegments();
  renderRun(state.run);
  renderFinal();
  updateSummary(project);
  syncButtons();
  if (isRunActive(state.run)) startPolling();
  else stopPolling();
}

function syncButtons() {
  const p = state.project;
  const segments = p?.segments || [];
  const hasProject = !!p;
  const hasSegments = segments.length > 0;
  const active = isRunActive(state.run);
  const hasFinal = !!p?.final;

  const setDisabled = (selector, disabled) => {
    const el = $(selector);
    if (el) el.disabled = !!disabled;
  };

  setDisabled("#btn-plan", !hasProject);
  setDisabled("#btn-plan2", !hasProject);
  setDisabled("#btn-prompts-all", !hasSegments);
  setDisabled("#btn-generate", !hasSegments || active);
  setDisabled("#btn-assemble", !hasSegments);
  setDisabled("#btn-assemble2", !hasSegments);
  setDisabled("#btn-cancel", !active);
  setDisabled("#btn-ref-upload", !hasProject);
  setDisabled("#ref-role", !hasProject);

  const cancel = $("#btn-cancel");
  if (cancel) cancel.classList.toggle("hidden", !active);

  $("#btn-create-label").textContent = hasProject ? "保存设定" : "新建作品";

  const download = $("#btn-download");
  const downloadTop = $("#btn-download-top");
  if (download) download.classList.toggle("hidden", !hasFinal);
  if (downloadTop) {
    downloadTop.classList.remove("hidden");
    downloadTop.classList.toggle("is-disabled", !hasFinal);
    downloadTop.setAttribute("aria-disabled", String(!hasFinal));
    downloadTop.title = hasFinal ? "下载成片 MP4" : "合成成片后可下载";
  }

  const generate = $("#btn-generate");
  if (generate) {
    const refs = p?.refs || [];
    generate.title = !hasSegments
      ? "先按台词分段"
      : refs.length
        ? "按分段逐段生成视频（耗时长）"
        : "生成需要至少一张参考图（人物）";
  }
}

/* ---------------- 事件绑定 ---------------- */

function bindEvents() {
  $("#btn-refresh").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    await withBusy(button, async () => {
      await Promise.allSettled([loadHealth(), loadOptions(), loadProjects()]);
      toast("系统状态已刷新", "ok", 2600);
    });
  });

  $$("[data-mode-card]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const next = Number(btn.dataset.modeCard);
      if (state.mode === next) return;
      setMode(next);
    });
  });

  $("#f-script").addEventListener("input", updateScriptCount);
  $("#f-cps").addEventListener("input", () => {
    const value = num($("#f-cps").value, 5);
    if (value < 1 || value > 12) $("#f-cps").value = String(Math.min(12, Math.max(1, value || 5)));
  });
  $("#f-target").addEventListener("input", () => clampRanges("target"));
  $("#f-max").addEventListener("input", () => clampRanges("max"));

  $("#btn-create").addEventListener("click", () => (state.project ? saveSettings() : createProject()));
  $("#btn-new").addEventListener("click", () => clearProject());
  $("#btn-plan").addEventListener("click", () => planSegments());
  $("#btn-plan2").addEventListener("click", () => planSegments());
  $("#btn-prompts-all").addEventListener("click", generateAllPrompts);
  $("#btn-generate").addEventListener("click", generateRun);
  $("#btn-cancel").addEventListener("click", cancelRun);
  $("#btn-assemble").addEventListener("click", assembleFilm);
  $("#btn-assemble2").addEventListener("click", assembleFilm);
  $("#btn-save-prompts").addEventListener("click", savePrompts);

  $("#voice-input").addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) await handleVoiceFile(file);
  });
  $("#btn-voice-replace").addEventListener("click", () => $("#voice-input").click());
  $("#btn-voice-del").addEventListener("click", deleteVoice);

  const drop = $("#voice-drop");
  ["dragenter", "dragover"].forEach((name) =>
    drop.addEventListener(name, (event) => {
      event.preventDefault();
      drop.classList.add("drag");
    })
  );
  ["dragleave", "drop"].forEach((name) =>
    drop.addEventListener(name, (event) => {
      event.preventDefault();
      drop.classList.remove("drag");
    })
  );
  drop.addEventListener("drop", async (event) => {
    const file = event.dataTransfer?.files?.[0];
    if (file) await handleVoiceFile(file);
  });

  $("#btn-ref-upload").addEventListener("click", () => $("#ref-input").click());
  $("#ref-input").addEventListener("change", async (event) => {
    const files = [...(event.target.files || [])];
    event.target.value = "";
    if (files.length) await uploadRefs(files);
  });

  $("#ref-grid").addEventListener("click", (event) => {
    const el = event.target.closest("[data-action]");
    if (!el) return;
    if (el.dataset.action === "del-ref") deleteRef(Number(el.dataset.index));
  });
  $("#ref-grid").addEventListener("change", (event) => {
    const el = event.target.closest('[data-action="role-ref"]');
    if (el) changeRefRole(Number(el.dataset.index), el.value);
  });

  $("#project-list").addEventListener("click", (event) => {
    const el = event.target.closest("[data-action]");
    if (!el) return;
    const id = el.dataset.id;
    if (!id) return;
    if (el.dataset.action === "open-project") {
      if (state.project?.id !== id) openProject(id);
    } else if (el.dataset.action === "del-project") {
      const item = state.projects.find((row) => row.id === id);
      removeProject(id, item?.name || id);
    }
  });
  $("#project-list").addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const el = event.target.closest('[data-action="open-project"]');
    if (!el) return;
    event.preventDefault();
    openProject(el.dataset.id);
  });

  $(".av-stage").addEventListener("click", (event) => {
    const el = event.target.closest("[data-action]");
    if (!el) return;
    const action = el.dataset.action;
    const index = el.dataset.index !== undefined ? Number(el.dataset.index) : null;
    if (action === "focus-create") {
      $("#f-name").focus();
      $("#f-name").scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    if (action === "plan") {
      planSegments();
      return;
    }
    if (action === "seg-prompt") generateSegmentPrompt(index);
    else if (action === "seg-regen") regenerateSegment(index);
    else if (action === "save-prompt") {
      const card = el.closest(".av-seg");
      const area = card?.querySelector(".av-prompt-area");
      if (area) saveSegmentPrompt(index, area.value);
    } else if (action === "take-select") {
      const takeId = el.dataset.take;
      if (takeId) selectTake(index, takeId);
    } else if (action === "take-del") {
      const takeId = el.dataset.take;
      if (takeId) deleteTake(index, takeId);
    }
  });

  $("#seg-list").addEventListener("focusout", (event) => {
    const el = event.target;
    if (el.matches?.('textarea[data-action="seg-text"]')) {
      saveSegmentText(Number(el.dataset.index), el.value);
    }
  });

  $("#seg-list").addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      const el = event.target.closest?.('textarea[data-action="seg-text"]');
      if (el) el.blur();
      return;
    }
    if (event.key !== "Enter" && event.key !== " ") return;
    const take = event.target.closest?.('[data-action="take-select"]');
    if (take) {
      event.preventDefault();
      selectTake(Number(take.dataset.index), take.dataset.take);
    }
  });

  window.addEventListener("beforeunload", stopPolling);
}

/* ---------------- 启动 ---------------- */

async function boot() {
  bindEvents();
  setMode(2, { quiet: true });
  renderSegments();
  renderRun(null);
  renderFinal();
  renderRefs();
  renderProjectList();
  syncButtons();
  updateScriptCount();
  updateRangeLabels();

  const lastId = localStorage.getItem(LAST_KEY);
  await Promise.allSettled([loadHealth(), loadOptions(), loadPrompts(), loadProjects()]);

  if (!state.project) fillGeneration(state.generationDefaults || {});
  if (lastId) await openProject(lastId);
  if (!state.project) {
    renderSegments();
    renderRun(null);
    renderFinal();
    renderRefs();
    syncButtons();
  }
  if (!state.backendReady) {
    toast("后端 /avatar/api 暂未就绪，功能会在后端启动后可用", "err", 9000);
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
