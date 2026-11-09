/* ============================================================
 * H3 DIGITAL HUMAN · 数字人工作室
 * 原生 ES Module，零构建、无第三方依赖、不 import MV 工作台模块。
 * 后端契约：/avatar/api/*，所有 JSON 响应 ok:true。
 * 功能：模式 1/2/3、音频波形时间线（切点拖拽/播放头切分/合并）、
 *       设计面板（密度/六件套/安全区/voice card/AI 建议）、
 *       每段节拍表、提示词摘要与 lint 面板、叠加模式提示。
 * ============================================================ */

import { Waveform } from "./waveform.js";

const API = "/avatar/api";
const LAST_KEY = "h3avatar:lastProject";
const FPS = 24;
const RUN_ACTIVE = new Set(["queued", "running"]);
const RUN_TERMINAL = new Set(["completed", "failed", "cancelled"]);

let waveform = null;

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
const PROMPT_SOURCE_LABEL = {
  llm: "LLM",
  fallback: "内置模板",
  manual: "手动编辑",
  repair: "lint 修复",
};
const ROLE_ITEMS = [
  ["performer", "人物"],
  ["environment", "场景"],
  ["appearance", "服装 / 造型"],
  ["subject", "道具 / 主体"],
  ["other", "其他"],
];
const ROLE_LABEL = Object.fromEntries(ROLE_ITEMS);

const DENSITY_LABEL = { low: "低", medium: "中", high: "高" };
const PLATFORM_LABEL = { generic: "通用", douyin: "抖音", bilibili: "B 站", xiaohongshu: "小红书" };
const GRAPHICS_MODE_LABEL = { generated: "生成式", overlay: "后期叠加" };
const AUDIO_MODE_LABEL = { runtime: "运行时驱动音频", reference: "参考音色", generated: "原生生成语音" };
const AUDIO_MODE_BY_MODE = { 1: "runtime", 2: "reference", 3: "generated" };

const BEAT_JOBS = [
  "hook", "definition", "number", "list", "steps", "comparison", "cause",
  "benefit", "warning", "proof", "location", "time", "technology", "finance",
  "education", "emotion", "cta",
];
const BEAT_EFFECTS = [
  "info_card", "headline", "lower_third", "stepper", "chart", "icon",
  "hud", "particle", "quote", "map", "cta_card",
];
const BEAT_ANCHORS = ["right_side", "lower_third", "beside_speaker", "top_left", "world_space", "surface"];
const BEAT_LAYERS = ["screen", "world", "surface", "camera"];
const ATTENTION_TARGETS = [
  ["lens", "镜头 lens"],
  ["world_space", "世界空间 world_space"],
];
const VOICE_CARD_FIELDS = [
  ["presentation", "呈现（身份 / 性别感 / 年龄感）", "例如：三十岁出头的女性，知性、克制", true],
  ["age_band", "年龄区间", "例如：28–35 岁", false],
  ["pitch", "音高", "例如：中低音区，尾音略下沉", false],
  ["resonance", "共鸣", "例如：胸腔共鸣为主，口腔靠后", false],
  ["texture", "质感", "例如：微沙哑 + 轻微气声，近场", false],
  ["articulation", "咬字", "例如：普通话标准，字头清晰不咬死", false],
  ["pace_phrasing", "节奏 / 断句", "例如：4.8 字/秒，逗号短停，句尾留白", false],
  ["intonation", "语调", "例如：陈述句尾音下压，问句轻微上挑", false],
  ["emotional_stance", "情绪姿态", "例如：可信、耐心，带一点现场感", false],
  ["capture_perspective", "拾音 / 话筒距离", "例如：近场电容麦，18cm，房间极安静", false],
  ["anti_style", "反风格", "例如：不要播音腔、不要甜腻、不要广告腔", true],
];

const FALLBACK_EFFECT_FAMILIES = [
  { id: "info_cards", label: "信息卡片", hint: "标题 + 要点，屏幕层" },
  { id: "kinetic_type", label: "动态文字", hint: "大字 / 关键词强调" },
  { id: "data_viz", label: "数据图表", hint: "柱状 / 折线 / 环形" },
  { id: "stepper_flow", label: "步骤流程", hint: "01 → 02 → 03 流程" },
  { id: "icon_system", label: "图标系统", hint: "线性图标点缀" },
  { id: "hud_panels", label: "HUD 面板", hint: "科技感边框 / 读数" },
  { id: "particles", label: "粒子光效", hint: "粒子 / 光斑" },
  { id: "quote_cards", label: "引用卡片", hint: "名言 / 引用" },
  { id: "map_layer", label: "地图层", hint: "地理定位" },
  { id: "lower_thirds", label: "下三分之一", hint: "人名 / 身份条" },
  { id: "cta_cards", label: "行动号召卡", hint: "结尾 CTA" },
  { id: "highlight_frames", label: "重点框", hint: "圈选 / 高亮" },
];

const FALLBACK_STYLE_SYSTEMS = [
  {
    id: "tech_amber", label: "暗调科技 · 琥珀",
    typography: "中文黑体 + 数字等宽", material: "磨砂玻璃 + 细描边",
    icon_family: "线性细描边图标", motion_grammar: "左下 35° 滑入，落定缓停，先入后出",
    accent_color: "#FFB454", secondary_color: "#8AB4F8",
    visual_style: "暗调科技纪录片，冷灰背景 + 琥珀高光",
  },
  {
    id: "minimal_paper", label: "极简纸感 · 黑白",
    typography: "思源宋体标题 + 黑体正文", material: "纸纹卡片 + 硬投影",
    icon_family: "实心几何图标", motion_grammar: "淡入淡出 + 轻微上移",
    accent_color: "#F5F1E8", secondary_color: "#94A3B8",
    visual_style: "极简纸感，低饱和，留白充足",
  },
  {
    id: "neon_cyber", label: "赛博霓虹",
    typography: "中文粗黑 + 等宽数字", material: "半透明霓虹框 + 扫描线",
    icon_family: "HUD 线性图标", motion_grammar: "故障闪入 + 扫描停留",
    accent_color: "#5EEAD4", secondary_color: "#A78BFA",
    visual_style: "赛博暗紫，霓虹描边，高对比",
  },
  {
    id: "warm_film", label: "暖调胶片",
    typography: "圆体标题 + 宋体正文", material: "胶片颗粒 + 柔光底框",
    icon_family: "手绘描边图标", motion_grammar: "缓慢推入 + 柔和位移",
    accent_color: "#FFB454", secondary_color: "#F87171",
    visual_style: "暖调胶片，柔和颗粒，纪录片质感",
  },
  {
    id: "clean_ui", label: "清爽 UI",
    typography: "无衬线黑体 + 等宽数字", material: "纯白卡片 + 轻阴影",
    icon_family: "圆角填充图标", motion_grammar: "弹性缩放入场，快速退场",
    accent_color: "#8AB4F8", secondary_color: "#34D399",
    visual_style: "明亮清爽的 UI 记录感，信息密度适中",
  },
];

const DESIGN_DEFAULT = {
  route: "Ref2VA",
  audio_mode: "generated",
  effect_density: "medium",
  platform: "generic",
  safe_area: { top: 0.08, bottom: 0.12, left: 0.06, right: 0.06 },
  style_system: {
    typography: "",
    material: "",
    icon_family: "",
    motion_grammar: "",
    accent_color: "#FFB454",
    secondary_color: "#8AB4F8",
    visual_style: "",
  },
  voice_card: {
    presentation: "", age_band: "", pitch: "", resonance: "", texture: "",
    articulation: "", pace_phrasing: "", intonation: "", emotional_stance: "",
    capture_perspective: "", anti_style: "",
  },
  graphics_budget: { max_groups: 1, max_flashes_per_sec: 3, prompt_chars_target: 7000 },
  graphics_mode: "generated",
  brand: { palette: [], typography_note: "", logo_refs: [] },
  effect_families: [],
};

const state = {
  health: null,
  options: {},
  generationDefaults: {},
  anchorPlain: {},
  prompts: null,
  projects: [],
  project: null,
  run: null,
  mode: 2,
  pendingVoice: null,
  pendingAudio: null,
  pollTimer: null,
  pollCount: 0,
  polling: false,
  finalKey: "",
  backendReady: false,
  busy: 0,
  catalog: null,
  designDefaults: null,
  designDensity: "medium",
  designGraphicsMode: "generated",
  designDirty: false,
  designLoadedFor: "",
  playhead: 0,
  draggingPlayhead: false,
  cuts: [],
  audioFailed: false,
  beatsDraft: {},
  segTabs: {},
  lintOverride: {},
  mention: { items: [], active: 0, target: null, onPick: null, trimAt: false },
  generatingPrompt: {},
  promptTicker: null,
  auditionIndex: null,
  auditionEnd: 0,
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

const num = (value, fallback = 0) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
};

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

function pad(n, len = 2) {
  return String(n).padStart(len, "0");
}

function fmtWhen(value) {
  if (!value) return "—";
  const raw = typeof value === "number" && value < 1e12 ? value * 1000 : value;
  const d = new Date(raw);
  if (!Number.isNaN(d.getTime())) {
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  return String(value);
}

function fmtSec(value) {
  return `${num(value).toFixed(1)}s`;
}

function fmtClock(value) {
  // 先量化到 0.1 秒再拆分钟，避免 59.96s 显示成 00:60.0
  const total = Math.round(Math.max(0, num(value)) * 10) / 10;
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

function mediaAudioUrl(project) {
  const audio = project?.audio;
  if (!project?.id || !audio?.filename) return "";
  return mediaUrl(project.id, audio.filename, audio.uploaded || project.updated);
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

function deepClone(value) {
  if (value === null || value === undefined) return value;
  try {
    return JSON.parse(JSON.stringify(value));
  } catch {
    return value;
  }
}

function isPlainObject(value) {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function deepMerge(base, patch) {
  const source = deepClone(base);
  if (!isPlainObject(source) || !isPlainObject(patch)) return patch === undefined ? source : deepClone(patch);
  Object.entries(patch).forEach(([key, value]) => {
    if (isPlainObject(value) && isPlainObject(source[key])) source[key] = deepMerge(source[key], value);
    else source[key] = deepClone(value);
  });
  return source;
}

function setByPath(target, path, value) {
  const parts = String(path).split(".");
  let cursor = target;
  parts.forEach((part, i) => {
    if (i === parts.length - 1) {
      cursor[part] = value;
      return;
    }
    if (!isPlainObject(cursor[part])) cursor[part] = {};
    cursor = cursor[part];
  });
}

function splitList(value) {
  return String(value || "")
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function safeColor(value, fallback) {
  const text = String(value || "").trim();
  if (/^#[0-9a-fA-F]{6}$/.test(text)) return text;
  if (/^#[0-9a-fA-F]{3}$/.test(text)) return `#${text[1]}${text[1]}${text[2]}${text[2]}${text[3]}${text[3]}`;
  return fallback;
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

function normalizeOptions(items, fallback = []) {
  const out = [];
  const push = (value, label) => {
    if (value === null || value === undefined || value === "") return;
    const key = String(value);
    if (out.some((item) => item.value === key)) return;
    out.push({ value: key, label: String(label || key) });
  };
  (Array.isArray(items) ? items : []).forEach((item) => {
    if (item === null || item === undefined) return;
    if (typeof item === "string" || typeof item === "number") push(item, item);
    else push(item.id ?? item.value ?? item.key ?? item.name, item.label ?? item.title ?? item.name ?? item.id);
  });
  if (!out.length) (Array.isArray(fallback) ? fallback : []).forEach((item) => {
    if (Array.isArray(item)) push(item[0], item[1]);
    else push(item, item);
  });
  return out;
}

function selectHtml(options, value, { placeholder = "" } = {}) {
  const current = value === null || value === undefined ? "" : String(value);
  const list = Array.isArray(options) ? options : [];
  const hasCurrent = list.some((item) => item.value === current);
  const head = placeholder
    ? `<option value="" ${current ? "" : "selected"}>${esc(placeholder)}</option>`
    : "";
  const body = list
    .map((item) => `<option value="${esc(item.value)}" ${item.value === current ? "selected" : ""}>${esc(item.label)}</option>`)
    .join("");
  if (current && !hasCurrent && !placeholder) {
    return `<option value="${esc(current)}" selected>${esc(current)}</option>${body}`;
  }
  return head + body;
}

function itemList(key) {
  const list = state.options?.[key];
  return Array.isArray(list) ? list : [];
}

function debounce(fn, wait = 160) {
  let timer = 0;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => fn(...args), wait);
  };
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

const projectPath = (id) => `${API}/projects/${encodeURIComponent(id)}`;

const api = {
  health: () => request(`${API}/health`),
  options: () => request(`${API}/options`),
  prompts: () => request(`${API}/prompts`),
  savePrompts: (body) => request(`${API}/prompts`, { method: "PATCH", body }),
  effectCatalog: () => request(`${API}/effect-catalog`),
  designDefaults: () => request(`${API}/design-defaults`),
  projects: () => request(`${API}/projects`),
  createProject: (fields, file) =>
    request(`${API}/projects`, { method: "POST", form: formData(fields, "voice", file) }),
  project: (id) => request(projectPath(id)),
  patchProject: (id, body) => request(projectPath(id), { method: "PATCH", body }),
  deleteProject: (id) => request(projectPath(id), { method: "DELETE" }),
  uploadVoice: (id, file) =>
    request(`${projectPath(id)}/voice`, { method: "POST", form: formData({}, "voice", file) }),
  addVoice: (id, file, name = "") =>
    request(`${projectPath(id)}/voices`, { method: "POST", form: formData({ name }, "voice", file) }),
  patchVoice: (id, voiceId, name) =>
    request(`${projectPath(id)}/voices/${voiceId}`, { method: "PATCH", body: { name } }),
  deleteVoiceItem: (id, voiceId) =>
    request(`${projectPath(id)}/voices/${voiceId}`, { method: "DELETE" }),
  voiceSample: (id, body) =>
    request(`${projectPath(id)}/voice/sample`, { method: "POST", body }),
  deleteVoice: (id) => request(`${projectPath(id)}/voice`, { method: "DELETE" }),
  uploadAudio: (id, file) =>
    request(`${projectPath(id)}/audio`, { method: "POST", form: formData({}, "audio", file) }),
  deleteAudio: (id) => request(`${projectPath(id)}/audio`, { method: "DELETE" }),
  analyze: (id, body) => request(`${projectPath(id)}/analyze`, { method: "POST", body }),
  cuts: (id, cuts) => request(`${projectPath(id)}/cuts`, { method: "PUT", body: { cuts } }),
  splitSegment: (id, index, time) =>
    request(`${projectPath(id)}/segments/${index}/split`, { method: "POST", body: { time } }),
  mergeSegment: (id, index) =>
    request(`${projectPath(id)}/segments/${index}/merge`, { method: "POST", body: {} }),
  addRef: (id, file, role, title, description) =>
    request(`${projectPath(id)}/refs`, {
      method: "POST",
      form: formData({ role, title, description }, "image", file),
    }),
  patchRef: (id, index, body) => request(`${projectPath(id)}/refs/${index}`, { method: "PATCH", body }),
  deleteRef: (id, index) => request(`${projectPath(id)}/refs/${index}`, { method: "DELETE" }),
  plan: (id, body) => request(`${projectPath(id)}/plan`, { method: "POST", body }),
  patchSegment: (id, index, body) =>
    request(`${projectPath(id)}/segments/${index}`, { method: "PATCH", body }),
  segmentPrompt: (id, index, body = {}) =>
    request(`${projectPath(id)}/segments/${index}/prompt`, { method: "POST", body }),
  promptsAll: (id) => request(`${projectPath(id)}/prompts`, { method: "POST", body: {} }),
  lintPrompt: (id, index, text) =>
    request(`${projectPath(id)}/segments/${index}/prompt/lint`, { method: "POST", body: { text } }),
  repairPrompt: (id, index) =>
    request(`${projectPath(id)}/segments/${index}/prompt/repair`, { method: "POST", body: {} }),
  segmentBeats: (id, index, beats) =>
    request(`${projectPath(id)}/segments/${index}/beats`, { method: "POST", body: { beats } }),
  design: (id) => request(`${projectPath(id)}/design`),
  patchDesign: (id, body) => request(`${projectPath(id)}/design`, { method: "PATCH", body }),
  suggestDesign: (id, body) => request(`${projectPath(id)}/design/suggest`, { method: "POST", body }),
  generate: (id, body) => request(`${projectPath(id)}/generate`, { method: "POST", body }),
  regenerate: (id, index) =>
    request(`${projectPath(id)}/segments/${index}/regenerate`, { method: "POST", body: {} }),
  cancel: (id) => request(`${projectPath(id)}/cancel`, { method: "POST", body: {} }),
  run: (id) => request(`${projectPath(id)}/run`),
  selectTake: (id, index, takeId) =>
    request(`${projectPath(id)}/segments/${index}/takes/${encodeURIComponent(takeId)}/select`, {
      method: "POST",
      body: {},
    }),
  deleteTake: (id, index, takeId) =>
    request(`${projectPath(id)}/segments/${index}/takes/${encodeURIComponent(takeId)}`, { method: "DELETE" }),
  assemble: (id) => request(`${projectPath(id)}/assemble`, { method: "POST", body: {} }),
  downloadUrl: (id) => `${projectPath(id)}/download`,
  exportUrl: (id) => `${projectPath(id)}/export`,
  importProject: (file) =>
    request(`${API}/projects/import`, { method: "POST", form: formData({}, "package", file) }),
};

/* ---------------- 表单读写 ---------------- */

function readForm() {
  let cps = num($("#f-cps").value, 5);
  cps = clamp(cps, 1, 12);
  let target = clamp(num($("#f-target").value, 8), 5, 30);
  let max = clamp(num($("#f-max").value, 12), 5, 30);
  if (max < target) max = target;
  return {
    name: $("#f-name").value.trim(),
    mode: state.mode,
    script: $("#f-script").value.replace(/\s+$/g, "").replace(/^\s+/g, ""),
    voice_description: $("#f-voice-desc").value.trim(),
    material_note: $("#f-material-note")?.value.trim() || "",
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

  gen.stitch = $("#f-stitch")?.value || g.stitch || "hard";
  gen.anchor_ref = "first";
  gen.anchor_seed = gen.seed;
  if (gen.stitch === "anchor") {
    gen.anchor_mid = $("#f-anchor-mid")?.value || "center";
    gen.anchor_mid_frames = String($("#f-anchor-mid-frames")?.value || "")
      .split(",").map((value) => Number(value.trim()))
      .filter((value) => Number.isFinite(value) && value > 0);
    gen.anchor_preprocess = $("#f-anchor-preprocess")?.value || "center_crop";
    gen.anchor_sampler = $("#f-anchor-sampler")?.value || "plain";
    if (gen.anchor_sampler === "selflift") gen.anchor_mid = "none";
    gen.anchor_upscale = $("#f-anchor-upscale")?.value || "lanczos";
    gen.anchor_pin_frames = ($("#f-anchor-pin")?.value || "false") === "true";
    gen.lowres_scale = Number($("#f-anchor-lowres")?.value || 0.5);
    const tiling = $("#f-anchor-tiling")?.value || "auto";
    gen.tiling_mode = tiling === "auto" ? "auto" : "manual";
    gen.tiling_tiles = tiling === "auto" ? 2 : Number(tiling);
  }
  return gen;
}

function fillGeneration(gen) {
  const g = gen || {};
  setSelectValue($("#f-aspect"), g.aspect_ratio || "9:16");
  setSelectValue($("#f-mp"), g.megapixels !== undefined && g.megapixels !== null ? String(Number(g.megapixels)) : "1");
  $("#f-seed").value = (g.stitch === "anchor" ? (g.anchor_seed ?? g.seed) : g.seed) ?? 0;
  setSelectValue($("#a-unet"), g.unet);
  setSelectValue($("#a-clip"), g.clip);
  setSelectValue($("#a-vae"), g.vae_video);
  setSelectValue($("#a-lora"), g.lora);
  setSelectValue($("#a-sampler"), g.sampler);
  setSelectValue($("#a-scheduler"), g.scheduler);
  $("#a-steps").value = g.steps ?? "";
  $("#a-cfg").value = g.cfg ?? "";
  $("#a-lora-strength").value = g.lora_strength ?? "";

  const stitch = g.stitch || "hard";
  if ($("#f-stitch")) $("#f-stitch").value = stitch;
  setSegmented("f-stitch-seg", stitch);
  const mid = g.anchor_mid || "center";
  if ($("#f-anchor-mid")) $("#f-anchor-mid").value = mid;
  setSegmented("f-anchor-mid-seg", mid);
  setSelectValue($("#f-anchor-preprocess"), g.anchor_preprocess || "center_crop");
  setSelectValue($("#f-anchor-sampler"), g.anchor_sampler || "plain");
  setSelectValue($("#f-anchor-upscale"), g.anchor_upscale || "lanczos");
  const pin = g.anchor_pin_frames === undefined ? false : !!g.anchor_pin_frames;
  if ($("#f-anchor-pin")) $("#f-anchor-pin").value = pin ? "true" : "false";
  setSegmented("f-anchor-pin-seg", pin ? "true" : "false");
  setSelectValue($("#f-anchor-lowres"), String(g.lowres_scale ?? 0.5));
  setSelectValue($("#f-anchor-tiling"),
    g.tiling_mode === "manual" ? String(g.tiling_tiles || 2) : "auto");
  if ($("#f-anchor-mid-frames")) {
    $("#f-anchor-mid-frames").value = (g.anchor_mid_frames || []).join(",");
  }
  syncAnchorUI();
}

function setSegmented(id, value) {
  const root = document.getElementById(id);
  if (!root) return;
  root.querySelectorAll("[data-value]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.value === value);
  });
}

function applyAnchorModelStack(sampler) {
  const plain = sampler === "plain";
  const saved = state.project?.generation || {};
  const fallback = state.generationDefaults || {};
  const preset = state.anchorPlain || {};
  const pick = (key) => {
    if (plain) return preset[key];
    return saved[key] !== undefined && saved[key] !== null ? saved[key] : fallback[key];
  };
  setSelectValue($("#a-unet"), pick("unet"));
  setSelectValue($("#a-clip"), pick("clip"));
  setSelectValue($("#a-vae"), pick("vae_video"));
  setSelectValue($("#a-lora"), pick("lora"));
  const strength = pick("lora_strength");
  if (strength !== undefined && strength !== null && strength !== "") {
    $("#a-lora-strength").value = strength;
  }
  setSelectValue($("#f-anchor-preprocess"), pick("anchor_preprocess") || "center_crop");
}

function syncAnchorUI() {
  const stitchInput = $("#f-stitch");
  if (!stitchInput) return;
  const mode = Number(state.mode) || 2;
  const anchorAvailable = mode === 1;
  if (stitchInput.value === "anchor" && !anchorAvailable) {
    stitchInput.value = "hard";
    setSegmented("f-stitch-seg", "hard");
  }
  const on = stitchInput.value === "anchor" && anchorAvailable;
  const fields = $("#anchor-fields");
  if (fields) fields.hidden = !on;
  const hint = $("#anchor-hint");
  if (hint) hint.hidden = !on;
  const note = $("#anchor-mode-note");
  if (note) note.textContent = anchorAvailable ? "仅模式 1 · 固定种子" : "仅模式 1 可用";
  const seedField = $("#f-seed")?.closest(".field");
  const seedLabel = seedField?.querySelector("label");
  if (seedLabel) {
    const textNode = [...seedLabel.childNodes].find((node) => node.nodeType === 3);
    if (textNode) textNode.textContent = on ? "锚定种子 " : "随机种子 ";
  }
  const seedHint = seedField?.querySelector(".hint");
  if (seedHint) seedHint.textContent = on ? "0 = 项目固定种子" : "0 = 每段随机";
  const wfTitle = $("#wf-title");
  const wfTemplate = $("#wf-template");
  const wfDesc = $("#wf-desc");
  if (wfTitle) wfTitle.textContent = on ? "首尾帧 · FL2VA 首尾同图" : "Ref 模式 · 锁定音轨";
  if (wfTemplate) wfTemplate.textContent = on ? "提示词：FL2VA 固定模板" : "提示词：Ref2VA 六段";
  if (wfDesc) {
    wfDesc.textContent = on
      ? "参数：参考图（首尾同图）+ 段音频 + 固定种子 → FL2VA 工作流"
      : "参数：驱动音频 + 参考图 + 每段随机种子 → 锁定音轨工作流（SelfLift 双采）";
  }
  const allLabel = $("#btn-prompts-all-label");
  if (allLabel) allLabel.textContent = on ? "一键填模板（可选）" : "一键润色（可选）";
  const allBtn = $("#btn-prompts-all");
  if (allBtn) {
    allBtn.title = on
      ? "可选：为每段写入首尾帧固定模板（不调用 LLM）"
      : "可选：为每段润色提示词（运行不会自动润色）";
  }
  const anchorBtn = document.querySelector('#f-stitch-seg [data-value="anchor"]');
  if (anchorBtn) {
    anchorBtn.disabled = !anchorAvailable;
    anchorBtn.classList.toggle("is-disabled", !anchorAvailable);
  }
  const sampler = $("#f-anchor-sampler")?.value || "plain";
  const selflift = sampler === "selflift";
  if (on) {
    applyAnchorModelStack(sampler);
    if (wfDesc) {
      wfDesc.textContent += sampler === "plain"
        ? " · 模型：Cobijada + 官方 dmad@0.8 单采栈（参考图原图直进）"
        : " · 模型：SelfLift 双采栈";
    }
  }
  if (selflift) {
    if ($("#f-anchor-mid")) $("#f-anchor-mid").value = "none";
    setSegmented("f-anchor-mid-seg", "none");
  }
  document.querySelectorAll("#f-anchor-mid-seg [data-value]").forEach((btn) => {
    btn.disabled = selflift;
    btn.classList.toggle("is-disabled", selflift);
  });
  const mid = $("#f-anchor-mid")?.value || "center";
  const customWrap = $("#anchor-custom-wrap");
  if (customWrap) customWrap.hidden = selflift || mid !== "custom";
  const liftWrap = $("#anchor-selflift-wrap");
  if (liftWrap) liftWrap.hidden = !(on && selflift);
  const liftHint = $("#anchor-selflift-hint");
  if (liftHint) liftHint.hidden = !(on && selflift);
  const card = $(".av-anchor-card");
  if (card) card.classList.toggle("is-compact", !on);
}

function bindSegmented(id, hiddenId) {
  const root = document.getElementById(id);
  if (!root) return;
  root.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-value]");
    if (!btn || btn.disabled) return;
    const hidden = document.getElementById(hiddenId);
    if (!hidden) return;
    hidden.value = btn.dataset.value;
    setSegmented(id, btn.dataset.value);
    syncAnchorUI();
  });
}

function initAnchorControls() {
  bindSegmented("f-stitch-seg", "f-stitch");
  bindSegmented("f-anchor-mid-seg", "f-anchor-mid");
  bindSegmented("f-anchor-pin-seg", "f-anchor-pin");
  $("#f-anchor-sampler")?.addEventListener("change", syncAnchorUI);
  syncAnchorUI();
}

/* 右栏：参数 / 运行结果 分页 */
const RIGHT_TAB_KEY = "h3av.rightTab";

function setRightTab(name, persist = true) {
  const params = $("#rail-params");
  const results = $("#rail-results");
  if (!params || !results) return;
  const showResults = name === "results";
  params.hidden = showResults;
  results.hidden = !showResults;
  $$("#rail-tabs [data-tab]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === name);
  });
  if (persist) {
    try {
      localStorage.setItem(RIGHT_TAB_KEY, name);
    } catch {
      /* 隐私模式忽略 */
    }
  }
}

function initRightTabs() {
  $$("#rail-tabs [data-tab]").forEach((btn) => {
    btn.addEventListener("click", () => setRightTab(btn.dataset.tab));
  });
  let saved = "params";
  try {
    saved = localStorage.getItem(RIGHT_TAB_KEY) || "params";
  } catch {
    saved = "params";
  }
  setRightTab(saved, false);
}

function updateRangeTone() {
  // 越往后拖越红：色相从琥珀 42° 压到 0°，亮度同步压暗，暗示“段越长越吃力”
  ["#f-target", "#f-max"].forEach((selector) => {
    const el = $(selector);
    if (!el) return;
    const min = num(el.min, 5);
    const max = num(el.max, 30);
    const value = clamp(num(el.value, min), min, max);
    const p = max > min ? (value - min) / (max - min) : 0;
    const hue = Math.round(42 - 42 * p);
    const light = Math.round(64 - 18 * p);
    el.style.setProperty("--range-p", `${(p * 100).toFixed(1)}%`);
    el.style.setProperty("--range-color", `hsl(${hue} 92% ${light}%)`);
  });
  const press = $("#max-pressure");
  if (press) {
    const value = num($("#f-max").value, 12);
    const level = value <= 15 ? "轻" : value <= 22 ? "中" : "重";
    press.textContent = `压力：${level}`;
    press.dataset.level = level;
  }
}

function updateRangeLabels() {
  $("#target-label").textContent = `${num($("#f-target").value, 8).toFixed(1)}s`;
  $("#max-label").textContent = `${num($("#f-max").value, 12).toFixed(1)}s`;
  updateRangeTone();
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
  if ($("#f-material-note")) $("#f-material-note").value = project.material_note || "";
  $("#asr-enabled").checked = project.asr?.enabled !== false;
  if (project.asr?.model) setSelectValue($("#asr-model"), project.asr.model === "base" ? "base" : "small");
  if (project.asr?.language) setSelectValue($("#asr-language"), project.asr.language === "auto" ? "auto" : "zh");
  setMode(Number(project.mode) === 1 ? 1 : Number(project.mode) === 3 ? 3 : 2, { quiet: true });
  fillGeneration(project.generation || {});
  updateRangeLabels();
  updateScriptCount();
  renderVoice();
  renderAudio();
}

function resetForm() {
  $("#f-name").value = "";
  $("#f-script").value = "";
  $("#f-cps").value = "5.0";
  $("#f-target").value = "8";
  $("#f-max").value = "12";
  $("#f-voice-desc").value = "";
  if ($("#f-material-note")) $("#f-material-note").value = "";
  $("#f-seed").value = "0";
  $("#asr-enabled").checked = true;
  setSelectValue($("#asr-model"), "small");
  setSelectValue($("#asr-language"), "zh");
  fillGeneration(state.generationDefaults || {});
  updateRangeLabels();
  updateScriptCount();
  renderVoice();
  renderAudio();
}

/* ---------------- 设计层：数据结构 / 目录 ---------------- */

function designBase(mode = state.mode) {
  const base = deepMerge(DESIGN_DEFAULT, state.designDefaults || {});
  base.audio_mode = AUDIO_MODE_BY_MODE[Number(mode)] || base.audio_mode || "generated";
  if (!Array.isArray(base.effect_families)) base.effect_families = [];
  if (!isPlainObject(base.voice_card)) base.voice_card = deepClone(DESIGN_DEFAULT.voice_card);
  if (!isPlainObject(base.style_system)) base.style_system = deepClone(DESIGN_DEFAULT.style_system);
  if (!isPlainObject(base.brand)) base.brand = deepClone(DESIGN_DEFAULT.brand);
  if (!isPlainObject(base.safe_area)) base.safe_area = deepClone(DESIGN_DEFAULT.safe_area);
  if (!isPlainObject(base.graphics_budget)) base.graphics_budget = deepClone(DESIGN_DEFAULT.graphics_budget);
  return base;
}

function designOf(project) {
  const mode = Number(project?.mode ?? state.mode) || state.mode;
  const base = designBase(mode);
  const design = deepMerge(base, project?.design || {});
  if (!design.audio_mode) design.audio_mode = AUDIO_MODE_BY_MODE[mode] || "generated";
  const legacy = project?.design?.style_system?.effect_families;
  if (!Array.isArray(design.effect_families) || !design.effect_families.length) {
    design.effect_families = Array.isArray(legacy) ? legacy.filter(Boolean) : [];
  }
  return design;
}

function styleSystemPresets() {
  const raw = state.catalog?.style_systems;
  if (Array.isArray(raw) && raw.length) return raw;
  return FALLBACK_STYLE_SYSTEMS;
}

function presetValues(field) {
  const values = [];
  styleSystemPresets().forEach((item) => {
    const value = String(item?.[field] ?? "").trim();
    if (value && !values.includes(value)) values.push(value);
  });
  return values;
}

function effectFamilyItems() {
  const raw = state.catalog?.effect_families;
  if (Array.isArray(raw) && raw.length) {
    return raw
      .map((item) => {
        if (typeof item === "string") return { id: item, label: item, hint: "" };
        const id = String(item?.id ?? item?.value ?? item?.label ?? "");
        if (!id) return null;
        const axes = item?.axes && typeof item.axes === "object"
          ? Object.values(item.axes).filter(Boolean).join(" · ")
          : "";
        return {
          id,
          label: String(item?.label ?? item?.name ?? id),
          hint: axes || String(item?.prompt_hint ?? ""),
        };
      })
      .filter(Boolean);
  }
  return FALLBACK_EFFECT_FAMILIES.map((item) => ({ ...item }));
}

const DENSITY_CARDS = {
  low: { label: "低", note: "1 条信息 · 长停留" },
  medium: { label: "中", note: "每拍 1 主视觉" },
  high: { label: "高", note: "短词组 · 快节奏" },
};

function svgPreview(markup) {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 96">${markup}</svg>`
  )}`;
}

function densityThumb(level) {
  const colors = { low: "#7C9CBF", medium: "#FFB454", high: "#FF6B5C" };
  const accent = colors[level] || colors.medium;
  const count = level === "low" ? 1 : level === "high" ? 5 : 3;
  let cards = "";
  for (let i = 0; i < count; i += 1) {
    const y = 20 + i * 13;
    if (y > 78) break;
    cards += `<rect x="96" y="${y}" width="48" height="9" rx="2" fill="${accent}" opacity="${(0.35 + 0.12 * i).toFixed(2)}"/>`;
  }
  return svgPreview(`<rect width="160" height="96" fill="#0F1218"/>
    <circle cx="48" cy="44" r="18" fill="#2B303B"/>
    <circle cx="48" cy="36" r="8" fill="#3C4657"/>
    <rect x="33" y="52" width="30" height="24" rx="8" fill="#303A4A"/>
    ${cards}
    <text x="8" y="88" font-size="8" fill="#8A94A6">${DENSITY_CARDS[level]?.note || ""}</text>`);
}

function styleThumb(item) {
  const accent = safeColor(item.accent_color, "#FFB454");
  const second = safeColor(item.secondary_color, "#8AB4F8");
  const id = String(item.id || "");
  const dark = /technology|documentary|premium/.test(id);
  const bg = dark ? "#0B0F16" : "#121826";
  let deco = "";
  if (id.includes("corporate")) {
    deco = `<rect x="96" y="20" width="52" height="34" rx="4" fill="${accent}" opacity=".18"/><rect x="102" y="26" width="40" height="4" rx="2" fill="${accent}"/><rect x="102" y="34" width="28" height="3" rx="1.5" fill="${second}" opacity=".7"/>`;
  } else if (id.includes("technology")) {
    deco = `<path d="M90 64 L112 42 L132 58 L150 34" stroke="${accent}" stroke-width="2" fill="none"/><circle cx="112" cy="42" r="3" fill="${accent}"/><circle cx="132" cy="58" r="3" fill="${second}"/>`;
  } else if (id.includes("premium")) {
    deco = `<rect x="98" y="18" width="50" height="52" rx="2" fill="none" stroke="${accent}" opacity=".8"/><rect x="104" y="26" width="30" height="3" fill="${accent}"/>`;
  } else if (id.includes("education")) {
    deco = `<circle cx="120" cy="40" r="16" fill="none" stroke="${accent}" stroke-width="2"/><path d="M112 44 L120 34 L128 44" stroke="${accent}" fill="none" stroke-width="2"/>`;
  } else if (id.includes("social")) {
    deco = `<circle cx="118" cy="38" r="20" fill="${accent}" opacity=".2"/><rect x="100" y="30" width="36" height="8" rx="4" fill="${accent}"/>`;
  } else if (id.includes("news")) {
    deco = `<rect x="96" y="58" width="54" height="14" rx="2" fill="${accent}" opacity=".2"/><rect x="100" y="62" width="46" height="6" rx="2" fill="${accent}"/><rect x="96" y="20" width="54" height="6" rx="2" fill="${second}" opacity=".6"/>`;
  } else if (id.includes("warm")) {
    deco = `<circle cx="120" cy="40" r="22" fill="${accent}" opacity=".16"/><rect x="100" y="34" width="40" height="6" rx="3" fill="${accent}"/>`;
  } else {
    deco = `<rect x="100" y="22" width="46" height="40" rx="4" fill="${accent}" opacity=".14"/><rect x="100" y="22" width="46" height="40" rx="4" fill="none" stroke="${accent}" opacity=".4"/>`;
  }
  return svgPreview(`<rect width="160" height="96" fill="${bg}"/>
    <circle cx="44" cy="44" r="18" fill="${second}" opacity=".25"/>
    <circle cx="44" cy="36" r="8" fill="${second}" opacity=".5"/>
    <rect x="29" y="52" width="30" height="24" rx="8" fill="${second}" opacity=".25"/>
    ${deco}
    <text x="8" y="88" font-size="8" fill="#9AA6B8">${item.label || item.id}</text>`);
}

function effectThumb(id, accent) {
  const a = safeColor(accent, "#FFB454");
  const shapes = {
    headline: `<rect x="24" y="26" width="80" height="10" rx="3" fill="${a}"/><rect x="24" y="42" width="52" height="6" rx="3" fill="${a}" opacity=".5"/>`,
    kinetic_type: `<text x="20" y="46" font-size="22" font-weight="700" fill="${a}">Aa</text><rect x="22" y="54" width="52" height="4" fill="${a}" opacity=".5"/>`,
    lower_third: `<rect x="12" y="58" width="96" height="18" rx="4" fill="${a}" opacity=".2"/><rect x="18" y="64" width="52" height="4" fill="${a}"/><rect x="18" y="71" width="36" height="3" fill="#fff" opacity=".4"/>`,
    info_card: `<rect x="14" y="18" width="92" height="58" rx="6" fill="${a}" opacity=".18"/><rect x="22" y="26" width="42" height="6" fill="${a}"/><rect x="22" y="38" width="66" height="4" fill="#fff" opacity=".35"/><rect x="22" y="46" width="54" height="4" fill="#fff" opacity=".25"/>`,
    chart: `<rect x="18" y="52" width="10" height="18" fill="${a}"/><rect x="34" y="42" width="10" height="28" fill="${a}" opacity=".8"/><rect x="50" y="30" width="10" height="40" fill="${a}" opacity=".6"/><rect x="66" y="46" width="10" height="24" fill="${a}" opacity=".4"/>`,
    icon: `<circle cx="40" cy="40" r="18" fill="none" stroke="${a}" stroke-width="3"/><path d="M32 40 L38 46 L50 32" stroke="${a}" stroke-width="3" fill="none"/>`,
    hud: `<path d="M20 24 h20 M20 24 v20 M100 24 h-20 M100 24 v20 M20 66 h20 M20 66 v-20 M100 66 h-20 M100 66 v-20" stroke="${a}" stroke-width="2" fill="none"/>`,
    stepper: `<circle cx="26" cy="36" r="7" fill="${a}"/><circle cx="52" cy="36" r="7" fill="${a}" opacity=".7"/><circle cx="78" cy="36" r="7" fill="${a}" opacity=".4"/><rect x="20" y="52" width="64" height="4" fill="#fff" opacity=".3"/>`,
    world_panel: `<rect x="16" y="16" width="88" height="62" rx="5" fill="${a}" opacity=".12"/><path d="M16 40 L104 40" stroke="${a}" opacity=".3"/><rect x="24" y="24" width="30" height="5" fill="${a}"/>`,
    default: `<rect x="18" y="22" width="84" height="50" rx="6" fill="${a}" opacity=".2"/><rect x="26" y="32" width="46" height="6" fill="${a}"/>`,
  };
  return svgPreview(`<rect width="160" height="96" fill="#0F1218"/>${shapes[id] || shapes.default}`);
}

function renderDesignCards() {
  const densityBox = $("#design-density");
  if (densityBox) {
    densityBox.innerHTML = ["low", "medium", "high"].map((level) => `
      <button type="button" class="av-density-card" data-density="${level}">
        <img src="${densityThumb(level)}" alt="">
        <span><b>${DENSITY_CARDS[level].label}</b><em>${DENSITY_CARDS[level].note}</em></span>
      </button>`).join("");
  }
  const styleBox = $("#design-style-systems");
  if (styleBox) {
    styleBox.innerHTML = styleSystemPresets().map((item) => `
      <button type="button" class="av-style-card${item.id === state.designStyleId ? " active" : ""}" data-style-id="${esc(item.id)}">
        <img src="${styleThumb(item)}" alt="">
        <span><b>${esc(item.label || item.id)}</b><em>${esc(item.material || "")}</em></span>
      </button>`).join("");
  }
  updateDesignChrome();
}

function inferStyleId(design) {
  const visual = String(design?.style_system?.visual_style || "");
  const items = styleSystemPresets();
  const exact = items.find((item) => String(item.visual_style || "") === visual || item.id === visual);
  if (exact) return exact.id;
  const typography = String(design?.style_system?.typography || "");
  const partial = items.find((item) => typography && String(item.typography || "") === typography);
  return partial ? partial.id : "";
}

function effectLabel(id) {
  return effectFamilyItems().find((item) => item.id === id)?.label || id;
}

function applyStyleSystem(id) {
  const item = styleSystemPresets().find((entry) => entry.id === id);
  if (!item) return;
  const set = (selector, value) => {
    const el = $(selector);
    if (el) el.value = value || "";
  };
  set("#design-typography", item.typography);
  set("#design-material", item.material);
  set("#design-icon-family", item.icon_family);
  set("#design-motion-grammar", item.motion_grammar);
  set("#design-visual-style", item.visual_style);
  $("#design-accent").value = designColor(item.accent_color);
  $("#design-secondary").value = designColor(item.secondary_color, "secondary");
  fillPresetSelect($("#design-typography-preset"), presetValues("typography"), item.typography || "");
  fillPresetSelect($("#design-material-preset"), presetValues("material"), item.material || "");
  state.designStyleId = id;
  markDesignDirty();
  renderDesignCards();
  updateDesignChrome();
}

function renderDesignPreview() {
  const frame = $("#design-preview-frame");
  if (!frame) return;
  const accent = safeColor($("#design-accent")?.value, "#FFB454");
  const second = safeColor($("#design-secondary")?.value, "#8AB4F8");
  const density = state.designDensity || "medium";
  const overlay = state.designGraphicsMode === "overlay";
  const families = selectedEffectIds();
  const wanted = density === "low" ? 1 : density === "high" ? 3 : 2;
  const chips = (families.length ? families : ["info_card"]).slice(0, wanted).map((id, index) => `
    <div class="av-preview-chip" style="border-color:${accent};background:${accent}22;color:${accent};--i:${index}">${esc(effectLabel(id))}</div>`).join("");
  frame.innerHTML = `
    <div class="av-preview-bg" style="background:radial-gradient(120% 80% at 76% 8%, ${accent}33, transparent 62%), linear-gradient(160deg, #171d29, #0a0e15)"></div>
    <div class="av-preview-person" style="background:linear-gradient(180deg, ${second}55, ${second}22)"><i></i><b></b></div>
    <div class="av-preview-safe" style="inset:8% 6% 12% 6%;border-color:${accent}55"></div>
    <div class="av-preview-kicker" style="color:${accent}">口播 · 数字人</div>
    <div class="av-preview-copy" style="color:#fff">这套方法只有三步</div>
    <div class="av-preview-chips">${chips}</div>
    <div class="av-preview-mode">${overlay ? "后期叠加" : "生成式图形"} · ${DENSITY_CARDS[density].label}密度</div>`;
  const title = $("#design-preview-title");
  if (title) title.textContent = overlay ? "叠加模式预览" : "风格预览";
  const sub = $("#design-preview-sub");
  if (sub) sub.textContent = `${DENSITY_CARDS[density].label}密度 · ${families.length} 个效果族 · 主色 ${accent}`;
  const tags = $("#design-preview-tags");
  if (tags) {
    tags.innerHTML = (families.length ? families : ["未选效果族"]).slice(0, 4)
      .map((id) => `<span>${esc(effectLabel(id) === id && id !== "未选效果族" ? id : effectLabel(id))}</span>`).join("");
  }
}


function fillPresetSelect(select, values, current) {
  if (!select) return;
  const list = Array.isArray(values) ? values : [];
  select.innerHTML = [`<option value="__custom__">自定义</option>`]
    .concat(list.map((value) => `<option value="${esc(value)}">${esc(value)}</option>`))
    .join("");
  select.value = list.includes(String(current || "")) ? String(current) : "__custom__";
}

function fillDesignOptions() {
  const current = state.designLoadedFor ? readDesignSafe() : null;
  fillPresetSelect(
    $("#design-typography-preset"),
    presetValues("typography"),
    current?.style_system?.typography || $("#design-typography")?.value || ""
  );
  fillPresetSelect(
    $("#design-material-preset"),
    presetValues("material"),
    current?.style_system?.material || $("#design-material")?.value || ""
  );
  renderEffectCheckboxes(current?.effect_families || selectedEffectIds());
  renderDesignCards();
}

function readDesignSafe() {
  try {
    return readDesign();
  } catch {
    return null;
  }
}

function selectedEffectIds() {
  return $$("#design-effects input[type=checkbox]").filter((el) => el.checked).map((el) => el.value);
}

function renderEffectCheckboxes(selected = []) {
  const box = $("#design-effects");
  if (!box) return;
  const families = effectFamilyItems();
  const chosen = Array.isArray(selected) ? selected.map(String) : [];
  if (!families.length) {
    box.innerHTML = `<div class="av-effect-empty">效果目录不可用（后端 /effect-catalog 未就绪）</div>`;
    return;
  }
  const accent = $("#design-accent")?.value || "#FFB454";
  box.innerHTML = families
    .map(
      (item) => `<label class="av-effect-item" title="${esc(item.hint || "")}">
        <input type="checkbox" value="${esc(item.id)}" ${chosen.includes(item.id) ? "checked" : ""}>
        <img class="av-effect-thumb" src="${effectThumb(item.id, accent)}" alt="" loading="lazy">
        <span>${esc(item.label)}${item.hint ? `<em>${esc(item.hint)}</em>` : ""}</span>
      </label>`
    )
    .join("");
}

function buildVoiceCardFields() {
  const box = $("#voice-card-fields");
  if (!box || box.dataset.built === "1") return;
  box.dataset.built = "1";
  box.innerHTML = VOICE_CARD_FIELDS.map(([key, label, hint, wide]) => {
    const control = wide
      ? `<textarea class="input" rows="2" spellcheck="false" data-voicecard="${key}" placeholder="${esc(hint)}"></textarea>`
      : `<input class="input" spellcheck="false" data-voicecard="${key}" placeholder="${esc(hint)}">`;
    return `<div class="field ${wide ? "av-voicecard-field-wide" : ""}">
      <label>${esc(label)}</label>${control}
    </div>`;
  }).join("");
}

function readVoiceCard() {
  const card = {};
  $$("[data-voicecard]").forEach((el) => {
    card[el.dataset.voicecard] = String(el.value ?? "").trim();
  });
  return card;
}

function fillVoiceCard(voiceCard) {
  const data = isPlainObject(voiceCard) ? voiceCard : {};
  $$("[data-voicecard]").forEach((el) => {
    el.value = data[el.dataset.voicecard] ?? "";
  });
}

function designColor(tone) {
  return safeColor(tone, tone === "secondary" ? "#8AB4F8" : "#FFB454");
}

function fillDesign(design) {
  const d = deepMerge(designBase(state.mode), design || {});
  state.designDensity = ["low", "medium", "high"].includes(d.effect_density) ? d.effect_density : "medium";
  state.designGraphicsMode = d.graphics_mode === "overlay" ? "overlay" : "generated";
  state.designStyleId = inferStyleId(d);
  $("#design-accent").value = designColor(d.style_system?.accent_color);
  $("#design-secondary").value = designColor(d.style_system?.secondary_color, "secondary");
  $("#design-typography").value = d.style_system?.typography || "";
  $("#design-material").value = d.style_system?.material || "";
  fillPresetSelect($("#design-typography-preset"), presetValues("typography"), d.style_system?.typography || "");
  fillPresetSelect($("#design-material-preset"), presetValues("material"), d.style_system?.material || "");
  renderEffectCheckboxes(Array.isArray(d.effect_families) ? d.effect_families : []);
  setSelectValue($("#design-platform"), d.platform || "generic");
  const area = isPlainObject(d.safe_area) ? d.safe_area : DESIGN_DEFAULT.safe_area;
  ["top", "bottom", "left", "right"].forEach((side) => {
    $(`#design-safe-${side}`).value = clamp(num(area[side], DESIGN_DEFAULT.safe_area[side]), 0, 0.3);
  });
  const budget = isPlainObject(d.graphics_budget) ? d.graphics_budget : DESIGN_DEFAULT.graphics_budget;
  $("#design-budget-groups").value = clamp(num(budget.max_groups, 1), 1, 4);
  $("#design-budget-flashes").value = clamp(num(budget.max_flashes_per_sec, 3), 1, 3);
  $("#design-budget-chars").value = clamp(num(budget.prompt_chars_target, 7000), 2000, 20000);
  const brand = isPlainObject(d.brand) ? d.brand : DESIGN_DEFAULT.brand;
  $("#design-brand-palette").value = Array.isArray(brand.palette) ? brand.palette.join(", ") : "";
  $("#design-brand-logos").value = Array.isArray(brand.logo_refs) ? brand.logo_refs.join(", ") : "";
  $("#design-brand-typography").value = brand.typography_note || "";
  $("#design-visual-style").value = d.style_system?.visual_style || "";
  $("#design-motion-grammar").value = d.style_system?.motion_grammar || "";
  $("#design-icon-family").value = d.style_system?.icon_family || "";
  $("#design-route").value = d.route || "Ref2VA";
  setSelectValue($("#design-audio-mode"), d.audio_mode || "");
  fillVoiceCard(d.voice_card);
  state.designDirty = false;
  updateDesignChrome();
}

function readDesign() {
  const styleSystem = {
    typography: $("#design-typography").value.trim(),
    material: $("#design-material").value.trim(),
    icon_family: $("#design-icon-family").value.trim(),
    motion_grammar: $("#design-motion-grammar").value.trim(),
    accent_color: safeColor($("#design-accent").value, "#FFB454"),
    secondary_color: safeColor($("#design-secondary").value, "#8AB4F8"),
    visual_style: $("#design-visual-style").value.trim(),
  };
  const design = {
    route: ($("#design-route").value || "").trim() || "Ref2VA",
    effect_density: state.designDensity,
    platform: $("#design-platform").value || "generic",
    safe_area: {
      top: clamp(num($("#design-safe-top").value, 0.08), 0, 0.3),
      bottom: clamp(num($("#design-safe-bottom").value, 0.12), 0, 0.3),
      left: clamp(num($("#design-safe-left").value, 0.06), 0, 0.3),
      right: clamp(num($("#design-safe-right").value, 0.06), 0, 0.3),
    },
    style_system: styleSystem,
    graphics_budget: {
      max_groups: Math.round(clamp(num($("#design-budget-groups").value, 1), 1, 4)),
      max_flashes_per_sec: Math.round(clamp(num($("#design-budget-flashes").value, 3), 1, 3)),
      prompt_chars_target: Math.round(clamp(num($("#design-budget-chars").value, 7000), 2000, 20000)),
    },
    graphics_mode: state.designGraphicsMode,
    brand: {
      palette: splitList($("#design-brand-palette").value),
      typography_note: $("#design-brand-typography").value.trim(),
      logo_refs: splitList($("#design-brand-logos").value),
    },
    effect_families: selectedEffectIds(),
  };
  const audioMode = $("#design-audio-mode").value;
  if (audioMode) design.audio_mode = audioMode;
  if ((state.project?.mode ?? state.mode) === 3) design.voice_card = readVoiceCard();
  return design;
}

function markDesignDirty() {
  state.designDirty = true;
  $("#design-state").textContent = state.project ? "未保存" : "待新建";
}

function updateDesignChrome() {
  const density = state.designDensity || "medium";
  $$("#design-density [data-density]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.density === density);
  });
  const overlay = state.designGraphicsMode === "overlay";
  $$("#design-graphics-mode [data-gmode]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.gmode === state.designGraphicsMode);
  });
  $$("#design-style-systems [data-style-id]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.styleId === state.designStyleId);
  });
  const badge = $("#design-badge");
  if (badge) {
    badge.textContent = GRAPHICS_MODE_LABEL[state.designGraphicsMode] || "生成式";
    badge.className = `pill ${overlay ? "" : "vocal"}`;
  }
  const mini = $(".av-design-mini");
  if (mini) mini.textContent = `密度${DENSITY_LABEL[density] || "中"}`;
  $("#design-graphics-note").classList.toggle("hidden", !overlay);
  $("#design-effects-count").textContent = `${selectedEffectIds().length} 项`;
  renderDesignPreview();
  $("#design-state").textContent = state.designDirty ? (state.project ? "未保存" : "待新建") : state.project ? "已保存" : "默认";
  const showVoiceCard = state.mode === 3;
  $("#voice-card").hidden = !showVoiceCard;

  const pill = $("#graphics-pill");
  if (pill) {
    pill.textContent = GRAPHICS_MODE_LABEL[state.designGraphicsMode] || "生成式";
    pill.className = `pill av-graphics-pill ${overlay ? "" : "vocal"}`;
    pill.classList.toggle("hidden", !state.project);
  }
  $("#overlay-note").classList.toggle("hidden", !overlay);
  $$(".av-overlay-inline").forEach((el) => {
    el.hidden = !overlay;
  });
  const generate = $("#btn-generate");
  if (generate) {
    generate.title = overlay
      ? "按分段逐段生成视频（叠加模式：画面文字/logo 由后期确定性叠加）"
      : "按分段逐段生成视频（耗时长）";
  }
}

async function loadDesignCatalog() {
  let catalog = null;
  let defaults = null;
  try {
    catalog = await api.effectCatalog();
  } catch {
    catalog = null;
  }
  try {
    const data = await api.designDefaults();
    defaults = data?.design || null;
  } catch {
    defaults = null;
  }
  state.catalog = catalog;
  state.designDefaults = defaults;
  fillDesignOptions();
}

async function loadCatalogAtBoot() {
  await loadDesignCatalog();
  buildVoiceCardFields();
  fillDesign(designBase(state.mode));
}

async function saveDesign() {
  const p = state.project;
  if (!p) {
    toast("请先新建或打开作品，再保存设计", "info");
    return;
  }
  await withBusy($("#btn-design-save"), async () => {
    const data = await api.patchDesign(p.id, readDesign());
    const design = data?.design || data?.project?.design;
    if (p) p.design = design || p.design;
    if (design) fillDesign(designOf(p));
    state.designDirty = false;
    fillDesignOptions();
    updateDesignChrome();
    renderProjectContext();
    renderSegments();
    toast("设计已保存", "ok");
  }).catch((err) => toast(`设计保存失败：${err.message}`, "err", 6000));
}

async function suggestDesignEffects() {
  const p = state.project;
  if (!p) {
    toast("请先新建或打开作品", "info");
    return;
  }
  await withBusy($("#btn-design-suggest"), async () => {
    const data = await api.suggestDesign(p.id, { mode: "effects" });
    const suggestion = data?.suggestion || {};
    const warnings = Array.isArray(data?.warnings) ? data.warnings : [];
    const stylePatch = isPlainObject(suggestion.style_system)
      ? Object.entries(suggestion.style_system).filter(([, value]) => String(value ?? "").trim())
      : [];
    const changed = suggestion.effect_density || suggestion.graphics_mode || stylePatch.length;
    if (!changed) {
      toast(warnings[0] || "AI 没有返回设计建议", warnings.length ? "err" : "info", 8000);
      return;
    }
    const next = readDesign();
    if (suggestion.effect_density) next.effect_density = suggestion.effect_density;
    if (suggestion.graphics_mode) next.graphics_mode = suggestion.graphics_mode;
    if (stylePatch.length) {
      // 建议里未填的字段留空，不要覆盖用户已填内容
      const style = { ...next.style_system };
      stylePatch.forEach(([key, value]) => {
        style[key] = value;
      });
      next.style_system = style;
    }
    if (Array.isArray(suggestion.effect_families) && suggestion.effect_families.length) {
      next.effect_families = suggestion.effect_families;
    }
    fillDesign(deepMerge(designBase(state.mode), next));
    markDesignDirty();
    updateDesignChrome();
    renderProjectContext();
    $("#design-details").open = true;
    toast(
      warnings.length ? `AI 建议已填入表单（${warnings.length} 条提示），请点「保存设计」` : "AI 建议已填入表单，请点「保存设计」",
      warnings.length ? "info" : "ok",
      6000
    );
  }).catch((err) => toast(`AI 建议失败：${err.message}`, "err", 6000));
}

async function suggestVoiceCard() {
  const p = state.project;
  if (!p) {
    toast("请先新建或打开作品", "info");
    return;
  }
  await withBusy($("#btn-voicecard-suggest"), async () => {
    const data = await api.suggestDesign(p.id, { mode: "voice" });
    const voiceCard = data?.suggestion?.voice_card || {};
    let filled = 0;
    Object.entries(voiceCard).forEach(([key, value]) => {
      const el = document.querySelector(`[data-voicecard="${key}"]`);
      if (el && value !== null && value !== undefined && String(value).trim()) {
        el.value = String(value);
        filled += 1;
      }
    });
    if (filled) markDesignDirty();
    updateDesignChrome();
    $("#design-details").open = true;
    $("#voice-card").hidden = false;
    $("#voice-card").scrollIntoView({ behavior: "smooth", block: "center" });
    const warnings = Array.isArray(data?.warnings) ? data.warnings : [];
    if (!filled) {
      toast(warnings[0] || "AI 没有返回音色卡建议", warnings.length ? "err" : "info", 8000);
      return;
    }
    toast(
      `已填入 ${filled} 项音色卡，请点「保存设计」${warnings.length ? `（${warnings.length} 条提示）` : ""}`,
      warnings.length ? "info" : "ok",
      6000
    );
  }).catch((err) => toast(`AI 生成音色卡失败：${err.message}`, "err", 6000));
}

/* ---------------- 模式 / 参考音频 / 模式 1 音频 ---------------- */

function modeHintText(mode) {
  if (mode === 1) return "音频驱动：上传音频后按波形自动分段，保留原音对口型；时间轴可拖切点、按播放头切分、合并下段。";
  if (mode === 2) return "参考音色：上传 2–15 秒参考音频，H3 逐段原生生成该音色说出台词的视频（自带音频）。";
  return "无音频参考：只用台词与音色描述，按约 5 字/秒规划每段时长，视频自带生成音频。";
}

async function switchMode(mode) {
  const value = [1, 2, 3].includes(Number(mode)) ? Number(mode) : 2;
  const project = state.project;
  if (!project || Number(project.mode) === value) {
    setMode(value);
    return;
  }
  const ok = await confirmDialog(
    "切换创作模式",
    `当前作品是「${MODE_LABEL[Number(project.mode)]}」，切换为「${MODE_LABEL[value]}」`
      + "会重置分段、素材与生成结果（项目名和设定保留）。是否切换？",
    "切换模式"
  );
  if (!ok) {
    // 用户取消：把高亮还原成作品真实模式，避免“看着是模式 2、实际还是模式 1”
    setMode(Number(project.mode), { quiet: true });
    return;
  }
  // 模式 1 的台词来自音频识别（project.script 可能为空）：切到 2/3 前把分段台词抄进台词框，
  // 切换后就能直接按台词重建分段，不会出现“切完什么都没有”。
  const oldScript = String(project.script || "").trim();
  const derivedScript = (project.segments || []).map((row) => String(row.text || "").trim())
    .filter(Boolean).join("");
  const payload = { mode: value };
  if (value !== 1 && !oldScript && derivedScript) payload.script = derivedScript;
  await withBusy(document.querySelector(`[data-mode-card="${value}"]`), async () => {
    const data = await api.patchProject(project.id, payload);
    state.pendingVoice = null;
    state.pendingAudio = null;
    applyProject(data.project, { fill: true });
    setMode(value, { quiet: true });
    await loadProjects();
    // 模式 2/3 没有音频时间轴：切过来后按现有台词自动重建分段，避免“分段没了”
    if (value !== 1 && String(data.project.script || "").trim()
        && !(data.project.segments || []).length) {
      toast("正在按台词重建分段…", "info", 4000);
      await planSegments({ silent: true });
    }
    toast(`已切换为「${MODE_LABEL[value]}」并保存到当前作品`, "ok");
  }).catch((err) => {
    toast(`切换模式失败：${err.message}`, "err", 7000);
    setMode(Number(project.mode), { quiet: true });
  });
}

function setMode(mode, { quiet = false } = {}) {
  const value = [1, 2, 3].includes(Number(mode)) ? Number(mode) : 2;
  state.mode = value;
  // 切模式时清掉不属于当前模式的待上传素材，避免“模式三高亮但表单还挂着参考音频”
  if (value !== 2 && state.pendingVoice) {
    state.pendingVoice = null;
  }
  if (value !== 1 && state.pendingAudio) {
    state.pendingAudio = null;
  }
  stopVoiceAudition();
  $$("[data-mode-card]").forEach((btn) => {
    btn.classList.toggle("active", Number(btn.dataset.modeCard) === value);
  });
  $("#mode-form-1").classList.toggle("hidden", value !== 1);
  $("#mode-form-2").classList.toggle("hidden", value !== 2);
  $("#mode-form-3").classList.toggle("hidden", value !== 3);
  const hint = $("#mode-hint");
  if (hint) hint.textContent = modeHintText(value);
  const scriptRequired = $("#script-required");
  if (scriptRequired) {
    scriptRequired.textContent = value === 1 ? "模式 1 可选 · 不阻塞生成" : "模式 2/3 必填";
  }
  const planRow = $("#form-actions-plan");
  if (planRow) {
    $("#btn-plan").classList.toggle("hidden", value === 1);
    planRow.classList.toggle("single", value === 1);
  }
  if (state.project && state.project.mode !== value && !quiet) {
    toast(`已切换为「${MODE_LABEL[value]}」，点「保存设定」生效`, "info");
  }
  renderVoice();
  renderAudio();
  updateDesignChrome();
  syncAnchorUI();
  syncButtons();
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

function stopVoiceAudition() {
  const el = $("#voice-audition");
  if (el) {
    el.pause();
    el.removeAttribute("src");
    try { el.load(); } catch { /* ignore */ }
  }
  const label = $("#voice-audition-label");
  if (label) label.textContent = "试听";
  if (state.voiceAuditionObjectUrl) {
    URL.revokeObjectURL(state.voiceAuditionObjectUrl);
    state.voiceAuditionObjectUrl = "";
  }
}

async function toggleVoiceAudition() {
  const voice = currentVoice();
  const el = $("#voice-audition");
  const label = $("#voice-audition-label");
  if (!voice || !el) return;
  if (!el.paused) {
    stopVoiceAudition();
    return;
  }
  let src = "";
  if (voice.pending && state.pendingVoice) {
    if (!state.voiceAuditionObjectUrl) {
      state.voiceAuditionObjectUrl = URL.createObjectURL(state.pendingVoice);
    }
    src = state.voiceAuditionObjectUrl;
  } else if (state.project) {
    src = `${projectPath(state.project.id)}/media/voice/ref.wav?v=${voice.sample_start || 0}-${voice.sample_length || 0}-${voice.uploaded || 0}`;
  }
  if (!src) return;
  el.src = src;
  try {
    await el.play();
    if (label) label.textContent = "停止";
  } catch (error) {
    toast(`试听失败：${error.message || error}`, "err");
    stopVoiceAudition();
  }
}

function voiceFileUrl(project, voice) {
  const filename = String(voice?.filename || "");
  if (!project || !filename) return "";
  const path = filename.includes("/") ? filename : `voice/${filename}`;
  return mediaUrl(project.id, path, voice.uploaded);
}

function renderVoiceList() {
  const box = $("#voice-list");
  if (!box) return;
  const project = state.project;
  if (state.mode !== 2) { box.innerHTML = ""; box.classList.add("hidden"); return; }
  let items = [];
  if (project && Array.isArray(project.voices) && project.voices.length) {
    items = project.voices.map((voice, index) => ({ voice, primary: index === 0 }));
  } else if (project && project.voice) {
    items = [{ voice: project.voice, primary: true }];
  } else if (state.pendingVoice) {
    items = [{ voice: { name: state.pendingVoice.name, filename: state.pendingVoice.name,
      duration: state.pendingVoice._probeDuration || 0, pending: true }, primary: true }];
  }
  box.classList.toggle("hidden", !items.length);
  box.innerHTML = items.map(({ voice, primary }) => {
    const duration = num(voice.sample_length) || num(voice.duration);
    return `<div class="av-voice-item ${primary ? "active" : ""}" data-voice-id="${esc(String(voice.id || ""))}">
      <input class="input av-voice-name" data-voice-name value="${esc(String(voice.name || voice.filename || "音色"))}"
        ${voice.pending ? "disabled" : ""} title="改个名字，方便在创作要求里引用">
      ${primary ? '<span class="av-voice-tag">主音色</span>' : ""}
      <span class="av-voice-dur">${duration > 0 ? `${duration.toFixed(1)}s` : "已选择"}</span>
      <button class="btn ghost sm" type="button" data-voice-audition title="试听这个音色">试听</button>
      ${voice.pending ? "" : `<button class="btn ghost sm" type="button" data-voice-delete title="删除这个音色">删除</button>`}
    </div>`;
  }).join("");
}

function auditionVoice(voice) {
  const el = $("#voice-audition");
  const src = voiceFileUrl(state.project, voice);
  if (!el || !src) { toast("这个音色还没有可试听的文件", "warn"); return; }
  el.src = src;
  el.play().catch(() => toast("无法播放这个音色", "err"));
}

async function renameVoice(voiceId, name) {
  const p = state.project;
  if (!p || !voiceId) return;
  try {
    const data = await api.patchVoice(p.id, voiceId, name);
    state.project = data.project;
    toast(`音色已改名为「${name}」`, "ok", 2600);
  } catch (error) {
    toast(`音色改名失败：${error.message}`, "err", 6000);
  }
}

async function removeVoice(voiceId) {
  const p = state.project;
  if (!p || !voiceId) return;
  const ok = await confirmDialog("删除音色", "删除后，引用这个音色的提示词可能失效。继续？", "删除");
  if (!ok) return;
  try {
    const data = await api.deleteVoiceItem(p.id, voiceId);
    applyProject(data.project, { fill: false, segments: false });
    toast("音色已删除", "ok", 3000);
  } catch (error) {
    toast(`删除音色失败：${error.message}`, "err", 6000);
  }
}

function renderVoice() {
  const voice = currentVoice();
  const drop = $("#voice-drop");
  const row = $("#voice-row");
  const label = $("#voice-label");
  const sampleBox = $("#voice-sample-box");
  if (!drop || !row) return;
  if (state.mode !== 2 || !voice) {
    drop.classList.toggle("hidden", state.mode !== 2);
    row.classList.add("hidden");
    if (sampleBox) sampleBox.classList.add("hidden");
    if (label) label.textContent = "";
    stopVoiceAudition();
    return;
  }
  drop.classList.add("hidden");
  row.classList.remove("hidden");
  $("#voice-name").textContent = voice.filename || "参考音频";
  const sourceDuration = num(voice.source_duration);
  const sampleDuration = num(voice.sample_length) || num(voice.duration);
  const parts = [];
  if (voice.pending) {
    parts.push(sourceDuration > 0 ? `原音 ${sourceDuration.toFixed(1)} 秒` : "已选择");
    parts.push("创建作品后自动截取音色样本");
  } else {
    if (sourceDuration > sampleDuration + 0.1) {
      parts.push(`原音 ${sourceDuration.toFixed(1)} 秒`);
      parts.push(`样本 ${sampleDuration.toFixed(1)} 秒`);
    } else if (sampleDuration > 0) {
      parts.push(`${sampleDuration.toFixed(1)} 秒`);
    }
    if (voice.sample_rate) parts.push(`${num(voice.sample_rate)} Hz`);
    if (!parts.length) parts.push("已上传");
  }
  const span = $("#voice-duration");
  span.textContent = parts.join(" · ");
  span.classList.remove("warn");
  label.textContent = voice.filename || "";

  if (sampleBox) {
    const hasSource = !voice.pending && num(voice.source_duration) > 0;
    sampleBox.classList.toggle("hidden", !hasSource);
    if (hasSource) {
      const total = num(voice.source_duration);
      const length = num(voice.sample_length) || 12;
      const start = num(voice.sample_start) || 0;
      const slider = $("#f-voice-start");
      slider.max = String(Math.max(0, total - length));
      slider.value = String(clamp(start, 0, Math.max(0, total - length)));
      slider.disabled = total <= length + 0.05;
      $("#voice-sample-range").textContent =
        `${start.toFixed(1)}–${Math.min(total, start + length).toFixed(1)}s（原音 ${total.toFixed(1)}s）`;
      const source = $("#voice-sample-source");
      if (source) source.textContent = voice.trimmed ? "原始录音已保留" : "原音不足 15 秒，整段使用";
      $("#voice-sample-hint").textContent = voice.trimmed
        ? "只把这一段送给 H3 当音色参考；拖动起点后点「试听」确认。"
        : "原音不足 15 秒，已整段作为音色样本。";
      $("#btn-voice-audition").disabled = false;
    }
  }
  renderVoiceList();
}

async function saveVoiceSample() {
  const project = state.project;
  const voice = project?.voice;
  if (!project || !voice?.source_filename) return;
  const slider = $("#f-voice-start");
  const start = num(slider?.value, num(voice.sample_start));
  const length = num(voice.sample_length) || 12;
  $("#voice-sample-range").textContent =
    `${start.toFixed(1)}–${Math.min(num(voice.source_duration), start + length).toFixed(1)}s（原音 ${num(voice.source_duration).toFixed(1)}s）`;
  try {
    const data = await api.voiceSample(project.id, { start_seconds: start, length_seconds: length });
    applyProject(data.project, { fill: false, segments: false });
    toast("音色样本已更新，可点「试听」确认", "ok");
  } catch (error) {
    toast(`音色样本更新失败：${error.message}`, "err");
    renderVoice();
  }
}

function currentAudio() {
  const project = state.project;
  if (state.pendingAudio) {
    return {
      filename: state.pendingAudio.name,
      duration: state.pendingAudio._probeDuration || 0,
      pending: true,
      replacing: !!(project && project.audio),
    };
  }
  return project?.audio || null;
}

function renderAudio() {
  const audio = currentAudio();
  const drop = $("#audio-drop");
  const row = $("#audio-row");
  if (!drop || !row) return;
  if (!audio) {
    drop.classList.remove("hidden");
    row.classList.add("hidden");
    $("#audio-label").textContent = "";
  } else {
    drop.classList.add("hidden");
    row.classList.remove("hidden");
    $("#audio-name").textContent = audio.filename || "音频";
    const parts = [];
    if (num(audio.duration) > 0) parts.push(`${num(audio.duration).toFixed(1)} 秒`);
    if (audio.pending) parts.push(audio.replacing ? "待上传替换 · 分析时生效" : "待上传 · 点「上传并分析」");
    else if (audio.sample_rate) parts.push(`${num(audio.sample_rate)} Hz`);
    if (!parts.length) parts.push("已上传");
    $("#audio-duration").textContent = parts.join(" · ");
    $("#audio-label").textContent = audio.filename || "";
  }
  const analysisReady = !!state.project?.analysis?.waveform?.original?.length;
  $("#btn-analyze-label").textContent = analysisReady ? "重新分析音频" : "上传并分析";
  $("#audio-hint").textContent = analysisReady
    ? "分析完成：可拖动切点、按播放头切分或合并下段；改切点后请重新生成提示词。"
    : "上传并分析后，在中间时间轴拖动切点、按播放头切分或合并下段。";
  syncButtons();
}

function probeAudioDuration(file) {
  return new Promise((resolve) => {
    let settled = false;
    let url = "";
    const elements = [];
    const finish = (value) => {
      if (settled) return;
      settled = true;
      if (url) URL.revokeObjectURL(url);
      elements.forEach((el) => {
        el.removeAttribute("src");
        el.remove();
      });
      resolve(Number.isFinite(value) && value > 0 ? value : 0);
    };
    const load = (tag, onFail) => {
      const el = document.createElement(tag);
      elements.push(el);
      el.preload = "metadata";
      el.onloadedmetadata = () => finish(el.duration);
      el.onerror = () => {
        if (onFail) onFail();
      };
      el.src = url;
    };
    try {
      url = URL.createObjectURL(file);
      // 先按音频探测；视频文件会失败后退回视频元素
      load("audio", () => load("video", null));
      window.setTimeout(() => finish(0), 6000);
    } catch {
      finish(0);
    }
  });
}

async function handleVoiceFile(file) {
  if (!file) return;
  if (state.project && Number(state.project.mode) !== 2) {
    toast(`当前作品是「${MODE_LABEL[Number(state.project.mode)]}」，不能上传参考音频；`
      + "请先把左侧模式切到「参考音色」并确认切换（或新建模式 2 作品）", "warn", 8000);
    return;
  }
  const duration = await probeAudioDuration(file);
  if (state.project) {
    await withBusy($("#btn-voice-replace"), async () => {
      const data = await api.addVoice(state.project.id, file, "");
      state.pendingVoice = null;
      applyProject(data.project, { fill: false, segments: false });
      const count = (data.project?.voices || []).length;
      toast(`已添加音色「${data.voice?.name || "音色"}」（共 ${count} 个）`, "ok");
    }).catch((err) => toast(`音频上传失败：${err.message}`, "err"));
    return;
  }
  state.pendingVoice = file;
  file._probeDuration = duration;
  renderVoice();
  toast(duration > 15
    ? `已选择 ${duration.toFixed(1)} 秒录音，创建作品后会自动截取 12 秒音色样本`
    : "参考音频已选择，创建作品时一起上传", "info");
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

async function handleAudioFile(file) {
  if (!file) return;
  if (state.project && Number(state.project.mode) !== 1) {
    toast(`当前作品是「${MODE_LABEL[Number(state.project.mode)]}」，不能上传驱动音频；`
      + "请先把左侧模式切到「音频驱动」并确认切换（或新建模式 1 作品）", "warn", 8000);
    return;
  }
  const duration = await probeAudioDuration(file);
  state.pendingAudio = file;
  file._probeDuration = duration;
  renderAudio();
  toast(
    state.project?.audio ? "已选择新音频，点「上传并分析」替换并重新分段" : "音频已选择，点「上传并分析」",
    "info"
  );
}

async function deleteAudio() {
  if (state.pendingAudio) {
    state.pendingAudio = null;
    renderAudio();
    toast("已取消待上传的音频", "info");
    return;
  }
  const p = state.project;
  if (!p?.audio) return;
  const ok = await confirmDialog("删除音频", "删除后模式 1 需要重新上传音频并分析才能生成。继续？", "删除");
  if (!ok) return;
  await withBusy($("#btn-audio-del"), async () => {
    const data = await api.deleteAudio(p.id);
    state.pendingAudio = null;
    state.playhead = 0;
    applyProject(data.project, { fill: false });
    toast("音频已删除", "ok");
  }).catch((err) => toast(`删除失败：${err.message}`, "err"));
}

async function ensureProject() {
  if (state.project) return state.project;
  const form = readForm();
  form.name = form.name || fmtTodayName();
  const data = await api.createProject({ ...form, generation: JSON.stringify(readGeneration()) });
  state.project = data.project;
  state.pendingVoice = null;
  applyProject(data.project, { fill: true });
  localStorage.setItem(LAST_KEY, data.project.id);
  await loadProjects();
  return data.project;
}

async function analyzeAudioFlow() {
  const button = $("#btn-analyze");
  showBusy("正在上传/分析音频（语音识别可能需要几十秒）…");
  try {
    await withBusy(button, async () => {
      let project = await ensureProject();
      if (Number(project.mode) !== state.mode) {
        // 用户切换了模式但还没点保存：先同步，否则 /audio 与 /analyze 会被后端拒绝
        const form = readForm();
        form.name = form.name || project.name;
        project = (await api.patchProject(project.id, { ...form, generation: readGeneration() })).project;
        state.project = project;
        applyProject(project, { fill: true, segments: false });
      }
      if (state.pendingAudio) {
        const data = await api.uploadAudio(project.id, state.pendingAudio);
        state.pendingAudio = null;
        state.project = data.project;
        project = data.project;
        applyProject(data.project, { fill: false, segments: false });
      }
      if (!project.audio) throw new Error("请先上传音频文件");
      const body = {
        use_asr: $("#asr-enabled").checked,
        asr_model: $("#asr-model").value || "small",
        asr_language: $("#asr-language").value || "zh",
        script: $("#f-script").value,
        target_seconds: num($("#f-target").value, 8),
        max_seconds: num($("#f-max").value, 12),
      };
      const data = await api.analyze(project.id, body);
      state.playhead = 0;
      applyProject(data.project, { fill: false });
      await loadProjects();
      const count = (data.project.segments || []).length;
      toast(count ? `已分析并分成 ${count} 段` : "分析完成，没有检测到可用的分段", count ? "ok" : "info", 6000);
    });
  } catch (err) {
    toast(`分析失败：${err.message}`, "err", 7000);
  } finally {
    hideBusy();
    syncButtons();
  }
}

/* ---------------- 音频时间线 ---------------- */

function segSeconds(seg) {
  const seconds = num(seg?.seconds);
  if (seconds > 0) return seconds;
  const edit = num(seg?.edit_frames);
  if (edit > 0) return edit / FPS;
  const frames = num(seg?.frames);
  if (frames > 0) return frames / FPS;
  return 0;
}

function segmentStart(project, index) {
  const segments = project?.segments || [];
  const seg = segments[index];
  if (!seg) return 0;
  const raw = Number(seg.start_frame);
  if (Number.isFinite(raw) && (raw > 0 || index === 0)) return raw / FPS;
  let total = 0;
  for (let i = 0; i < index; i += 1) total += segSeconds(segments[i]);
  return total;
}

function segmentEnd(project, index) {
  const seg = project?.segments?.[index];
  if (!seg) return 0;
  const raw = Number(seg.end_frame);
  if (Number.isFinite(raw) && raw > 0) return raw / FPS;
  return segmentStart(project, index) + segSeconds(seg);
}

function audioDuration() {
  const project = state.project;
  if (!project) return 0;
  const duration = num(project.audio?.duration);
  if (duration > 0) return duration;
  const segments = project.segments || [];
  if (!segments.length) return 0;
  return segmentEnd(project, segments.length - 1);
}

function internalCuts(project = state.project) {
  const p = project;
  if (!p) return [];
  const duration = audioDuration();
  const waveCuts = p.analysis?.waveform?.cuts;
  const frameCuts = p.analysis?.cuts;
  let cuts;
  if (Array.isArray(waveCuts) && waveCuts.length) {
    cuts = waveCuts.map((value) => num(value));
  } else if (Array.isArray(frameCuts) && frameCuts.length) {
    cuts = frameCuts.map((value) => num(value) / FPS);
  } else {
    cuts = [];
    const segments = p.segments || [];
    for (let i = 1; i < segments.length; i += 1) cuts.push(segmentStart(p, i));
  }
  return cuts
    .filter((value) => value > 0.05 && (!duration || value < duration - 0.05))
    .sort((a, b) => a - b);
}

function renderTimeline() {
  const project = state.project;
  const show = !!(project && Number(project.mode) === 1 && (project.audio || project.analysis?.waveform?.original?.length));
  $("#timeline").classList.toggle("hidden", !show);
  if (!show) {
    state.cuts = [];
    return;
  }
  const duration = audioDuration();
  const segments = project.segments || [];
  const waveReady = !!project.analysis?.waveform?.original?.length;
  $("#timeline-meta").textContent = waveReady
    ? `${fmtClock(duration)} · ${segments.length} 段${project.asr?.enabled ? ` · ASR ${project.asr.model || ""}` : ""}`
    : `待分析 · 音频 ${fmtClock(duration)}`;
  syncAudioSource(project);
  state.cuts = internalCuts(project);
  drawWave();
  renderCutHandles();
  if (state.playhead > duration) state.playhead = 0;
  positionPlayhead();
  updatePlayLabel();
}

function syncAudioSource(project) {
  const el = $("#av-audio");
  if (!el) return;
  const src = mediaAudioUrl(project);
  if (!src) {
    el.pause();
    el.removeAttribute("src");
    delete el.dataset.src;
    try { el.load(); } catch { /* ignore */ }
    return;
  }
  if (el.dataset.src !== src) {
    el.dataset.src = src;
    el.src = src;
    state.audioFailed = false;
  }
}

function ensureWaveform() {
  if (waveform) return waveform;
  const canvas = $("#wave-canvas");
  const wrap = $("#wave-wrap");
  if (!canvas || !wrap) return null;
  waveform = new Waveform(canvas, wrap);
  waveform.height = 96;
  waveform.canvas.style.height = "96px";
  waveform.onSeek = (seconds) => {
    setPlayhead(seconds);
    const audio = $("#av-audio");
    if (audio && audio.dataset.src && audio.readyState >= 1) {
      try { audio.currentTime = state.playhead; } catch { /* ignore */ }
    }
  };
  waveform.onBoundary = async (cuts) => {
    const project = state.project;
    if (!project) return;
    try {
      const data = await api.cuts(project.id, cuts.map((value) => Number(Number(value).toFixed(3))));
      applyProject(data.project, { fill: false });
      await loadProjects();
      toast("切点已更新", "ok");
    } catch (err) {
      toast(`切点更新失败：${err.message}`, "err", 7000);
      drawWave();
    }
  };
  waveform.onSplit = (time) => {
    setPlayhead(time, { seek: false });
    splitAtPlayhead();
  };
  waveform.onDeleteBoundary = (index) => mergeDown(index);
  return waveform;
}

function drawWave() {
  const wf = ensureWaveform();
  if (!wf) return;
  const project = state.project;
  const peaks = project?.analysis?.waveform?.original;
  const duration = audioDuration();
  const segments = (project?.segments || []).map((seg, index) => {
    const start = segmentStart(project, index);
    return { start, end: start + segSeconds(seg) };
  });
  const active = !!(state.run && RUN_ACTIVE.has(state.run.status));
  wf.editable = Number(project?.mode) === 1 && !active;
  wf.setData({
    peaks: Array.isArray(peaks) ? peaks : [],
    segments: Number(project?.mode) === 1 ? segments : [],
    duration: Array.isArray(peaks) && peaks.length ? duration : 0,
  });
  wf.setPlayhead(state.playhead);
}

function renderCutHandles() {
  // 切点由 Waveform 画布统一绘制（移植自 MV 工作台），DOM 手柄只保留占位
}

function positionPlayhead() {
  if (waveform) waveform.setPlayhead(state.playhead);
  const label = $("#playhead-label");
  if (label) label.textContent = `${fmtClock(state.playhead)} / ${fmtClock(Math.max(audioDuration(), 0))}`;
}

function setPlayhead(seconds, { seek = true } = {}) {
  const duration = audioDuration();
  state.playhead = clamp(num(seconds), 0, duration || 0);
  positionPlayhead();
  if (seek) {
    const el = $("#av-audio");
    if (el && el.dataset.src && el.readyState >= 1) {
      try {
        el.currentTime = state.playhead;
      } catch {
        /* ignore */
      }
    }
  }
}

function updatePlayLabel() {
  const el = $("#av-audio");
  const label = $("#play-btn-label");
  if (!label) return;
  label.textContent = el && !el.paused ? "暂停" : "播放";
}

function timeFromClientX(clientX, rect, duration) {
  const ratio = clamp((clientX - rect.left) / Math.max(1, rect.width), 0, 1);
  return ratio * Math.max(duration, 0);
}

function togglePlay() {
  const el = $("#av-audio");
  if (!el || !el.dataset.src) {
    toast("音频不可播放（尚未上传，或后端媒体接口未就绪）", "info");
    return;
  }
  if (el.paused) {
    if (state.playhead >= audioDuration() - 0.05) setPlayhead(0);
    el.play().catch((err) => toast(`播放失败：${err.message}`, "err"));
  } else {
    el.pause();
  }
}

function beginCutDrag(handle, event) {
  const index = Number(handle.dataset.cutIndex);
  const wrap = $("#wave-wrap");
  const duration = audioDuration();
  if (!wrap || !Number.isFinite(index) || !duration) return;
  event.preventDefault();
  event.stopPropagation();
  const origin = [...state.cuts];
  const maxSeconds = Math.max(3, num(state.project?.max_seconds, 12));
  const rect = wrap.getBoundingClientRect();
  handle.classList.add("dragging");
  let pending = origin[index];
  const tip = handle.querySelector(".wave-cut-tip");
  const onMove = (moveEvent) => {
    const seconds = timeFromClientX(moveEvent.clientX, rect, duration);
    const prev = index > 0 ? origin[index - 1] : 0;
    const next = index < origin.length - 1 ? origin[index + 1] : duration;
    const low = Math.max(prev + 3, next - maxSeconds) + 0.01;
    const high = Math.min(next - 3, prev + maxSeconds) - 0.01;
    const value = clamp(seconds, Math.min(low, high), Math.max(low, high));
    pending = value;
    handle.style.left = `${(value / duration) * 100}%`;
    if (tip) tip.textContent = fmtClock(value);
  };
  const onUp = async () => {
    document.removeEventListener("pointermove", onMove);
    document.removeEventListener("pointerup", onUp);
    handle.classList.remove("dragging");
    if (!Number.isFinite(pending) || Math.abs(pending - origin[index]) < 0.02) {
      renderCutHandles();
      return;
    }
    const next = origin.map((value, i) => (i === index ? pending : value)).sort((a, b) => a - b);
    renderCutHandles();
    await saveCuts(next);
  };
  document.addEventListener("pointermove", onMove);
  document.addEventListener("pointerup", onUp);
}

async function saveCuts(cuts) {
  const p = state.project;
  if (!p) return;
  const rounded = cuts.map((value) => Number(value.toFixed(3))).sort((a, b) => a - b);
  await withBusy(null, async () => {
    const data = await api.cuts(p.id, rounded);
    applyProject(data.project, { fill: false });
    await loadProjects();
    toast("切点已保存，分段已更新", "ok");
  }).catch((err) => toast(`切点保存失败：${err.message}`, "err", 7000));
}

async function splitAtPlayhead() {
  const p = state.project;
  if (!p || Number(p.mode) !== 1) return;
  if (!p.analysis?.waveform?.original?.length) {
    toast("请先分析音频", "info");
    return;
  }
  const segments = p.segments || [];
  const time = state.playhead;
  let index = -1;
  for (let i = 0; i < segments.length; i += 1) {
    const start = segmentStart(p, i);
    const end = start + segSeconds(segments[i]);
    if (time > start + 0.05 && time < end - 0.05) {
      index = i;
      break;
    }
  }
  if (index < 0) {
    toast("播放头不在任何段内部，请把播放头拖到某段中间", "info");
    return;
  }
  const start = segmentStart(p, index);
  const end = start + segSeconds(segments[index]);
  if (time - start < 3 || end - time < 3) {
    toast("切点两侧都至少需要 3 秒", "err", 6000);
    return;
  }
  await withBusy($("#btn-split"), async () => {
    const data = await api.splitSegment(p.id, index, Number(time.toFixed(3)));
    applyProject(data.project, { fill: false });
    await loadProjects();
    toast("已在播放头切分", "ok");
  }).catch((err) => toast(`切分失败：${err.message}`, "err", 7000));
}

async function mergeDown(index) {
  const p = state.project;
  if (!p) return;
  const segments = p.segments || [];
  if (index >= segments.length - 1) {
    toast("已经是最后一段，无法合并下段", "info");
    return;
  }
  const ok = await confirmDialog(
    "合并下段",
    `把第 ${index + 1} 段与第 ${index + 2} 段合并成一段。合并后这两段已生成的片段可能需要重新生成。继续？`,
    "合并"
  );
  if (!ok) return;
  await withBusy(null, async () => {
    const data = await api.mergeSegment(p.id, index);
    applyProject(data.project, { fill: false });
    await loadProjects();
    toast("已合并下段", "ok");
  }).catch((err) => toast(`合并失败：${err.message}`, "err", 7000));
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
  } catch {
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
    state.anchorPlain = data.anchor_plain || {};
  } catch {
    state.options = {};
    state.generationDefaults = {};
    state.anchorPlain = {};
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
      const mode = clamp(Number(item.mode) || 2, 1, 3);
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
    state.playhead = 0;
    state.beatsDraft = {};
    state.lintOverride = {};
    state.pendingAudio = null;
    state.pendingVoice = null;
    applyProject(data.project, { fill: true });
    localStorage.setItem(LAST_KEY, data.project.id);
    // 设计层单独再读一次 /design：项目 JSON 可能落后于最近一次设计保存
    try {
      const designData = await api.design(data.project.id);
      if (designData?.design) {
        state.project.design = designData.design;
        fillDesign(designOf(state.project));
        state.designLoadedFor = state.project.id;
      }
    } catch {
      /* 设计接口失败时沿用项目里的 design */
    }
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

function stopAudioPlayback() {
  const el = $("#av-audio");
  if (!el) return;
  try { el.pause(); } catch { /* ignore */ }
  el.removeAttribute("src");
  delete el.dataset.src;
  try { el.load(); } catch { /* ignore */ }
}

function clearProject({ silent = false } = {}) {
  stopPolling();
  state.project = null;
  state.run = null;
  state.pendingVoice = null;
  state.pendingAudio = null;
  state.finalKey = "";
  state.playhead = 0;
  state.cuts = [];
  state.beatsDraft = {};
  state.lintOverride = {};
  state.designLoadedFor = "";
  stopAudioPlayback();
  localStorage.removeItem(LAST_KEY);
  resetForm();
  fillDesign(designBase(state.mode));
  renderProjectContext();
  renderSegments();
  renderRefs();
  renderRun(null);
  renderFinal();
  renderTimeline();
  renderProjectList();
  syncButtons();
  if (!silent) toast("已清空当前选择，可新建作品", "info");
}

/* ---------------- 创建 / 保存 / 分段 ---------------- */

async function startNewProject() {
  if (isRunActive(state.run)) {
    toast("当前有生成任务在运行，请先停止任务再新建作品。", "warn", 5000);
    return;
  }
  if (!state.project) {
    await createProject();
    return;
  }
  const ok = await confirmDialog(
    "新建空白作品",
    "会新建一个空白作品。当前作品可在「最近作品」随时打开；表单里未保存的修改会丢弃。",
    "新建空白作品"
  );
  if (!ok) return;
  clearProject({ silent: true });
  await createProject();
}

async function createProject() {
  const form = readForm();
  form.name = form.name || fmtTodayName();
  const generation = readGeneration();
  const voiceFile = state.mode === 2 ? state.pendingVoice : null;
  await withBusy($("#btn-create"), async () => {
    const data = await api.createProject({ ...form, generation: JSON.stringify(generation) }, voiceFile);
    state.pendingVoice = null;
    state.playhead = 0;
    state.beatsDraft = {};
    state.lintOverride = {};
    applyProject(data.project, { fill: true });
    localStorage.setItem(LAST_KEY, data.project.id);
    await loadProjects();
    toast(`作品「${data.project.name}」已创建`, "ok");
    if (state.mode === 1) {
      toast(state.pendingAudio ? "点「上传并分析」开始分段" : "上传音频后点「上传并分析」", "info", 6000);
      return;
    }
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
    applyProject(project, { fill: true, segments: false });
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
  if (Number(p.mode) === 1) {
    toast("模式 1 请用「上传并分析」按波形分段", "info");
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
      state.beatsDraft = {};
      state.lintOverride = {};
      applyProject(data.project, { fill: false });
      await loadProjects();
      if (!silent) {
        const list = $("#seg-list");
        if (list) list.scrollIntoView({ behavior: "smooth", block: "start" });
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

/* ---------------- 提示词元信息 / lint ---------------- */

function lintSummary(seg, index = seg?.index) {
  const override = state.lintOverride[index];
  const lint = override?.lint || seg?.prompt_meta?.lint || null;
  const errors = Array.isArray(lint?.errors) ? lint.errors : [];
  const warnings = Array.isArray(lint?.warnings) ? lint.warnings : [];
  if (!lint) return { lint: null, errors, warnings, cls: "idle", text: "未 lint" };
  if (errors.length) return { lint, errors, warnings, cls: "err", text: `lint ${errors.length} 错误` };
  if (warnings.length) return { lint, errors, warnings, cls: "warn", text: `lint ${warnings.length} 警告` };
  return { lint, errors, warnings, cls: "ok", text: "lint 通过" };
}

function lintItemHtml(item, kind) {
  const code = item && typeof item === "object" ? item.code || "" : "";
  const message = item && typeof item === "object" ? item.message || JSON.stringify(item) : String(item);
  return `<div class="av-lint-item ${kind}">${code ? `<b>${esc(code)}</b>` : ""}${esc(message)}</div>`;
}

function promptMetaInner(seg) {
  const meta = seg?.prompt_meta || {};
  const { lint, errors, warnings, cls, text } = lintSummary(seg);
  const sourceKey = seg?.prompt_source || "fallback";
  const source = PROMPT_SOURCE_LABEL[sourceKey] || sourceKey;
  const sourceClass = sourceKey === "fallback" ? "fallback" : sourceKey === "manual" ? "manual" : "";
  const summary = String(meta.summary_line || "");
  const chars = num(lint?.chars ?? meta.chars) || String(seg?.prompt || "").length;
  const speech = num(lint?.speech_chars ?? meta.speech_chars);
  const cps = num(meta.target_cps);
  const effects = num(meta.effect_count);
  const stats = [];
  if (chars) stats.push(`全文 ${chars} 字`);
  if (speech) stats.push(`台词 ${speech} 字`);
  if (cps) stats.push(`语速 ${cps.toFixed(1)} 字/秒`);
  if (meta.effect_count !== undefined && meta.effect_count !== null) stats.push(`效果 ${effects} 个`);
  const detailItems = [
    ...errors.map((item) => lintItemHtml(item, "error")),
    ...warnings.map((item) => lintItemHtml(item, "warn")),
  ];
  const lintDetails = detailItems.length
    ? `<details class="av-lint">
        <summary><svg><use href="#i-chevron"/></svg><span>lint 明细（${errors.length} 错误 / ${warnings.length} 警告）</span></summary>
        <div class="av-lint-list">${detailItems.join("")}</div>
      </details>`
    : "";
  const stale = !!meta.stale;
  return `<div class="av-prompt-meta-top">
      <span class="prompt-source ${sourceClass}">${esc(source)}</span>
      ${stale ? '<span class="pill av-stale-pill" title="节拍或设计已变更，提示词可能已过期；重新生成或按 lint 修复即可更新">可能已过期</span>' : ""}
      ${summary ? `<span class="av-summary-line">${esc(summary)}</span>` : ""}
    </div>
    <div class="av-prompt-stats">
      ${stats.map((item) => `<span>${esc(item)}</span>`).join("")}
      <span class="lint-pill ${cls}">${esc(text)}</span>
    </div>
    ${lintDetails}`;
}

function refRoleLabel(role) {
  return ({ performer: "人物", environment: "场景", appearance: "服装",
    subject: "配角/道具", other: "其他" })[role] || role || "其他";
}

function noteMaterialHint() {
  const p = state.project;
  if (!p) return "";
  const refCount = (p.refs || []).length;
  const mode = Number(p.mode) || 3;
  const voiceCount = mode === 2
    ? (p.voices?.length || (p.voice?.filename ? 1 : 0)) : 0;
  const parts = [];
  if (refCount) parts.push(`图片 ${refCount} 张（@图1…@图${refCount}）`);
  if (voiceCount) parts.push(`音色 ${voiceCount} 个（@音频1…@音频${voiceCount}）`);
  return parts.length ? "可用：" + parts.join(" · ") : "当前没有可引用素材";
}

function toggleSegmentAudition(index) {
  const p = state.project;
  const audio = $("#av-audio");
  if (!p || !audio) return;
  if (state.auditionIndex === index) {
    audio.pause();
    state.auditionIndex = null;
    renderSegments();
    return;
  }
  syncAudioSource(p);
  state.auditionIndex = index;
  state.auditionEnd = segmentEnd(p, index);
  audio.currentTime = Math.max(0, segmentStart(p, index));
  audio.play().catch(() => {
    state.auditionIndex = null;
    toast("无法播放音频，请检查源文件", "err");
    renderSegments();
  });
  renderSegments();
}

function stopSegmentAudition() {
  if (state.auditionIndex === null) return;
  $("#av-audio")?.pause();
  state.auditionIndex = null;
  renderSegments();
}

function openTakePreview(project, seg, takeId) {
  const take = (seg?.takes || []).find((item) => String(item.take_id) === String(takeId));
  if (!take?.file) { toast("这条片段还没有视频文件", "warn"); return; }
  const modal = $("#modal-take-preview");
  const video = $("#take-preview-video");
  $("#take-preview-title").textContent = `第 ${Number(seg.index) + 1} 段 · 片段预览`;
  video.src = mediaUrl(project.id, take.file, take.created);
  modal.hidden = false;
  const close = () => {
    video.pause();
    video.removeAttribute("src");
    modal.hidden = true;
    $$("[data-close]", modal).forEach((btn) => btn.removeEventListener("click", close));
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (event) => { if (event.key === "Escape") close(); };
  $$("[data-close]", modal).forEach((btn) => btn.addEventListener("click", close));
  document.addEventListener("keydown", onKey);
}

function openImagePreview(src, title) {
  const modal = $("#modal-image-preview");
  const img = $("#image-preview-img");
  if (!modal || !img || !src) return;
  img.src = src;
  img.alt = title || "参考图";
  $("#image-preview-title").textContent = title || "参考图预览";
  modal.hidden = false;
  const close = () => {
    img.removeAttribute("src");
    modal.hidden = true;
    $$("[data-close]", modal).forEach((btn) => btn.removeEventListener("click", close));
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (event) => { if (event.key === "Escape") close(); };
  $$("[data-close]", modal).forEach((btn) => btn.addEventListener("click", close));
  document.addEventListener("keydown", onKey);
}

function segmentRefsHtml(project, seg) {
  const refs = project.refs || [];
  if (!refs.length) return "";
  const selected = Array.isArray(seg.ref_ids)
    ? seg.ref_ids.filter((value) => Number.isInteger(value) && value >= 0 && value < refs.length)
    : refs.map((_, index) => index);
  const mapping = selected.length
    ? selected.map((global, local) => `<Picture ${local + 1}>=图${global + 1}`).join(" · ")
    : "本段没有启用任何参考图";
  return `<div class="av-seg-refs">
    <div class="av-seg-refs-head"><span>本段参考图</span>
      <span class="hint">默认全用；点缩略图可预览；只把勾选的图发给 AI / H3（编号即入参顺序）</span></div>
    <div class="av-ref-check-list">
      ${refs.map((ref, index) => {
        const thumb = mediaUrl(project.id, `refs/${ref.filename}`, ref.added);
        const title = `图${index + 1} · ${ref.title || ref.filename}`;
        return `<span class="av-ref-item">
          <button class="av-ref-thumb-btn" type="button" data-action="ref-preview"
            data-src="${esc(thumb)}" data-title="${esc(title)}" title="预览这张参考图">
            <img class="av-ref-thumb" src="${esc(thumb)}" alt="${esc(title)}" loading="lazy">
          </button>
          <label class="av-ref-check">
            <input type="checkbox" data-ref-check data-index="${seg.index}" data-ref="${index}"
              ${selected.includes(index) ? "checked" : ""}>
            <span>图${index + 1} · ${esc(ref.title || ref.filename)}<em>${refRoleLabel(ref.role)}</em></span>
          </label>
        </span>`;
      }).join("")}
    </div>
    <div class="av-ref-map">${esc(mapping)}</div>
  </div>`;
}

function segmentVoicesHtml(project, seg) {
  if (Number(project.mode) !== 2) return "";
  const voices = (project.voices || []).length
    ? project.voices
    : (project.voice ? [project.voice] : []);
  if (!voices.length) {
    return `<div class="av-seg-voices"><div class="av-seg-voices-head">
      <span>本段音色</span><span class="hint">还没有音色：先在左侧「参考音色」上传</span></div></div>`;
  }
  const note = String(seg.note || "");
  const prompt = String(seg.prompt || "");
  return `<div class="av-seg-voices">
    <div class="av-seg-voices-head"><span>本段音色</span>
      <span class="hint">点「试听」听参考音色；谁用哪个写在创作要求里</span></div>
    <div class="av-voice-mini-list">
      ${voices.map((voice, index) => {
        const used = new RegExp(`@音频${index + 1}\\b`).test(note)
          || new RegExp(`<Audio ${index + 1}>`).test(prompt);
        return `<span class="av-voice-mini ${used ? "used" : ""}" data-seg-voice-id="${esc(String(voice.id || ""))}" data-seg-voice-index="${index}">
          <span>音频${index + 1} · ${esc(voice.name || voice.filename || "音色")}</span>
          <button class="btn ghost sm" type="button" data-action="seg-voice-audition"
            data-index="${seg.index}" data-voice-id="${esc(String(voice.id || ""))}" data-voice-index="${index}">试听</button>
          ${used ? "<em>本段引用</em>" : ""}
        </span>`;
      }).join("")}
    </div>
  </div>`;
}

function segmentNoteHtml(seg) {
  const mode = Number(state.project?.mode) || 3;
  return `<div class="av-seg-note">
    <div class="av-seg-note-head"><span>本段创作要求</span>
      <span class="hint">优先级最高 · 支持 @图N${mode === 2 ? " / @音频N" : ""}</span></div>
    <textarea class="av-note-area" data-note-index="${seg.index}" spellcheck="false"
      placeholder="例：让 @图1 的角色${mode === 2 ? "用 @音频1 的音色" : ""}在 @图2 的背景下讲话；镜头慢慢推近。">${esc(seg.note || "")}</textarea>
    <div class="av-note-tools">
      <button class="btn ghost sm" type="button" data-action="note-mention" data-index="${seg.index}">@ 引用素材</button>
      <span class="av-note-map">${esc(noteMaterialHint())}</span>
    </div>
  </div>`;
}

const persistSegmentRefs = debounce(async (index, refIds) => {
  const p = state.project;
  if (!p) return;
  try {
    const data = await api.patchSegment(p.id, index, { ref_ids: refIds });
    state.project = data.project;
    renderSegments();
    toast(refIds === null ? "本段已改为使用全部参考图"
      : `本段参考图已更新（${refIds.length} 张）`, "info", 3200);
  } catch (error) {
    toast(`本段参考图保存失败：${error.message}`, "err", 6000);
  }
}, 500);

const persistSegmentNote = debounce(async (index, value) => {
  const p = state.project;
  if (!p) return;
  try {
    const data = await api.patchSegment(p.id, index, { note: value });
    state.project = data.project;
  } catch (error) {
    toast(`本段创作要求保存失败：${error.message}`, "err", 6000);
  }
}, 700);

function mentionCandidates() {
  const p = state.project;
  const items = [];
  (p?.refs || []).forEach((ref, i) => {
    items.push({ type: "picture", index: i + 1, token: `@图${i + 1}`,
      label: `图${i + 1} · ${ref.title || ref.filename}`,
      sub: refRoleLabel(ref.role),
      thumb: mediaUrl(p.id, `refs/${ref.filename}`, ref.added) });
  });
  if (Number(p?.mode) === 2) {
    const voices = p.voices?.length
      ? p.voices
      : (p.voice?.filename ? [{ name: "默认音色", filename: p.voice.filename, uploaded: p.voice.uploaded }] : []);
    voices.forEach((voice, i) => {
      items.push({ type: "audio", index: i + 1, token: `@音频${i + 1}`,
        label: `音频${i + 1} · ${voice.name || "音色"}`,
        sub: "音色素材（不是人物身份绑定）",
        audio: voiceFileUrl(p, voice) });
    });
  }
  return items;
}

function renderMentionList() {
  const list = $("#mention-list");
  if (!list) return;
  list.innerHTML = state.mention.items.map((item, i) => `
    <div class="mention-item ${i === state.mention.active ? "active" : ""}" data-mention-index="${i}">
      ${item.thumb ? `<img src="${esc(item.thumb)}" alt="">`
        : `<span class="mi-icon"><svg><use href="#i-wave"/></svg></span>`}
      <span class="mi-body"><span class="mi-label">${esc(item.label)}</span>
        <span class="mi-sub">${esc(item.sub || "")}</span></span>
    </div>`).join("");
}

function renderMentionPreview() {
  const box = $("#mention-preview");
  const item = state.mention.items[state.mention.active];
  if (!box) return;
  box.innerHTML = !item ? '<span class="hint">选择素材可预览</span>'
    : item.thumb ? `<img src="${esc(item.thumb)}" alt="${esc(item.label)}">`
    : `<audio controls preload="none" src="${esc(item.audio || "")}"></audio>`;
}

function closeMentionPop() {
  $("#mention-pop")?.classList.add("hidden");
  const insertBtn = $("#btn-mention-insert");
  if (insertBtn) insertBtn.disabled = true;
  state.mention.target = null;
  state.mention.onPick = null;
}

function insertAtCaret(textarea, token, { trimAt = false } = {}) {
  if (!textarea) return;
  let start = textarea.selectionStart ?? textarea.value.length;
  const end = textarea.selectionEnd ?? start;
  let value = textarea.value;
  if (trimAt && start > 0 && value.slice(start - 1, start) === "@") {
    value = value.slice(0, start - 1) + value.slice(start);
    start -= 1;
  }
  textarea.value = value.slice(0, start) + token + value.slice(end);
  textarea.selectionStart = textarea.selectionEnd = start + token.length;
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
  textarea.focus();
}

function openMentionPop(target, onPick, { trimAt = false } = {}) {
  if (!target) return;
  const items = mentionCandidates();
  if (!items.length) { toast("当前没有可引用的素材（先上传参考图/音色）", "warn", 4200); return; }
  state.mention = { items, active: 0, target, onPick, trimAt };
  const pop = $("#mention-pop");
  renderMentionList();
  renderMentionPreview();
  pop.classList.remove("hidden");
  const insertBtn = $("#btn-mention-insert");
  if (insertBtn) insertBtn.disabled = false;
  const rect = target.getBoundingClientRect();
  const width = 620;
  pop.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - width - 12))}px`;
  const top = rect.bottom + 6;
  pop.style.top = `${top + 300 > window.innerHeight ? Math.max(8, rect.top - 300) : top}px`;
}

function mentionKeydown(event) {
  const pop = $("#mention-pop");
  if (!pop || pop.classList.contains("hidden")) return false;
  if (event.key === "Escape") { event.preventDefault(); closeMentionPop(); return true; }
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const delta = event.key === "ArrowDown" ? 1 : -1;
    state.mention.active = (state.mention.active + delta + state.mention.items.length)
      % state.mention.items.length;
    renderMentionList();
    renderMentionPreview();
    return true;
  }
  if (event.key === "Enter") {
    event.preventDefault();
    const item = state.mention.items[state.mention.active];
    if (item && state.mention.onPick) state.mention.onPick(item.token);
    closeMentionPop();
    return true;
  }
  return false;
}

function promptElapsed(index) {
  const started = state.generatingPrompt[index];
  return started ? Math.max(0, Math.round((Date.now() - started) / 1000)) : 0;
}

function promptBlockHtml(project, seg) {
  const prompt = (seg.prompt || "").trim();
  const generating = Boolean(state.generatingPrompt[seg.index]);
  const overlay = state.designGraphicsMode === "overlay";
  const stale = !!seg.prompt_meta?.stale;
  const anchor = Number(project?.mode) === 1
    && String((project?.generation || {}).stitch || "hard") === "anchor";
  const { cls, text } = prompt ? lintSummary(seg) : { cls: "info", text: "未生成" };
  const status = generating ? `生成中（${promptElapsed(seg.index)}s）` : text;
  return `<details class="av-seg-prompt" ${prompt ? "" : "open"}>
    <summary>
      <svg><use href="#i-chevron"/></svg>
      <span>提示词</span>
      <span class="lint-pill ${generating ? "info" : cls}">${esc(status)}</span>
      ${stale ? '<span class="pill av-stale-pill" title="节拍/设计/创作要求已变更，提示词可能已过期">可能已过期</span>' : ""}
      <span class="grow"></span>
      <span class="av-prompt-toggle">展开编辑</span>
    </summary>
    <div class="av-prompt-body">
      <p class="av-prompt-note">即将发给视频模型的提示词${anchor ? " · 首尾帧固定模板" : " · Ref2VA 模板"} · 润色可选，运行不会自动润色</p>
      <div class="av-prompt-meta" data-meta-index="${seg.index}">${promptMetaInner(seg)}</div>
      <p class="av-overlay-inline" ${overlay ? "" : "hidden"}>叠加模式：提示词只写人物表演 / 构图 / 光，文字与 logo 由后期确定性叠加，不要求生成画面文字。</p>
      <textarea class="av-prompt-area" spellcheck="false" data-prompt-index="${seg.index}"
        placeholder="可以直接手写/粘贴提示词；也可以点「润色」，让 AI 按全局 + 本段创作要求写…">${esc(seg.prompt || "")}</textarea>
      <div class="av-prompt-bar">
        <span class="av-prompt-hint">${generating
          ? "AI 正在写，可继续等待；完成后会自动填入并展开"
          : (prompt ? "保存后重新生成会用新提示词；面板内容就是实际发送版本" : "提示词可以为空：生成视频时会先按内置模板/LLM 自动补上")}</span>
        <div class="av-prompt-bar-actions">
          <button class="btn ghost sm${generating ? " generating" : ""}" type="button" data-action="seg-prompt" data-index="${seg.index}" ${generating ? "disabled" : ""}>${generating ? "生成中…" : (anchor ? "填入模板（可选）" : "润色（可选）")}</button>
          <button class="btn ghost sm" type="button" data-action="save-prompt" data-index="${seg.index}">保存</button>
          <button class="btn ghost sm" type="button" data-action="lint-prompt" data-index="${seg.index}">Lint 检查</button>
          <button class="btn ghost sm" type="button" data-action="repair-prompt" data-index="${seg.index}">
            <svg><use href="#i-spark"/></svg>按 lint 修复
          </button>
        </div>
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

/* ---------------- 节拍表 ---------------- */

function beatOptions() {
  const catalog = state.catalog || {};
  const rawJobs = Array.isArray(catalog.semantic_jobs) ? catalog.semantic_jobs : [];
  const effectPool = [];
  rawJobs.forEach((job) => {
    ["primary", "accent"].forEach((key) => {
      const list = job?.[key];
      if (Array.isArray(list)) list.forEach((value) => effectPool.push(value));
      else if (typeof list === "string") effectPool.push(list);
    });
  });
  return {
    jobs: normalizeOptions(rawJobs, BEAT_JOBS),
    effects: normalizeOptions(effectPool, BEAT_EFFECTS),
    anchors: normalizeOptions(catalog.anchors, BEAT_ANCHORS),
    layers: normalizeOptions(catalog.layers, BEAT_LAYERS),
  };
}

function beatsFor(index, seg) {
  const draft = state.beatsDraft[index];
  if (Array.isArray(draft)) return draft;
  return Array.isArray(seg?.beats) ? seg.beats : [];
}

function emptyBeat() {
  return {
    index: 0,
    trigger: "",
    job: "hook",
    primary_effect: "info_card",
    accent_effect: "",
    anchor: "right_side",
    layer: "screen",
    visible_copy: "",
    lifecycle: { entrance: "", landing: "", hold: "", exit: "", recovery: "" },
    attention_target: "lens",
    gesture: "",
    risk: "",
  };
}

function normalizeBeat(beat) {
  const base = emptyBeat();
  if (!isPlainObject(beat)) return base;
  const merged = { ...base, ...beat };
  merged.lifecycle = { ...base.lifecycle, ...(isPlainObject(beat.lifecycle) ? beat.lifecycle : {}) };
  return merged;
}

function beatRowHtml(beat, index, options) {
  const data = normalizeBeat(beat);
  const life = data.lifecycle || {};
  const lifeFields = [
    ["entrance", "入场"],
    ["landing", "落定"],
    ["hold", "停留"],
    ["exit", "退场"],
    ["recovery", "恢复"],
  ];
  const lifeCells = lifeFields
    .map(
      ([key, label]) => `<td data-beat-col="life"><input data-beat="lifecycle.${key}" value="${esc(life[key] || "")}" placeholder="${label}" title="${label}"></td>`
    )
    .join("");
  return `<tr data-beat-row>
    <td class="av-beat-no">${pad(index + 1)}</td>
    <td data-beat-col="trigger"><input data-beat="trigger" value="${esc(data.trigger || "")}" placeholder="必须在台词里"></td>
    <td><select data-beat="job">${selectHtml(options.jobs, data.job)}</select></td>
    <td><select data-beat="primary_effect">${selectHtml(options.effects, data.primary_effect)}</select></td>
    <td><select data-beat="accent_effect">${selectHtml(options.effects, data.accent_effect, { placeholder: "（无）" })}</select></td>
    <td><select data-beat="anchor">${selectHtml(options.anchors, data.anchor)}</select></td>
    <td><select data-beat="layer">${selectHtml(options.layers, data.layer)}</select></td>
    <td data-beat-col="copy"><input data-beat="visible_copy" value="${esc(data.visible_copy || "")}" placeholder="画面文字"></td>
    ${lifeCells}
    <td><select data-beat="attention_target">${selectHtml(ATTENTION_TARGETS.map(([value, label]) => ({ value, label })), data.attention_target)}</select></td>
    <td><input data-beat="gesture" value="${esc(data.gesture || "")}" placeholder="手势"></td>
    <td><input data-beat="risk" value="${esc(data.risk || "")}" placeholder="风险"></td>
    <td><button class="beat-del" type="button" data-action="beat-del" title="删除这一行"><svg><use href="#i-close"/></svg></button></td>
  </tr>`;
}

function beatsBlockHtml(project, seg) {
  const beats = beatsFor(seg.index, seg);
  const options = beatOptions();
  const rows = beats.map((beat, index) => beatRowHtml(beat, index, options)).join("");
  const empty = beats.length ? "" : `<p class="av-beats-empty">暂无节拍：生成提示词时 AI 会自动设计节拍，也可点「AI 建议节拍」后手动调整。</p>`;
  return `<details class="av-beats" data-beats-index="${seg.index}">
    <summary>
      <svg><use href="#i-chevron"/></svg>
      <span>节拍表</span>
      <span class="pill beats-count">${beats.length} 条</span>
      <span class="grow"></span>
      <span class="av-prompt-toggle">展开编辑</span>
    </summary>
    <div class="av-beats-body">
      ${empty}
      <div class="av-beats-scroll">
        <table class="av-beats-table">
          <thead><tr>
            <th>#</th><th>触发词</th><th>职能</th><th>主效</th><th>辅效</th><th>锚点</th><th>层级</th><th>可见文案</th>
            <th>入场</th><th>落定</th><th>停留</th><th>退场</th><th>恢复</th><th>注意目标</th><th>手势</th><th>风险</th><th></th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <div class="av-beats-actions">
        <button class="btn ghost sm" type="button" data-action="beat-add"><svg><use href="#i-plus"/></svg>新增行</button>
        <button class="btn ghost sm" type="button" data-action="beats-suggest"><svg><use href="#i-spark"/></svg>AI 建议节拍</button>
        <span class="grow"></span>
        <button class="btn primary sm" type="button" data-action="beats-save"><svg><use href="#i-check"/></svg>保存节拍</button>
      </div>
    </div>
  </details>`;
}

function captureBeatsDrafts() {
  $$("#seg-list .av-beats[open]").forEach((details) => {
    const index = Number(details.dataset.beatsIndex);
    const rows = $$("tbody tr[data-beat-row]", details);
    state.beatsDraft[index] = rows.map((row, i) => collectBeatRow(row, i));
  });
}

function collectBeatRow(row, index) {
  const beat = { index };
  $$("[data-beat]", row).forEach((el) => {
    const value = String(el.value ?? "").trim();
    setByPath(beat, el.dataset.beat, value);
  });
  return normalizeBeat(beat);
}

function renumberBeatRows(details) {
  $$("tbody tr[data-beat-row]", details).forEach((row, i) => {
    const cell = row.querySelector(".av-beat-no");
    if (cell) cell.textContent = pad(i + 1);
  });
  const count = $$("tbody tr[data-beat-row]", details).length;
  const pill = details.querySelector(".beats-count");
  if (pill) pill.textContent = `${count} 条`;
  const empty = details.querySelector(".av-beats-empty");
  if (empty) empty.hidden = count > 0;
  const tbody = details.querySelector("tbody");
  if (tbody && !count) {
    const hint = details.querySelector(".av-beats-body > .av-beats-empty");
    if (hint) hint.hidden = false;
  }
}

function addBeatRow(index) {
  const details = document.querySelector(`.av-beats[data-beats-index="${index}"]`);
  if (!details) return;
  details.open = true;
  const tbody = details.querySelector("tbody");
  if (!tbody) return;
  tbody.insertAdjacentHTML("beforeend", beatRowHtml(emptyBeat(), tbody.children.length, beatOptions()));
  renumberBeatRows(details);
}

function deleteBeatRow(button) {
  const row = button.closest("tr[data-beat-row]");
  const details = button.closest(".av-beats");
  if (!row || !details) return;
  row.remove();
  renumberBeatRows(details);
}

function collectBeats(index) {
  const details = document.querySelector(`.av-beats[data-beats-index="${index}"]`);
  if (!details) return [];
  return $$("tbody tr[data-beat-row]", details).map((row, i) => collectBeatRow(row, i));
}

async function saveBeats(index) {
  const p = state.project;
  if (!p) return;
  const beats = collectBeats(index);
  await withBusy(null, async () => {
    const data = await api.segmentBeats(p.id, index, beats);
    if (data.project) state.project = data.project;
    delete state.beatsDraft[index];
    renderSegments();
    renderProjectContext();
    updateSummary(state.project);
    syncButtons();
    openSegmentDetails(index, ".av-beats");
    toast(`第 ${index + 1} 段节拍已保存（${beats.length} 条）`, "ok");
  }).catch((err) => toast(`节拍保存失败：${err.message}`, "err", 6000));
}

async function suggestBeats(index) {
  const p = state.project;
  if (!p) {
    toast("请先新建或打开作品", "info");
    return;
  }
  await withBusy(null, async () => {
    const data = await api.suggestDesign(p.id, { mode: "beats", segment_index: index });
    const beats = Array.isArray(data?.suggestion?.beats) ? data.suggestion.beats : [];
    const warnings = Array.isArray(data?.warnings) ? data.warnings : [];
    const details = document.querySelector(`.av-beats[data-beats-index="${index}"]`);
    if (!details) return;
    details.open = true;
    const tbody = details.querySelector("tbody");
    if (!tbody) return;
    if (!beats.length) {
      toast(
        warnings[0] || "AI 没有返回节拍建议；可先生成提示词，让 AI 在生成时自动设计节拍",
        warnings.length ? "err" : "info",
        8000
      );
      return;
    }
    const options = beatOptions();
    tbody.innerHTML = beats
      .map((beat, i) => beatRowHtml(normalizeBeat(beat), i, options))
      .join("");
    renumberBeatRows(details);
    state.beatsDraft[index] = beats.map((beat, i) => normalizeBeat({ ...beat, index: i }));
    toast(
      `AI 建议已填入节拍表（${beats.length} 条）${warnings.length ? `，${warnings.length} 条提示` : ""}，请点「保存节拍」`,
      warnings.length ? "info" : "ok",
      7000
    );
  }).catch((err) => toast(`AI 建议节拍失败：${err.message}`, "err", 7000));
}

/* ---------------- 分段渲染 ---------------- */

function segTimeHtml(seg) {
  const frames = seg.frames !== undefined && seg.frames !== null ? `${num(seg.frames)} 帧` : "— 帧";
  return `约 ${num(segSeconds(seg)).toFixed(1)}s<br>${frames}`;
}

function segRangeLabel(project, seg) {
  const start = segmentStart(project, seg.index);
  const end = start + segSeconds(seg);
  return `${fmtClock(start)} – ${fmtClock(end)} · ${segSeconds(seg).toFixed(1)}s`;
}

function takesHtml(project, seg) {
  const takes = Array.isArray(seg.takes) ? seg.takes : [];
  if (!takes.length) {
    return `<span class="av-takes-empty">还没有片段 · 点「单段重跑」或「开始生成」</span>`;
  }
  return takes
    .map((take, takeIndex) => {
      const active = take.take_id === seg.selected_take;
      const thumb = take.thumb ? mediaUrl(project.id, take.thumb, take.created) : "";
      const fallback = `<div class="av-take-fallback"><svg><use href="#i-film"/></svg></div>`;
      const overlayFlag = typeof take.overlay === "boolean" ? take.overlay : null;
      const overlayBadge = overlayFlag === null
        ? ""
        : `<span class="take-overlay ${overlayFlag ? "" : "none"}">${overlayFlag ? "已叠加" : "未叠加"}</span>`;
      return `<div class="av-take ${active ? "active" : ""}" role="button" tabindex="0"
        data-action="take-select" data-index="${seg.index}" data-take="${esc(take.take_id)}"
        title="点击锁定为合成对象${take.created ? ` · ${esc(fmtWhen(take.created))}` : ""}">
        ${thumb ? `<img src="${esc(thumb)}" alt="" loading="lazy">` : fallback}
        <button class="take-play" type="button" data-action="take-preview"
          data-index="${seg.index}" data-take="${esc(take.take_id)}" title="放大播放这个片段">
          <svg><use href="#i-play"/></svg>
        </button>
        <span class="take-no">${takeIndex + 1}</span>
        ${overlayBadge}
        ${active ? '<span class="take-picked">✓</span>' : ""}
        ${active ? "" : `<button class="take-del" type="button" data-action="take-del" data-index="${seg.index}" data-take="${esc(take.take_id)}" title="删除这个片段"><svg><use href="#i-close"/></svg></button>`}
        <span class="take-name">${active ? "已锁定" : `片段 ${takeIndex + 1}`}</span>
      </div>`;
    })
    .join("");
}

function overlayStateOf(seg) {
  const takes = Array.isArray(seg.takes) ? seg.takes : [];
  if (!takes.length) return null;
  const selected = takes.find((take) => take.take_id === seg.selected_take) || takes[takes.length - 1];
  return typeof selected?.overlay === "boolean" ? selected.overlay : null;
}

function segCardHtml(project, seg) {
  const status = seg.status || "idle";
  const [statusText, statusClass] = runStatusMeta(status);
  const mode = Number(project.mode) === 1 ? 1 : Number(project.mode) === 3 ? 3 : 2;
  const isMode1 = mode === 1;
  const overlayMode = state.designGraphicsMode === "overlay";
  const overlayState = overlayMode ? overlayStateOf(seg) : null;
  const overlayPill = overlayMode
    ? `<span class="pill ${overlayState === true ? "vocal" : ""}">${overlayState === null ? "后期叠加" : overlayState ? "已后期叠加" : "未叠加"}</span>`
    : "";
  const promptTitle = overlayMode
    ? "叠加模式：提示词只写人物表演/构图/光，不画文字"
    : "用设计层与节拍表生成这一段提示词";
  const anchor = mode === 1 && String((project.generation || {}).stitch || "hard") === "anchor";
  const promptActionLabel = anchor
    ? (seg.prompt ? "重填模板" : "填入模板")
    : (seg.prompt ? "重新润色" : "润色（可选）");
  const takes = Array.isArray(seg.takes) ? seg.takes : [];
  const activeTab = state.segTabs[seg.index] || "clip";
  const tabBtn = (key, label, count) => `<button type="button" role="tab"
    class="av-seg-tab ${activeTab === key ? "active" : ""}"
    data-action="seg-tab" data-index="${seg.index}" data-tab="${key}">${label}${count === null || count === undefined ? "" : `<i>${count}</i>`}</button>`;
  return `<article class="av-seg ${esc(status)}" data-index="${seg.index}">
    <div class="av-seg-index">
      <span class="seg-num">${pad(seg.index + 1)}</span>
      <span class="av-seg-time">${isMode1 ? segmentTimeCell(seg) : segTimeHtml(seg)}</span>
    </div>
    <div class="av-seg-main">
      <div class="av-seg-top">
        <h3>第 ${seg.index + 1} 段</h3>
        <span class="pill seg-status ${statusClass}">${statusText}</span>
        ${isMode1 ? `<span class="pill av-seg-time-range">${esc(segRangeLabel(project, seg))}</span>` : ""}
        <span class="pill">${num(seg.chars)} 字</span>
        ${overlayPill}
        ${seg.prompt ? '<span class="pill vocal">已备提示词</span>' : ""}
      </div>
      ${seg.error ? `<div class="av-seg-error">${esc(seg.error)}</div>` : ""}
      ${warningsHtml(seg)}
      <div class="av-seg-tabs" role="tablist">
        ${tabBtn("clip", "预览 / 片段", takes.length)}
        ${tabBtn("prompt", "提示词", null)}
        ${tabBtn("text", "台词", null)}
        ${tabBtn("ref", "参考图", null)}
      </div>
      <div class="av-seg-pane" data-pane="clip" ${activeTab === "clip" ? "" : "hidden"}>
        <div class="av-takes">
          <span class="av-takes-label">取片</span>
          ${takesHtml(project, seg)}
        </div>
        <p class="av-takes-hint">点卡片锁定为合成对象 · 点 ▶ 放大播放</p>
      </div>
      <div class="av-seg-pane" data-pane="prompt" ${activeTab === "prompt" ? "" : "hidden"}>
        ${segmentNoteHtml(seg)}
        ${promptBlockHtml(project, seg)}
        ${beatsBlockHtml(project, seg)}
      </div>
      <div class="av-seg-pane" data-pane="text" ${activeTab === "text" ? "" : "hidden"}>
        <textarea class="av-seg-text" rows="2" spellcheck="false" data-action="seg-text"
          data-index="${seg.index}" data-fkey="seg-text-${seg.index}"
          placeholder="${isMode1 ? "这一段的台词（可选：来自语音识别或按比例分配，不填不影响生成）…" : "这一段的台词…"}">${esc(seg.text || "")}</textarea>
        ${segmentVoicesHtml(project, seg)}
      </div>
      <div class="av-seg-pane" data-pane="ref" ${activeTab === "ref" ? "" : "hidden"}>
        ${segmentRefsHtml(project, seg)}
      </div>
    </div>
    <div class="av-seg-actions">
      <button class="btn ghost sm${state.generatingPrompt[seg.index] ? " generating" : ""}" type="button"
        data-action="seg-prompt" data-index="${seg.index}" title="${esc(promptTitle)}"
        ${state.generatingPrompt[seg.index] ? "disabled" : ""}>
        <svg><use href="#i-spark"/></svg>${state.generatingPrompt[seg.index]
          ? `生成中（${promptElapsed(seg.index)}s）` : promptActionLabel}
      </button>
      <button class="btn ghost sm" type="button" data-action="seg-regen" data-index="${seg.index}" title="只重跑这一段，不影响其它段与已锁定片段">
        <svg><use href="#i-refresh"/></svg>单段重跑
      </button>
      ${isMode1 && seg.index < (project.segments || []).length - 1 ? `<button class="btn ghost sm" type="button" data-action="seg-merge" data-index="${seg.index}">
        <svg><use href="#i-merge"/></svg>合并下段
      </button>` : ""}
      ${isMode1 ? `<button class="btn ghost sm" type="button" data-action="seg-audition" data-index="${seg.index}">
        <svg><use href="#i-play"/></svg>${state.auditionIndex === seg.index ? "停止试听" : "试听"}
      </button>` : ""}
    </div>
  </article>`;
}

function segmentTimeCell(seg) {
  return `${segSeconds(seg).toFixed(1)}s<br>${num(seg.frames)} 帧`;
}

function activateSegmentTab(index, tab) {
  const card = document.querySelector(`.av-seg[data-index="${index}"]`);
  if (!card) return;
  state.segTabs[index] = tab;
  card.querySelectorAll(".av-seg-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });
  card.querySelectorAll(".av-seg-pane").forEach((pane) => {
    pane.hidden = pane.dataset.pane !== tab;
  });
}

function openSegmentDetails(index, selector) {
  if (selector.includes("seg-prompt") || selector.includes("av-beats")) {
    activateSegmentTab(index, "prompt");
  }
  const card = document.querySelector(`.av-seg[data-index="${index}"]`);
  const details = card?.querySelector(selector);
  if (details) details.open = true;
  return details;
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
  const openPrompt = $$("#seg-list .av-seg-prompt[open]").map((details) =>
    Number(details.closest(".av-seg")?.dataset.index)
  );
  const openBeats = $$("#seg-list .av-beats[open]").map((details) => Number(details.dataset.beatsIndex));
  captureBeatsDrafts();
  empty.hidden = true;
  list.innerHTML = segments.map((seg) => segCardHtml(project, seg)).join("");
  openPrompt.filter((index) => Number.isFinite(index)).forEach((index) => {
    const details = list.querySelector(`.av-seg[data-index="${index}"] .av-seg-prompt`);
    if (details) details.open = true;
  });
  openBeats.filter((index) => Number.isFinite(index)).forEach((index) => {
    const details = list.querySelector(`.av-beats[data-beats-index="${index}"]`);
    if (details) details.open = true;
  });
  updateOverlayInline();
}

function updateOverlayInline() {
  const overlay = state.designGraphicsMode === "overlay";
  $$(".av-overlay-inline").forEach((el) => {
    el.hidden = !overlay;
  });
}

function showEmpty(kind) {
  const empty = $("#stage-empty");
  if (!empty) return;
  empty.hidden = false;
  if (kind === "no-segments") {
    const isMode1 = state.project?.mode === 1;
    if (isMode1) {
      const hasAudio = !!currentAudio();
      empty.innerHTML = `
        <div class="empty-orb"><svg><use href="#i-wave"/></svg></div>
        <h3>音频还没有分段</h3>
        <p>模式 1 以音频波形为时间轴：上传音频并分析后，会按目标段长自动切分，之后可以拖动切点、按播放头切分或合并下段。</p>
        <ol>
          <li><b>01</b>${hasAudio ? "音频已就绪，点「上传并分析」。" : "在左栏「音频驱动」表单拖入音频 / 视频。"}</li>
          <li><b>02</b>按需要开启「语音识别辅助切分」，选模型与语言。</li>
          <li><b>03</b>分析完成后在波形时间线上微调切点，再逐段生成。</li>
        </ol>
        <div class="inline" style="justify-content:center">
          <button class="btn primary" type="button" data-action="analyze-audio"><svg><use href="#i-wave"/></svg>上传并分析</button>
        </div>`;
      return;
    }
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
    <p>模式 1 用现成音频对口型，按波形分段；模式 2 用参考音频定音色，模式 3 用文字描述音色；台词会按标点与语速自动分段。</p>
    <ol>
      <li><b>01</b>在左栏选模式，填作品名称与台词。</li>
      <li><b>02</b>模式 1 上传音频；模式 2 上传 2–15 秒参考音频；模式 3 填写音色描述。</li>
      <li><b>03</b>点「新建作品」，模式 1 再点「上传并分析」，模式 2/3 点「按台词分段」。</li>
    </ol>
    <div class="inline" style="justify-content:center">
      <button class="btn primary" type="button" data-action="focus-create"><svg><use href="#i-plus"/></svg>去填写作品设定</button>
    </div>`;
}

function renderProjectContext() {
  const p = state.project;
  const segments = p?.segments || [];
  $("#stage-name").textContent = p ? p.name || "未命名作品" : "从一句台词开始";
  if (p) {
    const mode = clamp(Number(p.mode) || 2, 1, 3);
    const design = p.design || {};
    const density = DENSITY_LABEL[design.effect_density] || "中";
    const gmode = GRAPHICS_MODE_LABEL[state.designGraphicsMode] || "生成式";
    const audioPart = num(p.audio?.duration) > 0 ? fmtClock(num(p.audio.duration)) : "待上传";
    const extra = mode === 1
      ? ` · 音频 ${audioPart}`
      : ` · 目标 ${num(p.target_seconds, 8).toFixed(1)}s / 最长 ${num(p.max_seconds, 12).toFixed(1)}s`;
    $("#stage-sub").textContent = `模式 ${mode} · ${MODE_LABEL[mode]} · ${segments.length} 段${extra} · 设计：密度${density} / ${gmode}${p.final ? " · 已有成片" : ""}`;
  } else {
    $("#stage-sub").textContent = "选模式、写台词、传参考，剩下交给 H3 逐段原生生成";
  }
  $("#project-state").textContent = p ? `已保存 · ${segments.length} 段` : "未创建";
  const isMode1 = (p?.mode ?? state.mode) === 1;
  if (p) {
    $("#form-hint").textContent = isMode1
      ? (p.audio ? "音频已就绪：点「上传并分析」重建分段，或直接在时间轴微调切点。" : "拖入音频后点「上传并分析」，按波形自动分段。")
      : segments.length
        ? "改动台词后点「按台词分段」重建分段；只改参数点「保存设定」。"
        : "点「按台词分段」，按标点与语速切段。";
  } else {
    $("#form-hint").textContent = isMode1
      ? "选择音频后点「上传并分析」，会先创建作品再上传分析。"
      : "填写台词后新建作品，再点「按台词分段」。";
  }
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

function transientRunState(text) {
  const pill = $("#run-state");
  if (!pill) return () => {};
  const previous = { text: pill.textContent, cls: pill.className };
  const stage = $("#run-stage");
  const previousStage = stage ? stage.textContent : null;
  pill.textContent = text;
  pill.className = "pill status-running";
  if (stage) stage.textContent = text;
  return () => {
    if (state.run) return; // 已有真实生成任务：交给 renderRun 刷新
    pill.textContent = previous.text;
    pill.className = previous.cls;
    if (stage && previousStage !== null) stage.textContent = previousStage;
  };
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
  if (active) setRightTab("results", false);
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
    frame.innerHTML = `<div class="final-empty av-final-empty"><svg><use href="#i-film"/></svg><span>合成完成后在此预览</span><em>逐段生成并取片后，点「合成全片」</em></div>`;
    info.classList.add("hidden");
    stale.classList.add("hidden");
    duration.textContent = "—";
    const assembleLabel = $("#btn-assemble-label");
    if (assembleLabel) assembleLabel.textContent = "合成全片";
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
  const assembleLabel = $("#btn-assemble-label");
  if (assembleLabel) assembleLabel.textContent = "重新合成全片";
  stale.classList.toggle("hidden", !final.stale);
  if (final.stale) {
    $("#final-stale-text").textContent = "取片或分段已有变更，成片需要重新合成";
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

function currentSegment(index) {
  return (state.project?.segments || []).find((item) => Number(item.index) === Number(index)) || null;
}

function applyPromptResult(index, data) {
  if (data.project) {
    state.project = data.project;
  } else if (state.project) {
    const seg = currentSegment(index);
    if (seg) {
      if (data.text !== undefined) seg.prompt = data.text;
      if (data.source !== undefined) seg.prompt_source = data.source;
      if (data.prompt_meta !== undefined) seg.prompt_meta = data.prompt_meta;
      if (data.warnings !== undefined) seg.prompt_warnings = data.warnings;
    }
  }
  delete state.lintOverride[index];
  renderSegments();
  renderProjectContext();
  updateSummary(state.project);
  syncButtons();
}

function promptToast(sourceKey, lint) {
  const source = PROMPT_SOURCE_LABEL[sourceKey] || sourceKey || "内置模板";
  const ls = lint?.errors?.length
    ? `，lint ${lint.errors.length} 错误`
    : lint?.warnings?.length
      ? `，lint ${lint.warnings.length} 警告`
      : "，lint 通过";
  return `提示词已生成（${source}${ls}）`;
}

async function generateSegmentPrompt(index) {
  const p = state.project;
  if (!p) return;
  if (state.generatingPrompt[index]) return;
  state.generatingPrompt[index] = Date.now();
  renderSegments();
  openSegmentDetails(index, ".av-seg-prompt");
  if (state.promptTicker) clearInterval(state.promptTicker);
  state.promptTicker = setInterval(() => {
    if (!Object.keys(state.generatingPrompt).length) {
      clearInterval(state.promptTicker);
      state.promptTicker = null;
      return;
    }
    renderSegments();
  }, 1000);
  const restore = transientRunState(`生成提示词（第 ${index + 1} 段）…`);
  try {
  await withBusy(null, async () => {
    const data = await api.segmentPrompt(p.id, index, { repair: true });
    applyPromptResult(index, data);
    const details = openSegmentDetails(index, ".av-seg-prompt");
    toast(promptToast(data.source, data.prompt_meta?.lint), data.prompt_meta?.lint?.errors?.length ? "warn" : "ok", 7000);
    if (details) details.querySelector(".av-prompt-area")?.focus();
    const warnings = data.warnings || [];
    if (warnings.length) {
      toast(
        `提示：${warnings.slice(0, 2).map((item) => (typeof item === "string" ? item : JSON.stringify(item))).join("；")}`,
        "info",
        6000
      );
    }
    if (Array.isArray(data.project?.segments?.[index]?.beats) && !data.project.segments[index].beats.length) {
      toast("节拍表为空：生成提示词时 AI 会自动设计节拍（可展开节拍表查看）", "info", 6000);
    }
  });
  } catch (err) {
    toast(`提示词生成失败：${err.message}`, "err", 7000);
  } finally {
    delete state.generatingPrompt[index];
    if (!Object.keys(state.generatingPrompt).length && state.promptTicker) {
      clearInterval(state.promptTicker);
      state.promptTicker = null;
    }
    restore();
    renderSegments();
    openSegmentDetails(index, ".av-seg-prompt");
  }
}

async function saveSegmentPrompt(index, value) {
  const p = state.project;
  if (!p) return;
  const seg = currentSegment(index);
  if (seg && (seg.prompt || "") === value) {
    toast("提示词没有变化", "info", 2400);
    return;
  }
  await withBusy(null, async () => {
    const data = await api.patchSegment(p.id, index, { prompt: value });
    state.project = data.project;
    // 手动保存后补一次 lint，避免 meta 里显示旧提示词的 lint 结论
    try {
      const lintData = await api.lintPrompt(p.id, index, value);
      const freshMeta = currentSegment(index)?.prompt_meta;
      state.lintOverride[index] = { lint: lintData.lint, meta: freshMeta };
    } catch {
      delete state.lintOverride[index];
    }
    renderSegments();
    updateSummary(data.project);
    syncButtons();
    openSegmentDetails(index, ".av-seg-prompt");
    toast("提示词已保存", "ok");
  }).catch((err) => toast(`提示词保存失败：${err.message}`, "err"));
}

async function repairSegmentPrompt(index) {
  const p = state.project;
  if (!p) return;
  const card = document.querySelector(`.av-seg[data-index="${index}"]`);
  const area = card?.querySelector(".av-prompt-area");
  const seg = currentSegment(index);
  try {
    await withBusy(null, async () => {
      if (area && seg && area.value !== (seg.prompt || "")) {
        const patched = await api.patchSegment(p.id, index, { prompt: area.value });
        state.project = patched.project;
      }
      const data = await api.repairPrompt(p.id, index);
      applyPromptResult(index, data);
      openSegmentDetails(index, ".av-seg-prompt");
      toast(promptToast(data.source, data.prompt_meta?.lint), data.prompt_meta?.lint?.errors?.length ? "warn" : "ok", 7000);
    });
  } catch (err) {
    toast(`按 lint 修复失败：${err.message}`, "err", 7000);
  }
}

async function lintSegmentPrompt(index) {
  const p = state.project;
  if (!p) return;
  const card = document.querySelector(`.av-seg[data-index="${index}"]`);
  const area = card?.querySelector(".av-prompt-area");
  const text = area ? area.value : currentSegment(index)?.prompt || "";
  try {
    const data = await api.lintPrompt(p.id, index, text);
    state.lintOverride[index] = { lint: data.lint, meta: data.prompt_meta };
    const seg = currentSegment(index) || { index };
    const meta = card?.querySelector(`[data-meta-index="${index}"]`);
    if (meta) meta.innerHTML = promptMetaInner({ ...seg, prompt_meta: data.prompt_meta || seg.prompt_meta });
    const lint = data.lint || {};
    const errors = Array.isArray(lint.errors) ? lint.errors.length : 0;
    const warnings = Array.isArray(lint.warnings) ? lint.warnings.length : 0;
    toast(
      errors ? `Lint：${errors} 个错误、${warnings} 个警告` : warnings ? `Lint 通过，${warnings} 个警告` : "Lint 通过",
      errors ? "err" : warnings ? "warn" : "ok",
      6000
    );
  } catch (err) {
    toast(`Lint 检查失败：${err.message}`, "err", 6000);
  }
}

async function saveSegmentText(index, value) {
  const p = state.project;
  if (!p) return;
  const seg = currentSegment(index);
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
  const restore = transientRunState(`批量生成提示词（${p.segments.length} 段）…`);
  try {
  await withBusy($("#btn-prompts-all"), async () => {
    const data = await api.promptsAll(p.id);
    state.project = data.project;
    state.lintOverride = {};
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
  });
  } catch (err) {
    toast(`提示词生成失败：${err.message}`, "err", 6000);
  } finally {
    restore();
  }
}

async function generateRun() {
  const p = state.project;
  if (!p) return toast("请先新建作品", "err");
  if (Number(p.mode) === 1 && !p.audio) return toast("模式 1 请先上传并分析音频", "err");
  if (!(p.segments || []).length) return toast("请先按台词分段", "err");
  await withBusy($("#btn-generate"), async () => {
    const data = await api.generate(p.id, { force: false, generation: readGeneration() });
    state.run = data.run || state.run;
    if (state.project) state.project.run = state.run;
    renderRun(state.run);
    state.pollCount = 0;
    startPolling();
    toast(
      state.designGraphicsMode === "overlay"
        ? "已开始逐段生成（叠加模式：文字/logo 生成后由后期确定性叠加）"
        : "已开始逐段生成，耗时长，请保持页面打开",
      "info",
      6000
    );
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
    toast(`第 ${index + 1} 段开始单段重跑`, "info");
  }).catch((err) => toast(`单段重跑失败：${err.message}`, "err", 6000));
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
  const seg = currentSegment(index);
  if (!seg || !takeId) return;
  if (seg.selected_take === takeId) {
    toast("这已经是当前选用的片段", "info", 2400);
    return;
  }
  try {
    const data = await api.selectTake(p.id, index, takeId);
    applyProject(data.project, { fill: false });
    toast("已锁定这条片段为合成对象，点「重新合成全片」更新成片", "ok");
  } catch (err) {
    toast(`锁定片段失败：${err.message}`, "err");
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
  showBusy("正在用 ffmpeg 合成全片…");
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
    syncButtons();
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
    state.pollFailures = 0;
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
    // 网络抖动/后端重启一次就永久停轮询，会让任务还在跑但界面再也不更新；
    // 连续失败 3 次才停。
    state.pollFailures = (state.pollFailures || 0) + 1;
    if (state.pollFailures >= 3) {
      stopPolling();
      toast(`获取运行状态失败：${err.message}`, "err", 6000);
    }
  } finally {
    state.polling = false;
  }
}

function isEditingStage() {
  const el = document.activeElement;
  if (!el || !el.closest) return false;
  return !!(el.closest("#seg-list") && (el.tagName === "TEXTAREA" || el.tagName === "INPUT" || el.tagName === "SELECT"));
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
    renderAudio();
    renderRefs();
    renderTimeline();
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
  if (fill || state.designLoadedFor !== project.id) {
    fillDesign(designOf(project));
    state.designLoadedFor = project.id;
  }
  renderProjectContext();
  renderVoice();
  renderAudio();
  renderRefs();
  renderTimeline();
  if (segments) renderSegments();
  renderRun(state.run);
  renderFinal();
  updateSummary(project);
  updateDesignChrome();
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
  const mode = clamp(Number(p?.mode ?? state.mode) || 2, 1, 3);
  const isMode1 = mode === 1;
  const audioReady = !!(state.pendingAudio || p?.audio);

  const setDisabled = (selector, disabled) => {
    const el = $(selector);
    if (el) el.disabled = !!disabled;
  };

  setDisabled("#btn-plan", !hasProject || isMode1);
  setDisabled("#btn-plan2", !hasProject || isMode1);
  $("#btn-plan").classList.toggle("hidden", isMode1);
  $("#btn-plan2").classList.toggle("hidden", isMode1);
  setDisabled("#btn-prompts-all", !hasSegments);
  setDisabled("#btn-generate", !hasSegments || active);
  setDisabled("#btn-assemble", !hasSegments);
  setDisabled("#btn-assemble2", !hasSegments);
  setDisabled("#btn-cancel", !active);
  setDisabled("#btn-ref-upload", !hasProject);
  setDisabled("#ref-role", !hasProject);

  setDisabled("#btn-analyze", !audioReady);
  setDisabled("#btn-analyze2", !hasProject || !isMode1);
  $("#btn-analyze2").classList.toggle("hidden", !(isMode1 && hasProject && p?.audio));
  const waveReady = !!p?.analysis?.waveform?.original?.length;
  setDisabled("#btn-split", !(isMode1 && hasSegments && audioReady && waveReady));
  setDisabled("#btn-playhead-play", !audioReady || !isMode1);
  setDisabled("#btn-design-save", !hasProject);
  setDisabled("#btn-design-suggest", !hasProject);
  setDisabled("#btn-voicecard-suggest", !hasProject);

  const cancel = $("#btn-cancel");
  if (cancel) cancel.classList.toggle("hidden", !active);

  $("#btn-create-label").textContent = hasProject ? "保存设定" : "新建作品";
  // 已有作品时始终给一个明确的新建入口，避免主按钮变成「保存设定」后找不到新建
  $("#btn-new")?.classList.toggle("hidden", !hasProject);

  const download = $("#btn-download");
  const downloadTop = $("#btn-download-top");
  if (download) download.classList.toggle("hidden", !hasFinal);
  if (downloadTop) {
    downloadTop.classList.remove("hidden");
    downloadTop.classList.toggle("is-disabled", !hasFinal);
    downloadTop.setAttribute("aria-disabled", String(!hasFinal));
    downloadTop.title = hasFinal ? "下载成片 MP4" : "合成全片后可下载";
  }

  const generate = $("#btn-generate");
  if (generate) {
    if (isMode1 && !p?.audio) generate.title = "模式 1 请先上传并分析音频";
    else if (!hasSegments) generate.title = isMode1 ? "先上传并分析音频" : "先按台词分段";
    else generate.title = state.designGraphicsMode === "overlay"
      ? "按分段逐段生成（叠加模式：文字/logo 由后期确定性叠加）"
      : "按分段逐段生成视频（耗时长）";
  }
}

/* ---------------- 事件绑定 ---------------- */

function bindDragEvents(drop, input, handler) {
  if (!drop) return;
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
    if (file) await handler(file);
  });
  if (input) {
    input.addEventListener("change", async (event) => {
      const file = event.target.files?.[0];
      event.target.value = "";
      if (file) await handler(file);
    });
  }
}

function bindWaveEvents() {
  // 指针交互（定位播放头 / 拖切点 / 双击切分 / 右键合并）由 Waveform 组件接管
  // （组件移植自 web/js/waveform.js，画布层统一绘制标尺、波形、段底色与播放头）
}

function bindAudioElement() {
  const el = $("#av-audio");
  if (!el) return;
  el.addEventListener("timeupdate", () => {
    if (!state.draggingPlayhead) setPlayhead(el.currentTime, { seek: false });
    if (state.auditionIndex !== null && el.currentTime >= state.auditionEnd - 0.02) {
      stopSegmentAudition();
    }
  });
  el.addEventListener("play", updatePlayLabel);
  el.addEventListener("pause", updatePlayLabel);
  el.addEventListener("ended", () => {
    updatePlayLabel();
    setPlayhead(0);
  });
  el.addEventListener("error", () => {
    state.audioFailed = true;
  });
}

function bindDesignEvents() {
  const panel = $("#panel-design");
  if (!panel) return;
  $("#design-density").addEventListener("click", (event) => {
    const btn = event.target.closest("[data-density]");
    if (!btn) return;
    state.designDensity = btn.dataset.density;
    markDesignDirty();
    updateDesignChrome();
  });
  $("#design-style-systems").addEventListener("click", (event) => {
    const btn = event.target.closest("[data-style-id]");
    if (!btn) return;
    applyStyleSystem(btn.dataset.styleId);
  });
  ["#design-accent", "#design-secondary"].forEach((selector) => {
    const el = $(selector);
    if (el) el.addEventListener("input", () => {
      markDesignDirty();
      updateDesignChrome();
    });
  });
  $("#design-graphics-mode").addEventListener("click", (event) => {
    const btn = event.target.closest("[data-gmode]");
    if (!btn) return;
    state.designGraphicsMode = btn.dataset.gmode === "overlay" ? "overlay" : "generated";
    markDesignDirty();
    updateDesignChrome();
    renderProjectContext();
  });
  $("#design-typography-preset").addEventListener("change", (event) => {
    const value = event.target.value;
    if (value && value !== "__custom__") $("#design-typography").value = value;
    markDesignDirty();
    updateDesignChrome();
  });
  $("#design-material-preset").addEventListener("change", (event) => {
    const value = event.target.value;
    if (value && value !== "__custom__") $("#design-material").value = value;
    markDesignDirty();
    updateDesignChrome();
  });
  $("#design-effects").addEventListener("change", () => {
    markDesignDirty();
    updateDesignChrome();
  });
  panel.addEventListener("input", (event) => {
    if (event.target.closest("#design-effects")) return;
    markDesignDirty();
  });
  panel.addEventListener("change", (event) => {
    if (event.target.closest("#design-effects")) return;
    markDesignDirty();
  });
  $("#btn-design-save").addEventListener("click", saveDesign);
  $("#btn-design-suggest").addEventListener("click", suggestDesignEffects);
  $("#btn-voicecard-suggest").addEventListener("click", suggestVoiceCard);
}

function bindEvents() {
  $("#btn-refresh").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    await withBusy(button, async () => {
      await Promise.allSettled([loadHealth(), loadOptions(), loadProjects()]);
      await loadDesignCatalog();
      if (state.project) fillDesign(designOf(state.project));
      else fillDesign(designBase(state.mode));
      renderProjectContext();
      toast("系统状态已刷新", "ok", 2600);
    });
  });

  $$("[data-mode-card]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const next = Number(btn.dataset.modeCard);
      if (state.mode === next) return;
      switchMode(next);
    });
  });

  $("#f-script").addEventListener("input", updateScriptCount);
  $(".av-stage").addEventListener("input", (event) => {
    const area = event.target.closest("[data-note-index]");
    if (!area) return;
    const card = area.closest(".av-seg");
    const index = Number(card?.dataset.index);
    const caret = area.selectionStart;
    if (area.value.slice(caret - 1, caret) === "@") {
      openMentionPop(area, (token) => insertAtCaret(area, token, { trimAt: true }), { trimAt: true });
    }
    if (Number.isFinite(index)) persistSegmentNote(index, area.value);
  });
  document.addEventListener("change", (event) => {
    const box = event.target.closest("[data-ref-check]");
    if (!box) return;
    const card = box.closest(".av-seg");
    const index = Number(card?.dataset.index);
    const p = state.project;
    if (!p || !Number.isFinite(index)) return;
    const boxes = Array.from(card.querySelectorAll("[data-ref-check]"));
    const chosen = boxes.filter((item) => item.checked)
      .map((item) => Number(item.dataset.ref)).sort((a, b) => a - b);
    const allCount = (p.refs || []).length;
    persistSegmentRefs(index, chosen.length === allCount ? null : chosen);
  }, true);
  document.addEventListener("keydown", (event) => {
    if (mentionKeydown(event)) event.stopPropagation();
  }, true);
  document.addEventListener("click", (event) => {
    const pop = $("#mention-pop");
    if (!pop || pop.classList.contains("hidden")) return;
    const item = event.target.closest("[data-mention-index]");
    if (item) {
      state.mention.active = Number(item.dataset.mentionIndex);
      renderMentionList();
      renderMentionPreview();
      return;
    }
    if (event.target.closest("#btn-mention-insert")) {
      const chosen = state.mention.items[state.mention.active];
      if (chosen && state.mention.onPick) state.mention.onPick(chosen.token);
      closeMentionPop();
      return;
    }
    if (!event.target.closest("#mention-pop")) closeMentionPop();
  }, true);
  $("#f-cps").addEventListener("input", () => {
    const value = num($("#f-cps").value, 5);
    if (value < 1 || value > 12) $("#f-cps").value = String(clamp(value || 5, 1, 12));
  });
  $("#f-target").addEventListener("input", () => clampRanges("target"));
  $("#f-max").addEventListener("input", () => clampRanges("max"));
  ["#f-target", "#f-max"].forEach((selector) => {
    const el = $(selector);
    if (el) el.addEventListener("input", updateRangeTone);
  });

  $("#btn-create").addEventListener("click", () => (state.project ? saveSettings() : createProject()));
  $("#btn-new").addEventListener("click", startNewProject);
  // 数字人包（.h3avatar.zip）导入 / 导出：后端与文档已定义契约，之前页面没有入口
  $("#btn-import-pack")?.addEventListener("click", () => $("#pack-input")?.click());
  $("#pack-input")?.addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    await withBusy($("#btn-import-pack"), async () => {
      try {
        const data = await api.importProject(file);
        await loadProjects();
        if (data.project?.id) await openProject(data.project.id);
        toast(`已导入「${data.project?.name || "数字人包"}」`, "ok");
      } catch (error) {
        toast(`导入失败：${error.message}`, "err", 6000);
      }
    });
  });
  $("#btn-export-pack")?.addEventListener("click", () => {
    if (!state.project) {
      toast("请先打开一个作品再导出。", "warn");
      return;
    }
    window.location.href = api.exportUrl(state.project.id);
  });
  $("#btn-plan").addEventListener("click", () => planSegments());
  $("#btn-plan2").addEventListener("click", () => planSegments());
  $("#btn-prompts-all").addEventListener("click", generateAllPrompts);
  $("#btn-generate").addEventListener("click", generateRun);
  $("#btn-cancel").addEventListener("click", cancelRun);
  $("#btn-assemble").addEventListener("click", assembleFilm);
  $("#btn-assemble2").addEventListener("click", assembleFilm);
  $("#btn-save-prompts").addEventListener("click", savePrompts);
  $("#btn-analyze").addEventListener("click", analyzeAudioFlow);
  $("#btn-analyze2").addEventListener("click", analyzeAudioFlow);
  $("#btn-split").addEventListener("click", splitAtPlayhead);
  $("#btn-playhead-play").addEventListener("click", togglePlay);

  $("#asr-enabled").addEventListener("change", () => {
    $("#asr-opts").style.opacity = $("#asr-enabled").checked ? "" : ".45";
  });

  bindDragEvents($("#audio-drop"), $("#audio-input"), handleAudioFile);
  $("#btn-audio-replace").addEventListener("click", () => $("#audio-input").click());
  $("#btn-audio-del").addEventListener("click", deleteAudio);

  bindDragEvents($("#voice-drop"), $("#voice-input"), handleVoiceFile);
  document.addEventListener("click", (event) => {
    const row = event.target.closest(".av-voice-item");
    if (!row) return;
    const voiceId = row.dataset.voiceId || "";
    const project = state.project;
    const voice = (project?.voices || []).find((item) => String(item.id) === voiceId)
      || (project?.voice && String(project.voice.id) === voiceId ? project.voice : null);
    if (event.target.closest("[data-voice-audition]")) {
      if (voice) auditionVoice(voice);
      return;
    }
    if (event.target.closest("[data-voice-delete]")) {
      removeVoice(voiceId);
      return;
    }
  }, true);
  document.addEventListener("change", (event) => {
    const input = event.target.closest("[data-voice-name]");
    if (!input) return;
    const row = input.closest(".av-voice-item");
    const voiceId = row?.dataset.voiceId || "";
    const name = input.value.trim();
    if (voiceId && name) renameVoice(voiceId, name);
  }, true);
  $("#btn-voice-replace").addEventListener("click", () => $("#voice-input").click());
  $("#btn-voice-del").addEventListener("click", deleteVoice);
  $("#btn-voice-audition").addEventListener("click", toggleVoiceAudition);
  $("#voice-audition").addEventListener("ended", stopVoiceAudition);
  $("#f-voice-start").addEventListener("input", () => {
    const voice = state.project?.voice;
    if (!voice) return;
    const total = num(voice.source_duration);
    const length = num(voice.sample_length) || 12;
    const start = num($("#f-voice-start").value, 0);
    $("#voice-sample-range").textContent =
      `${start.toFixed(1)}–${Math.min(total, start + length).toFixed(1)}s（原音 ${total.toFixed(1)}s）`;
  });
  $("#f-voice-start").addEventListener("change", saveVoiceSample);

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

  bindWaveEvents();
  bindAudioElement();
  bindDesignEvents();

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
    if (action === "seg-tab") {
      activateSegmentTab(index, el.dataset.tab || "clip");
      return;
    }
    if (action === "plan") {
      planSegments();
      return;
    }
    if (action === "analyze-audio") {
      analyzeAudioFlow();
      return;
    }
    if (action === "seg-prompt") generateSegmentPrompt(index);
    else if (action === "seg-audition") toggleSegmentAudition(index);
    else if (action === "ref-preview") openImagePreview(el.dataset.src, el.dataset.title);
    else if (action === "seg-voice-audition") {
      const p = state.project;
      const voices = (p?.voices || []).length ? p.voices : (p?.voice ? [p.voice] : []);
      const voiceId = el.dataset.voiceId || "";
      const voice = voices.find((item) => String(item.id) === voiceId)
        || voices[Number(el.dataset.voiceIndex)] || voices[0];
      if (voice) auditionVoice(voice);
    }
    else if (action === "take-preview") {
      const seg = currentSegment(index);
      if (seg) openTakePreview(state.project, seg, el.dataset.take);
    }
    else if (action === "note-mention") {
      const card = el.closest(".av-seg");
      const area = card?.querySelector(".av-note-area");
      const target = area || $("#f-material-note");
      openMentionPop(target, (token) => insertAtCaret(target, token, { trimAt: state.mention.trimAt }));
    }
    else if (action === "seg-regen") regenerateSegment(index);
    else if (action === "seg-merge") mergeDown(index);
    else if (action === "save-prompt") {
      const card = el.closest(".av-seg");
      const area = card?.querySelector(".av-prompt-area");
      if (area) saveSegmentPrompt(index, area.value);
    } else if (action === "lint-prompt") lintSegmentPrompt(index);
    else if (action === "repair-prompt") repairSegmentPrompt(index);
    else if (action === "beats-suggest" || action === "beats-save" || action === "beat-add") {
      const details = el.closest("[data-beats-index]");
      const beatIndex = details ? Number(details.dataset.beatsIndex) : index;
      if (action === "beats-suggest") suggestBeats(beatIndex);
      else if (action === "beats-save") saveBeats(beatIndex);
      else addBeatRow(beatIndex);
    } else if (action === "beat-del") deleteBeatRow(el);
    else if (action === "take-select") {
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
    const actionEl = event.target.closest?.("[data-action]");
    if (!actionEl) return;
    if (actionEl.dataset.action === "take-select") {
      event.preventDefault();
      selectTake(Number(actionEl.dataset.index), actionEl.dataset.take);
      return;
    }
    if (actionEl.dataset.action === "take-preview") {
      event.preventDefault();
      const seg = currentSegment(Number(actionEl.dataset.index));
      if (seg) openTakePreview(state.project, seg, actionEl.dataset.take);
    }
  });

  const redraw = debounce(() => {
    if (!$("#timeline").classList.contains("hidden")) {
      drawWave();
      positionPlayhead();
    }
  }, 160);
  window.addEventListener("resize", redraw);
  window.addEventListener("beforeunload", () => {
    stopPolling();
    stopAudioPlayback();
  });
}

/* ---------------- 启动 ---------------- */

async function boot() {
  buildVoiceCardFields();
  bindEvents();
  initAnchorControls();
  initRightTabs();
  setMode(2, { quiet: true });
  renderSegments();
  renderRun(null);
  renderFinal();
  renderRefs();
  renderProjectList();
  renderTimeline();
  syncButtons();
  updateScriptCount();
  updateRangeLabels();

  const lastId = localStorage.getItem(LAST_KEY);
  await Promise.allSettled([loadHealth(), loadOptions(), loadPrompts(), loadProjects(), loadCatalogAtBoot()]);
  if (!state.designLoadedFor) {
    buildVoiceCardFields();
    fillDesign(designBase(state.mode));
  }

  if (!state.project) fillGeneration(state.generationDefaults || {});
  // 启动恢复上次作品时，如果用户已经抢先新建了作品，就不要再覆盖它
  if (lastId && !state.project) await openProject(lastId);
  if (!state.project) {
    renderSegments();
    renderRun(null);
    renderFinal();
    renderRefs();
    renderTimeline();
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
