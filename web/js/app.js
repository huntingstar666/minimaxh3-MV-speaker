// 主应用逻辑
import { api } from "./api.js";
import { state, setProject } from "./store.js";
import { Waveform } from "./waveform.js";
import {
  $, $$, busy, clamp, confirmDialog, debounce, escapeHtml, formatDate, formatTime, formatDuration,
  openModal, closeModal, settleConfirm, toast,
} from "./ui.js";
import {
  collectGeneration, renderFinal, renderGeneration, renderHistory, renderLLMForm,
  renderProject, renderRun, renderSegments, renderSettingsForm, renderStatus,
  segmentRefIndices, setActiveSegment, setAudioWaveform, ROLE_LABELS,
} from "./render.js";

const PROJECT_STATES = ["completed", "failed", "cancelled", "partial"];
let waveform = null;
let lastRunStatus = "";
let idlePolls = 0;
let refSegmentIndex = null;
const cutHistory = [];

// ------------------------------- 初始化 -------------------------------
async function init() {
  waveform = new Waveform($("#wave-canvas"), $("#wave-wrap"));
  waveform.onSeek = (seconds) => {
    const audio = $("#audio-el");
    // 先记录这次点击/拖动开始前的播放状态，拖动结束要回到该状态
    state.scrubResumePaused = audio.paused;
    audio.currentTime = Math.min(seconds, audio.duration || seconds);
    audio.play().catch(() => {});
  };
  waveform.editable = true;
  waveform.onBoundary = (cuts) => {
    if (!state.project) return;
    if (["queued", "running"].includes(state.run?.status)) {
      toast("任务运行中切点已锁定；要调整请先停止任务。", "warn", 5000);
      renderSegments();
      return;
    }
    const duration = Number(state.project.segments?.at(-1)?.end || 0);
    let previous = 0;
    for (const cut of cuts) {
      if (cut - previous < MIN_SEGMENT_SECONDS) {
        toast(`切点 ${previous.toFixed(1)}s 与 ${cut.toFixed(1)}s 之间不足 ${MIN_SEGMENT_SECONDS} 秒`, "warn", 5000);
        return;
      }
      previous = cut;
    }
    if (duration - previous < MIN_SEGMENT_SECONDS) {
      toast(`最后一个切点距结尾不足 ${MIN_SEGMENT_SECONDS} 秒`, "warn", 5000);
      return;
    }
    applyCuts(api.setBoundaries(state.project.id, cuts), "切点已更新，分镜提示词已重置");
  };
  waveform.onSplit = (time) => {
    if (!state.project) return;
    if (["queued", "running"].includes(state.run?.status)) {
      toast("任务运行中不能切分；要调整请先停止任务。", "warn", 5000);
      return;
    }
    const blocker = splitBlocker(time);
    if (blocker) {
      toast(blocker, "warn", 5000);
      return;
    }
    applyCuts(api.splitSegment(state.project.id, time), `已在 ${time.toFixed(2)}s 处切分`);
  };
  waveform.onDeleteBoundary = (index) => deleteBoundary(index);
  // 拖动播放头试听：移动超过阈值后开始播放，松手回到拖动前的播放状态
  waveform.onScrubStart = () => {
    const audio = $("#audio-el");
    // 播放状态已在 onSeek（按下时）记录，这里只负责让拖动过程可听
    if (audio.paused) audio.play().catch(() => { /* 浏览器拦截自动播放时静默 */ });
  };
  waveform.onScrub = (seconds) => {
    const audio = $("#audio-el");
    audio.currentTime = Math.min(seconds, audio.duration || seconds);
    $("#play-time").textContent = formatTime(audio.currentTime);
    waveform.setPlayhead(audio.currentTime);
  };
  waveform.onScrubEnd = () => {
    const audio = $("#audio-el");
    if (state.scrubResumePaused) audio.pause();
    state.scrubResumePaused = false;
    const current = audio.currentTime;
    const index = (state.project?.segments || []).findIndex(
      (row) => current >= row.start && current < row.end);
    if (index !== -1) {
      state.currentSegment = index;
      setActiveSegment(index);
    }
  };
  waveform.onTogglePlay = () => $("#btn-preview-audio")?.click();
  bindEvents();
  attachMentionMenu($("#note-input"));
  attachMentionMenu($("#prompt-note"));
  // 先渲染项目与波形；ComfyUI 相关请求放到首屏之后，避免拖慢音轨显示。
  await Promise.all([loadProjects(), loadSettings()]);
  renderSettingsForm();
  renderLLMForm();
  if (state.lastProject) {
    try {
      await loadProject(state.lastProject, false);
    } catch {
      localStorage.removeItem("h3wb:lastProject");
      state.lastProject = "";
      renderProject();
    }
  } else {
    renderProject();
  }
  Promise.all([loadHealth(), loadOptions(), loadPresets()]).then(() => {
    renderStatus();
    renderGeneration();
    renderHistory();
    const audioSupport = state.health?.asr;
    $("#asr-toggle").disabled = !audioSupport;
    $("#asr-hint").textContent = audioSupport
      ? "识别词级时间戳，切点落在字与字之间（首次使用会下载模型）"
      : "本机未安装 faster-whisper，自动使用声学分段（仍可正常生成）";
  });
}

async function loadHealth() {
  try {
    state.health = await api.health();
    renderStatus();
  } catch (error) {
    state.health = null;
    toast(`无法连接后端：${error.message}`, "err");
  }
}

async function loadOptions(force = false) {
  const key = "h3wb.options";
  try {
    const data = await api.options(force);
    state.options = data.options;
    state.health && (state.health.comfy = data.comfy);
    try { localStorage.setItem(key, JSON.stringify(data.options)); } catch { /* 隐私模式忽略 */ }
    if (data.stale) toast(data.error || "ComfyUI 未连接，正在使用上次的节点列表。", "warn", 5000);
  } catch (error) {
    // ComfyUI 掉线时回退到上次成功读取的下拉列表，避免画幅/采样器变空
    let cached = null;
    try { cached = JSON.parse(localStorage.getItem(key) || "null"); } catch { cached = null; }
    if (cached) {
      state.options = cached;
      toast("ComfyUI 未连接，画幅与模型列表沿用上次结果。", "warn", 5000);
    } else {
      toast(`读取 ComfyUI 节点失败：${error.message}`, "err", 5000);
    }
  }
}

async function loadSettings() {
  try {
    const data = await api.settings();
    state.settings = data.settings;
    state.directorDefaults = data.director_defaults;
  } catch (error) {
    toast(`读取配置失败：${error.message}`, "err");
  }
}

async function loadProjects() {
  try {
    const data = await api.projects();
    state.projects = data.projects || [];
  } catch {
    state.projects = [];
  }
}

// ------------------------------- 项目 -------------------------------
function isPackFile(file) {
  const name = String(file?.name || "").toLowerCase();
  return name.endsWith(".zip") || name.endsWith(".h3pack");
}

async function importPack(file) {
  if (!file) return;
  const zone = $("#pack-drop");
  zone?.classList.add("busy");
  busy("正在解析 MV 包…");
  try {
    const data = await api.importProject(file);
    await loadProjects();
    renderHistory();
    await loadProject(data.project.id, false);
    const summary = data.summary || {};
    toast(`已导入「${data.project.name}」 · ${summary.segments || 0} 段 · ${summary.refs || 0} 张参考图`, "ok", 6000);
    (summary.warnings || []).forEach((text) => toast(text, "warn", 6000));
  } catch (error) {
    toast(error.message, "err", 9000);
  } finally {
    zone?.classList.remove("busy");
    busy("", false);
    const input = $("#pack-input");
    if (input) input.value = "";
  }
}

function exportPack() {
  if (!state.project) return;
  toast("正在生成 MV 包…", "ok");
  const link = document.createElement("a");
  link.href = api.exportProject(state.project.id);
  link.download = "";
  document.body.append(link);
  link.click();
  link.remove();
}

async function loadProject(id, announce = true) {
  // 切换项目必须停掉上一个项目的轮询/刷新定时器，否则会一直请求新项目并
  // 每 4.5s 用服务端数据重建段列表（播放高亮/滚动丢失）。
  stopPolling();
  const data = await api.project(id);
  stopAudition();
  cutHistory.length = 0;
  updateUndoButton();
  setProject(data.project);
  state.run = data.project.run || null;
  lastRunStatus = state.run?.status || "";
  renderProject();
  renderGeneration();
  setAudioWaveform(waveform);
  if (state.run && ["queued", "running"].includes(state.run.status)) startPolling();
  if (announce) toast(`已载入「${data.project.name}」`, "ok");
}

async function refreshProjectQuiet() {
  if (!state.project) return;
  try {
    const data = await api.project(state.project.id);
    const previous = state.project;
    setProject(data.project);
    renderSegments();
    renderFinal();
    if (data.project.final && !previous?.final) renderProject();
  } catch { /* 轮询期间忽略 */ }
}

function projectHasAudio(project = state.project) {
  return Number(project?.source?.samples || 0) > 0 || Number(project?.source?.duration || 0) > 0;
}

function projectNameFrom(file) {
  const typed = ($("#project-name").value || "").trim();
  if (typed) return typed.slice(0, 60);
  const base = String(file?.name || "").replace(/\.[^.]+$/, "").slice(0, 60);
  return base || "未命名作品";
}

function creationFields(file, name = projectNameFrom(file)) {
  return {
    name,
    mode: $("#mode-select").value || "singing",
    script: $("#script-input").value || "",
    target_seconds: Number($("#target-range").value),
    max_seconds: Number($("#max-range").value),
  };
}

function newProjectNameFor(file) {
  const typed = ($("#project-name").value || "").trim();
  const current = (state.project?.name || "").trim();
  // 当前作品已有音轨时，拖音频默认是「另起一部」：优先用文件名，
  // 除非用户明确改了名称（与当前作品名不同）。
  if (typed && typed !== current) return typed.slice(0, 60);
  const base = String(file?.name || "").replace(/\.[^.]+$/, "").slice(0, 60);
  return base || typed || "未命名作品";
}

async function createBlankProject() {
  busy("正在创建空白作品…");
  try {
    const created = await api.createProject(creationFields());
    stopPolling();
    cutHistory.length = 0;
    updateUndoButton();
    setProject(created.project);
    state.run = created.project.run || null;
    lastRunStatus = state.run?.status || "";
    renderProject();
    setAudioWaveform(waveform);
    await loadProjects();
    renderHistory();
    toast("空白作品已创建：拖入音频后点「分析音频并分段」", "ok", 6000);
  } catch (error) {
    toast(`新建失败：${error.message}`, "err", 6000);
  } finally {
    busy("", false);
  }
}

async function startNewProject() {
  if (["queued", "running"].includes(state.run?.status)) {
    toast("任务运行中，请先停止任务再新建作品。", "warn", 5000);
    return;
  }
  if (state.project) {
    const ok = await confirmDialog(
      "新建作品",
      "会新建一个空白作品；当前作品保留在「历史作品」里，可随时打开。继续？",
      "新建空白作品",
    );
    if (!ok) return;
    // 清空旧作品内容，避免把上一部的名称/台词带进新作品
    $("#project-name").value = "";
    $("#script-input").value = "";
    if ($("#note-input")) $("#note-input").value = "";
  }
  await createBlankProject();
}

async function handleAudioFile(file) {
  if (!file) return;
  if (isPackFile(file)) {
    importPack(file);
    return;
  }
  // 空白作品：把音频补到当前作品，而不是又新建一个
  if (state.project && !projectHasAudio()) {
    busy("正在解码音频…");
    try {
      const data = await api.uploadAudio(state.project.id, file);
      setProject(data.project);
      renderProject();
      await loadProjects();
      renderHistory();
      busy("", false);
      toast(`音频已附加：${file.name}`, "ok");
      await analyzeProject();
    } catch (error) {
      toast(error.message, "err", 6000);
      busy("", false);
    }
    return;
  }
  await createProject(file);
}

async function createProject(file) {
  if (!file) return;
  busy("正在解码音频…");
  try {
    const name = newProjectNameFor(file);
    const created = await api.createProject(creationFields(file, name), file);
    setProject(created.project);
    $("#project-name").value = name;
    renderProject();
    busy("", false);                              // 结束“解码”阶段，避免遮罩计数叠加
    busy("正在分析音频并规划分段…");
    const analyzed = await api.analyze(created.project.id, {
      mode: $("#mode-select").value,
      target_seconds: Number($("#target-range").value),
      max_seconds: Number($("#max-range").value),
      script: $("#script-input").value,
      use_asr: $("#asr-toggle").checked,
      asr_model: $("#asr-model").value,
      asr_language: $("#asr-lang").value,
    });
    setProject(analyzed.project);
    renderProject();
    setAudioWaveform(waveform);
    await loadProjects();
    renderHistory();
    toast(`已切分为 ${analyzed.project.segments.length} 段`, "ok");
  } catch (error) {
    toast(error.message, "err", 6000);
  } finally {
    busy("", false);
  }
}

async function analyzeProject() {
  if (!state.project) {
    toast("请先新建或打开作品。", "warn");
    return;
  }
  if (!projectHasAudio()) {
    toast("请先拖入音频，再分析分段。", "warn");
    return;
  }
  const target = Number($("#target-range").value);
  const maximum = Number($("#max-range").value);
  if (target > maximum) {
    toast("目标段长不能大于最长段长。", "err");
    return;
  }
  if (["queued", "running"].includes(state.run?.status)) {
    toast("任务运行中，重新分析会中断当前生成，请先停止任务。", "warn", 5000);
    return;
  }
  const useAsr = $("#asr-toggle").checked;
  const segments = state.project.segments || [];
  const hasPrompts = segments.some((row) => String(row.final_prompt || "").trim());
  const hasTakes = segments.some((row) => (row.takes || []).length || row.output);
  const modeChanged = ($("#mode-select").value || "") !== (state.project.mode || "");
  if (hasPrompts || hasTakes || modeChanged) {
    const confirmed = await confirmDialog(
      "重新分析",
      "重新分析会重建全部分段" + (hasPrompts ? "，现有分镜提示词会失效" : "")
        + (hasTakes ? "，当前选片记录会被新片段替代" : "")
        + (modeChanged ? "，模式切换后按新腔位重新分段" : "")
        + "（后端会自动备份 project.json，可在项目目录找回）。继续？",
      "继续分析",
    );
    if (!confirmed) {
      renderProject();  // 取消时把模式下拉等控件恢复成当前项目的值
      return;
    }
  }
  busy(useAsr ? "正在识别语音并分析音频（首次会下载模型，请稍候）…" : "正在重新分析音频…");
  try {
    const data = await api.analyze(state.project.id, {
      mode: $("#mode-select").value,
      target_seconds: target,
      max_seconds: maximum,
      script: $("#script-input").value,
      use_asr: useAsr,
      asr_model: $("#asr-model").value,
      asr_language: $("#asr-lang").value,
    });
    setProject(data.project);
    renderProject();
    setAudioWaveform(waveform);
    cutHistory.length = 0;
    updateUndoButton();
    toast(`重新分段完成：${data.project.segments.length} 段`, "ok");
  } catch (error) {
    toast(error.message, "err", 6000);
  } finally {
    busy("", false);
  }
}

const persistProjectField = debounce(async (patchValue) => {
  if (!state.project) return;
  try {
    const data = await api.patchProject(state.project.id, patchValue);
    setProject(data.project);
  } catch (error) {
    toast(error.message, "err");
  }
}, 800);

const persistGeneration = debounce(async () => {
  if (!state.project) return;
  await persistProjectField({ generation: collectGeneration() });
}, 500);

// ------------------------------- 分段边界 -------------------------------
const MIN_SEGMENT_SECONDS = 3;

function splitBlocker(time) {
  const segments = state.project?.segments || [];
  const row = segments.find((item) => time > Number(item.start) && time < Number(item.end));
  if (!row) return "请把切点放在某一段内部。";
  const left = time - Number(row.start);
  const right = Number(row.end) - time;
  if (left < MIN_SEGMENT_SECONDS || right < MIN_SEGMENT_SECONDS) {
    return `切点两侧都要至少 ${MIN_SEGMENT_SECONDS} 秒；当前片段 ${formatTime(row.start)}–${formatTime(row.end)}，`
      + `该位置只能切出 ${left.toFixed(1)}s + ${right.toFixed(1)}s。`;
  }
  return null;
}

async function applyCuts(request, successMessage, options = {}) {
  stopAudition();
  const snapshot = options.record === false ? null : currentCuts();
  if (snapshot) cutHistory.push(snapshot);
  try {
    const data = await request;
    setProject(data.project);
    renderProject();
    setAudioWaveform(waveform);
    await loadProjects();
    renderHistory();
    updateUndoButton();
    toast(successMessage, "ok");
    return true;
  } catch (error) {
    if (snapshot) cutHistory.pop();
    updateUndoButton();
    toast(error.message, "err", 6000);
    setAudioWaveform(waveform);
    return false;
  }
}

function currentCuts() {
  return (state.project?.segments || []).slice(0, -1).map((row) => Number(row.end));
}

function updateUndoButton() {
  const button = $("#btn-undo");
  if (button) button.disabled = cutHistory.length === 0;
}

async function undoCut() {
  if (!state.project || !cutHistory.length) return;
  const cuts = cutHistory[cutHistory.length - 1];
  const ok = await applyCuts(api.setBoundaries(state.project.id, cuts),
    "已撤回上一次分段操作", { record: false });
  if (ok) cutHistory.pop();
  updateUndoButton();
}

async function deleteBoundary(index) {
  await mergeSegment(index, "delete");
}

async function splitAtPlayhead() {
  if (!state.project) return;
  const audio = $("#audio-el");
  const time = audio.currentTime;
  if (!(time > 0 && time < (state.project.source?.duration || 0))) {
    toast("请先把播放头移到要切分的位置。", "warn");
    return;
  }
  const blocker = splitBlocker(time);
  if (blocker) {
    toast(blocker, "warn", 5000);
    return;
  }
  await applyCuts(api.splitSegment(state.project.id, time), `已在 ${time.toFixed(2)}s 处切分`);
}

async function mergeSegment(index, reason = "merge") {
  if (!state.project) return;
  if (!state.project.segments?.[index + 1]) return;
  const deleting = reason === "delete";
  const confirmed = await confirmDialog(
    deleting ? "删除分割线" : "合并分段",
    deleting
      ? `删除第 ${index + 1}、${index + 2} 段之间的分割线？两段会合并，分镜提示词会重置。`
      : `把第 ${index + 1} 段与第 ${index + 2} 段合并？合并后分镜提示词会重置，已生成的片段仍保留。`,
    deleting ? "删除" : "合并",
  );
  if (!confirmed) return;
  await applyCuts(api.mergeSegment(state.project.id, index),
    deleting ? `已删除第 ${index + 1} 段后的分割线` : `已合并第 ${index + 1}、${index + 2} 段`);
}

// ------------------------------- 接续方式 -------------------------------
async function setSegmentStitch(index, value) {
  if (!state.project) return;
  const label = `第 ${index + 1} 段`;
  try {
    const data = await api.patchSegment(state.project.id, index, { stitch: value });
    setProject(data.project);
    renderProject();
    toast(value === "seam" ? `${label}将续接上一段` : `${label}改回硬切`, "ok");
  } catch (error) {
    toast(error.message || "接续方式保存失败", "err");
    renderProject();  // 失败时把下拉恢复成项目里的值
  }
}

// ------------------------------- 参考图 -------------------------------
async function uploadRef(file, role) {
  if (!file || !state.project) return;
  busy("正在上传参考图…");
  try {
    const data = await api.addRef(state.project.id, file, role, file.name.replace(/\.[^.]+$/, ""));
    setProject(data.project);
    renderProject();
    toast(`已添加${ROLE_LABELS[role] || "参考图"}：${file.name}`, "ok");
  } catch (error) {
    toast(error.message, "err");
  } finally {
    busy("", false);
  }
}

async function changeRefRole(index, role) {
  if (!state.project) return;
  try {
    const data = await api.patchRef(state.project.id, index, { role });
    setProject(data.project);
    renderProject();
    toast(`参考图 ${index + 1} → ${ROLE_LABELS[role] || role}`, "ok");
  } catch (error) {
    toast(error.message, "err");
  }
}

async function deleteRef(index) {
  if (!state.project) return;
  const confirmed = await confirmDialog("删除参考图", `确定删除参考图 ${index + 1}？`, "删除");
  if (!confirmed) return;
  try {
    const data = await api.deleteRef(state.project.id, index);
    setProject(data.project);
    renderProject();
    toast("已删除", "ok");
  } catch (error) {
    toast(error.message, "err");
  }
}

// ------------------------------- 本段参考图 -------------------------------
function openSegRefs(index) {
  if (!state.project) return;
  const row = state.project.segments?.[index];
  if (!row) return;
  refSegmentIndex = index;
  const refs = state.project.refs || [];
  const selected = segmentRefIndices(state.project, row);
  const inherited = !(Array.isArray(row.ref_ids) && row.ref_ids.length);
  $("#segref-title").textContent = `第 ${index + 1} 段参考图`;
  $("#segref-sub").textContent = `${formatTime(row.start)} – ${formatTime(row.end)} · ${row.duration}s`;
  $("#segref-list").innerHTML = refs.length
    ? refs.map((ref, position) => `
      <label class="segref-item ${selected.includes(position) ? "picked" : ""}">
        <input type="checkbox" value="${position}" ${selected.includes(position) ? "checked" : ""}>
        <img src="/api/projects/${state.project.id}/media/refs/${escapeHtml(ref.filename)}" alt="">
        <span class="segref-meta">
          <strong>图${position + 1} · ${escapeHtml(ref.title || "参考图")}</strong>
          <span>${ROLE_LABELS[ref.role] || ref.role}${inherited ? " · 当前继承全局" : ""}</span>
        </span>
      </label>
    `).join("")
    : `<div class="segref-empty">还没有参考图，请先在左侧对应分组里添加。</div>`;
  openModal("modal-segrefs");
}

async function saveSegRefs(inherit) {
  if (!state.project || refSegmentIndex === null) return;
  const index = refSegmentIndex;
  const total = (state.project.refs || []).length;
  let ids = null;
  if (!inherit) {
    const picked = $$("#segref-list input[type=checkbox]:checked")
      .map((box) => Number(box.value))
      .sort((a, b) => a - b);
    if (!picked.length) {
      toast("至少勾选一张参考图；要使用全部请点“继承全局”。", "err");
      return;
    }
    if (picked.length < total) ids = picked;
  }
  busy("正在更新本段参考图…");
  try {
    const data = await api.patchSegment(state.project.id, index, { ref_ids: ids });
    setProject(data.project);
    renderProject();
    closeModal("modal-segrefs");
    toast(ids
      ? `第 ${index + 1} 段改为只使用 ${ids.length} 张参考图`
      : `第 ${index + 1} 段恢复继承全局参考图`, "ok");
  } catch (error) {
    toast(error.message, "err", 6000);
  } finally {
    busy("", false);
  }
}

// ------------------------------- @ 参考图补全 -------------------------------
let mentionMenu = null;
let mentionState = null;

function closeMention() {
  if (mentionMenu) {
    mentionMenu.remove();
    mentionMenu = null;
  }
  mentionState = null;
}

function mentionContext(textarea) {
  const position = textarea.selectionStart;
  const before = textarea.value.slice(0, position);
  const match = before.match(/@([^\s@]{0,12})$/);
  if (!match) return null;
  return { start: position - match[0].length, end: position, query: match[1] };
}

function mentionItems(query) {
  const project = state.project;
  if (!project) return [];
  const key = String(query || "").replace(/^图/, "").trim().toLowerCase();
  return (project.refs || []).map((ref, index) => ({
    number: index + 1,
    label: `${ROLE_LABELS[ref.role] || "参考图"}${ref.title ? ` · ${ref.title}` : ""}`,
    thumb: `/api/projects/${project.id}/media/refs/${encodeURIComponent(ref.filename || "")}`,
  })).filter((item) => !key || String(item.number) === key
    || item.label.toLowerCase().includes(key));
}

function chooseMention(textarea, context, index) {
  const item = mentionState?.items?.[index];
  if (!item) return;
  const value = textarea.value;
  const inserted = `@图${item.number} `;
  textarea.value = `${value.slice(0, context.start)}${inserted}${value.slice(context.end)}`;
  const caret = context.start + inserted.length;
  textarea.setSelectionRange(caret, caret);
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
  textarea.focus();
  closeMention();
}

function openMention(textarea, context, items, active) {
  closeMention();
  const rect = textarea.getBoundingClientRect();
  const menu = document.createElement("div");
  menu.className = "mention-menu";
  menu.innerHTML = items.length
    ? items.map((item, index) => `
        <div class="mention-item ${index === active ? "active" : ""}" data-index="${index}">
          <img src="${escapeHtml(item.thumb)}" alt="" loading="lazy">
          <b>图${item.number}</b>
          <span>${escapeHtml(item.label)}</span>
        </div>`).join("")
    : `<div class="mention-empty">还没有参考图</div>`;
  document.body.appendChild(menu);
  const height = menu.offsetHeight;
  const below = rect.bottom + 6;
  menu.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - menu.offsetWidth - 12))}px`;
  menu.style.top = `${below + height > window.innerHeight - 12
    ? Math.max(8, rect.top - height - 6) : below}px`;
  menu.addEventListener("mousedown", (event) => {
    const row = event.target.closest(".mention-item");
    if (!row) return;
    event.preventDefault();
    chooseMention(textarea, context, Number(row.dataset.index));
  });
  mentionMenu = menu;
  mentionState = { textarea, context, items, active };
}

function attachMentionMenu(textarea) {
  if (!textarea) return;
  const refresh = () => {
    if (!state.project) {
      closeMention();
      return;
    }
    const context = mentionContext(textarea);
    if (!context) {
      closeMention();
      return;
    }
    const items = mentionItems(context.query);
    const active = Math.min(mentionState?.active ?? 0, Math.max(0, items.length - 1));
    openMention(textarea, context, items, active);
  };
  textarea.addEventListener("input", refresh);
  textarea.addEventListener("click", refresh);
  textarea.addEventListener("blur", () => setTimeout(closeMention, 150));
  textarea.addEventListener("keydown", (event) => {
    if (!mentionMenu) return;
    const items = mentionState?.items || [];
    if (event.key === "Escape") {
      event.preventDefault();
      closeMention();
      return;
    }
    if ((event.key === "ArrowDown" || event.key === "ArrowUp") && items.length) {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      mentionState.active = (mentionState.active + step + items.length) % items.length;
      openMention(textarea, mentionState.context, items, mentionState.active);
      return;
    }
    if ((event.key === "Enter" || event.key === "Tab") && items.length) {
      event.preventDefault();
      chooseMention(textarea, mentionState.context, mentionState.active);
    }
  });
}

// ------------------------------- 导演简报与创作要求 -------------------------------
async function loadPresets() {
  try {
    const data = await api.briefPresets();
    state.presets = data.presets || [];
  } catch {
    state.presets = [];
  }
}

function renderPresetOptions() {
  const select = $("#prompt-preset");
  if (!select) return;
  select.innerHTML = state.presets.length
    ? state.presets.map((item) =>
      `<option value="${item.id}">${escapeHtml(item.category)} · ${escapeHtml(item.name)}</option>`).join("")
    : `<option value="">暂无模板</option>`;
}

function applyPreset() {
  if (!state.project || state.promptIndex === null) return;
  const preset = state.presets.find((item) => item.id === $("#prompt-preset").value);
  if (!preset) return;
  $("#prompt-brief").value = preset.brief;
  toast(`已导入「${preset.name}」模板，可直接修改后点“保存简报”。`, "ok", 4200);
}

async function saveBrief() {
  if (!state.project || state.promptIndex === null) return;
  const index = state.promptIndex;
  const brief = $("#prompt-brief").value.trim();
  if (!brief) {
    toast("导演简报不能为空；可先导入一个模板再改。", "err");
    return;
  }
  busy("正在保存简报…");
  try {
    const data = await api.patchSegment(state.project.id, index, {
      brief,
      note: $("#prompt-note").value.trim(),
      lyrics: $("#prompt-lyrics").value.trim(),
    });
    setProject(data.project);
    renderProject();
    toast("简报与本段创作要求已保存；点「AI 重写」即可按新内容重写提示词。", "ok", 4800);
  } catch (error) {
    toast(error.message, "err", 6000);
  } finally {
    busy("", false);
  }
}

// 关闭弹窗时自动保存 brief/note/lyrics 以及提示词正文的改动，避免用户改完直接关而丢失
async function autoSavePromptFieldsOnClose() {
  if (!state.project || state.promptIndex == null) return;
  const row = state.project.segments[state.promptIndex];
  if (!row) return;
  const nextBrief = $("#prompt-brief").value.trim();
  const nextNote = $("#prompt-note").value.trim();
  const nextLyrics = $("#prompt-lyrics").value.trim();
  const current = overlayValues(row);
  const displayed = $("#prompt-text").value;
  const nextPrompt = stripOverlayText(displayed, current).trim();
  const patch = {};
  if (nextBrief && nextBrief !== (row.brief || "")) patch.brief = nextBrief;
  if (nextNote !== (row.note || "")) patch.note = nextNote;
  if (nextLyrics !== (row.lyrics ?? row.text ?? "")) patch.lyrics = nextLyrics;
  // 正文有改动也要一起落盘，否则「写了提示词直接关弹窗」会静默丢失
  if (nextPrompt !== (row.final_prompt || "").trim()) {
    patch.final_prompt = nextPrompt;
    const prefix = overlayText(current);
    const wasBaked = row.prompt_overlays && sameOverlays(row.prompt_overlays, current);
    if (wasBaked && (!prefix || !displayed.startsWith(prefix))) patch.prompt_overlays = current;
  }
  if (!Object.keys(patch).length) return;
  try {
    const data = await api.patchSegment(state.project.id, state.promptIndex, patch);
    setProject(data.project);
    renderProject();
    renderSegments();
  } catch { /* 静默失败，用户下次保存时会重试 */ }
}

const saveNoteDebounced = debounce(async () => {
  if (!state.project) return;
  const value = $("#note-input").value;
  try {
    const data = await api.patchProject(state.project.id, { material_note: value });
    setProject(data.project);
    renderSegments();
  } catch (error) {
    toast(error.message, "err");
  }
}, 700);

// ------------------------------- 生成 -------------------------------
async function generate({ only, force, rebuild } = {}) {
  if (!state.project) return;
  if (!(state.project.refs || []).length) {
    toast("请先添加至少一张人物参考图。", "err");
    return;
  }
  if (!state.project.segments?.length) {
    toast("请先分析音频并分段。", "err");
    return;
  }
  const generation = collectGeneration();
  busy("正在提交任务…");
  try {
    const data = await api.generate(state.project.id, {
      generation,
      only: only || [],
      force: force || [],
      rebuild: Boolean(rebuild),
    });
    state.run = data.run;
    lastRunStatus = data.run.status;
    renderRun();
    startPolling();
    toast(rebuild ? "已开始重新拼接" : "任务已排队", "ok");
  } catch (error) {
    toast(error.message, "err", 6000);
  } finally {
    busy("", false);
  }
}

async function regenerateSegment(index) {
  if (!state.project) return;
  if (["queued", "running"].includes(state.run?.status)) {
    toast("任务运行中，请先停止或等待完成。", "err");
    return;
  }
  const confirmed = await confirmDialog(
    `重新生成第 ${index + 1} 段`,
    "将重新渲染该段并自动重新拼接成片，其余已完成分段会复用。继续？",
    "重新生成",
  );
  if (!confirmed) return;
  busy("正在提交单段任务…");
  try {
    const data = await api.regenerate(state.project.id, index, { generation: collectGeneration() });
    state.run = data.run;
    renderRun();
    startPolling();
    toast(`第 ${index + 1} 段已排队`, "ok");
  } catch (error) {
    toast(error.message, "err", 6000);
  } finally {
    busy("", false);
  }
}

function previewSegment(index) {
  const row = state.project?.segments?.[index];
  if (!row) return;
  if (row.selected_take) openTakeModal(index, row.selected_take);
  else if (row.output) openTakeModal(index, null);
  else toast("这一段还没有可预览的视频。", "warn");
}

// ------------------------------- 选片 / 重新合成 -------------------------------
let takeModalState = { index: null, takeId: null };

function takeSignature(row) {
  return `${Number(row.start_frame || 0)}-${Number(row.end_frame || 0)}`;
}

function visibleTakes(row) {
  const signature = takeSignature(row);
  return (Array.isArray(row.takes) ? row.takes : [])
    .filter((take) => !take.segment_signature || take.segment_signature === signature);
}

function takeMedia(project, path) {
  return `/api/projects/${project.id}/media/${String(path || "").replace(/^\/+/, "")}`;
}

function openTakeModal(index, takeId = null) {
  if (!state.project) return;
  const row = state.project.segments?.[index];
  if (!row) return;
  const takes = visibleTakes(row);
  if (!takes.length) {
    toast("这一段还没有生成过片段。", "warn");
    return;
  }
  takeModalState = {
    index,
    takeId: takeId || row.selected_take || takes[takes.length - 1].take_id,
  };
  renderTakeModal();
  openModal("modal-take");
}

function renderTakeModal() {
  const { index, takeId } = takeModalState;
  const project = state.project;
  const row = project?.segments?.[index];
  if (!row) return;
  const takes = visibleTakes(row);
  const take = takes.find((item) => item.take_id === takeId) || takes[takes.length - 1];
  if (!take) return;
  takeModalState.takeId = take.take_id;
  const current = take.take_id === row.selected_take;
  const selectedIndex = takes.findIndex((item) => item.take_id === row.selected_take);
  $("#take-title").textContent = `第 ${index + 1} 段 · 选片`;
  $("#take-sub").textContent = `${formatTime(row.start)} – ${formatTime(row.end)} · ${takes.length} 条片段 · ${current ? "当前使用中" : selectedIndex >= 0 ? `当前使用第 ${selectedIndex + 1} 条` : "尚未选用"}`;
  const video = $("#take-video");
  const source = takeMedia(project, take.file);
  video.src = source;
  video.poster = take.thumb ? takeMedia(project, take.thumb) : "";
  video.load();
  const bytes = Number(take.bytes || 0);
  const sizeText = bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)}MB`
    : `${Math.max(1, Math.round(bytes / 1024))}KB`;
  const info = [
    ["状态", current ? "使用中" : "历史片段"],
    ["生成时间", formatDate(take.created)],
    ["时长 / 帧数", `${Number(take.duration || 0).toFixed(2)}s · ${take.frames || 0} 帧`],
    ["文件大小", take.legacy ? "旧片段（迁移）" : sizeText],
    ["提示词来源", { manual: "手工编辑", llm: "AI 扩写", fallback: "内置合成", legacy: "旧片段" }[take.prompt_source || (take.legacy ? "legacy" : "")] || "—"],
    ["生成提示词", escapeHtml(String(take.prompt_text || "").replace(/\s+/g, " ").slice(0, 160)) || "—"],
  ];
  $("#take-info").innerHTML = info.map(([label, value]) =>
    `<div class="take-info-row"><span>${label}</span><b>${value}</b></div>`).join("");
  $("#take-modal-strip").innerHTML = takes.map((item, order) => `
    <button class="take-thumb ${item.take_id === take.take_id ? "active" : ""} ${item.take_id === row.selected_take ? "selected" : ""}"
      data-take="${escapeHtml(item.take_id)}" title="第 ${order + 1} 条 · ${Number(item.duration || 0).toFixed(1)}s">
      ${item.thumb ? `<img src="${takeMedia(project, item.thumb)}" alt="" loading="lazy">`
        : '<span class="take-thumb-empty"><svg><use href="#i-film"/></svg></span>'}
      <b>${order + 1}</b>
      ${item.take_id === row.selected_take ? '<i class="take-check">✓</i>' : ""}
    </button>`).join("");
  const selectBtn = $("#btn-take-select");
  selectBtn.disabled = current;
  selectBtn.textContent = current ? "当前使用中" : "选用此片段";
  $("#btn-take-delete").disabled = current && takes.length > 1;
  $("#btn-take-delete").title = current && takes.length > 1 ? "请先选用其他片段再删除" : "";
}

async function selectTakeFromModal() {
  const { index, takeId } = takeModalState;
  if (!state.project || index == null || !takeId) return;
  if (["queued", "running"].includes(state.run?.status)) {
    toast("任务运行中，请等待完成后再切换片段。", "warn");
    return;
  }
  try {
    const data = await api.selectTake(state.project.id, index, takeId);
    setProject(data.project);
    renderProject();
    renderFinal();
    closeModal("modal-take");
    toast("已选用该片段，成片需要重新合成", "ok", 5000);
  } catch (error) {
    toast(error.message, "err", 6000);
  }
}

async function deleteTakeFromModal() {
  const { index, takeId } = takeModalState;
  if (!state.project || index == null || !takeId) return;
  const confirmed = await confirmDialog("删除片段", "删除这条片段文件？不可恢复。", "删除");
  if (!confirmed) return;
  try {
    const data = await api.deleteTake(state.project.id, index, takeId);
    setProject(data.project);
    const row = state.project.segments[index];
    renderProject();
    renderFinal();
    if (visibleTakes(row).length) renderTakeModal();
    else closeModal("modal-take");
    toast("片段已删除", "ok");
  } catch (error) {
    toast(error.message, "err", 6000);
  }
}

async function assembleProject() {
  if (!state.project) return;
  if (["queued", "running"].includes(state.run?.status)) {
    toast("任务运行中，请等待完成后再合成。", "warn");
    return;
  }
  busy("正在重新合成成片…");
  try {
    const data = await api.assemble(state.project.id);
    state.run = data.run;
    renderRun();
    startPolling();
    toast("已开始重新合成成片", "ok");
  } catch (error) {
    toast(error.message, "err", 6000);
  } finally {
    busy("", false);
  }
}

async function deleteProject(id) {
  const item = (state.projects || []).find((project) => project.id === id);
  const confirmed = await confirmDialog(
    "删除项目",
    `删除「${item?.name || id}」？项目文件、参考图和成片都会删除，不可恢复。`,
    "删除",
  );
  if (!confirmed) return;
  try {
    await api.deleteProject(id);
    if (state.project?.id === id) {
      localStorage.removeItem("h3wb:lastProject");
      location.reload();
      return;
    }
    await loadProjects();
    renderHistory();
    toast("项目已删除", "ok");
  } catch (error) {
    toast(error.message, "err", 6000);
  }
}

function stopAudition() {
  const audio = $("#audio-el");
  if (state.auditionSegment !== null) {
    audio.pause();
    state.auditionSegment = null;
    renderSegments();
  }
}

function toggleAudition(index) {
  const audio = $("#audio-el");
  const row = state.project?.segments?.[index];
  if (!row) return;
  if (state.auditionSegment === index) {
    stopAudition();
    return;
  }
  state.auditionSegment = index;
  if (audio.currentTime < row.start || audio.currentTime >= row.end) audio.currentTime = row.start;
  audio.play().catch(() => {
    state.auditionSegment = null;
    renderSegments();
    toast("无法播放音频，请检查源文件。", "err");
  });
  renderSegments();
  setActiveSegment(index);
}

async function cancelRun() {
  if (!state.project) return;
  try {
    const data = await api.cancel(state.project.id);
    state.run = data.run;
    renderRun();
    toast("已请求中断", "warn");
  } catch (error) {
    toast(error.message, "err");
  }
}

function startPolling() {
  stopPolling();
  // 关键：任务状态会从上一次的终态（completed）重新回到 queued；
  // 不重置 lastRunStatus 的话，重新合成/单段重生成完成后 pollRun 认为
  // 「状态没变」，不收尾也不刷新成片，按钮会一直 disabled。
  lastRunStatus = "queued";
  idlePolls = 0;
  state.pollTimer = setInterval(pollRun, 1000);
  state.refreshTimer = setInterval(refreshProjectQuiet, 4500);
}

function stopPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  if (state.refreshTimer) clearInterval(state.refreshTimer);
  state.pollTimer = null;
  state.refreshTimer = null;
}

async function pollRun() {
  if (!state.project) return stopPolling();
  // 慢响应时不要并发：后发先至的旧快照会把 state.run 覆盖回去
  if (state.pollInFlight) return;
  state.pollInFlight = true;
  try {
    const data = await api.run(state.project.id);
    const run = data.run;
    state.run = run;
    renderRun();
    if (run.status === "running" && Number.isFinite(run.segment) && run.segment >= 0) {
      setActiveSegment(run.segment);
    }
    if (run.status === "idle") {
      // 切到没有任务的项目/任务已经不存在：连续几次 idle 就收尾，避免定时器泄漏
      idlePolls += 1;
      if (idlePolls >= 2) stopPolling();
      return;
    }
    idlePolls = 0;
    if (PROJECT_STATES.includes(run.status) && lastRunStatus !== run.status) {
      lastRunStatus = run.status;
      stopPolling();
      await loadProject(state.project.id, false);
      if (run.status === "completed") toast("成片完成，可在右侧预览与下载", "ok", 5000);
      if (run.status === "failed") toast(`生成失败：${run.error || "未知错误"}`, "err", 7000);
      if (run.status === "cancelled") toast("任务已取消", "warn");
    }
  } catch {
    /* 网络抖动时保持轮询 */
  } finally {
    state.pollInFlight = false;
  }
}

// ------------------------------- 提示词 -------------------------------
// 提示词弹窗与风格选择器都会用到；必须放在模块作用域，否则 openPrompt 调用时报
// ReferenceError，表现为点「提示词」「点击编辑」完全没反应。
function updateMotionLabel() {
  const row = state.project?.segments?.[state.promptIndex];
  const label = $("#prompt-motion-label");
  if (label) label.textContent = row?.motion ? `运镜：${row.motion.slice(0, 14)}` : "运镜：未设置";
}

function openPrompt(index) {
  if (!state.project) return;
  const row = state.project.segments[index];
  if (!row) return;
  state.promptIndex = index;
  $("#prompt-title").textContent = `分镜提示词 · 第 ${index + 1} 段`;
  $("#prompt-sub").textContent = `${formatTime(row.start)} – ${formatTime(row.end)} · ${row.duration}s · ${row.generation_frames} 帧 · ${row.visual_type === "environment" ? "空镜" : row.visual_type === "atmosphere" ? "氛围" : "表演"}`;
  $("#prompt-brief").value = row.brief || "";
  $("#prompt-note").value = row.note || "";
  $("#prompt-lyrics").value = row.lyrics ?? row.text ?? "";
  renderPresetOptions();
  $("#prompt-shot").innerHTML = [
    `开场 ${row.camera_start || "—"} / ${row.camera_start_angle || "—"}`,
    `结束 ${row.camera_end || "—"} / ${row.camera_end_angle || "—"}`,
    `运镜 ${row.camera_move_family || "—"}`,
    `入场 ${row.entry_cut_strategy || "—"}`,
    `风险 ${row.entry_cut_risk || "—"}`,
    `声音 ${row.audio_role === "vocal" ? "人声" : row.audio_role === "instrumental" ? "空镜/器乐" : "待确认"}`,
  ].map((text) => `<span>${escapeHtml(text)}</span>`).join("");
  $("#prompt-source").textContent = promptSourceLabel(row);
  renderPromptRefMap(row);
  $("#prompt-text").value = composePromptWithOverlays(row);
  renderPromptCheck(row);
  updateMotionLabel();
  openModal("modal-prompt");
}

// 全局风格 / 全局滤镜 / 本段运镜：三个可选的提示词前缀字段。
// 规则：未合并进正文时显示、生成时追加到正文最上面；AI 扩写/润色会把它们一并发给
// LLM，返回的是整段新提示词，此时用 prompt_overlays 标记“已合并”，避免重复注入。
function overlayValues(row) {
  return {
    style: (state.project?.style || "").trim(),
    filter: (state.project?.filter || "").trim(),
    motion: (row?.motion || "").trim(),
  };
}

function overlayText(values) {
  if (!values) return "";
  return [values.style, values.filter, values.motion]
    .map((value) => String(value || "").trim())
    .filter(Boolean)
    .join("\n");
}

function sameOverlays(a, b) {
  if (!a || !b) return false;
  return ["style", "filter", "motion"].every(
    (key) => String(a[key] || "").trim() === String(b[key] || "").trim());
}

function stripOverlayText(text, values) {
  const prefix = overlayText(values);
  const body = String(text ?? "");
  if (prefix && body.startsWith(prefix)) {
    return body.slice(prefix.length).replace(/^\s+/, "");
  }
  return body;
}

function composeOverlays(row, body) {
  const current = overlayValues(row);
  const baked = row?.prompt_overlays && typeof row.prompt_overlays === "object"
    ? row.prompt_overlays : null;
  const content = String(body ?? "");
  if (baked && sameOverlays(baked, current)) return content.trim();
  const cleaned = stripOverlayText(content, baked || current).trim();
  const prefix = overlayText(current);
  if (!prefix) return cleaned;
  return cleaned ? `${prefix}\n\n${cleaned}` : prefix;
}

// 提示词框显示用：优先展示正文；三个字段尚未合并时追加在正文最上面
function composePromptWithOverlays(row) {
  return composeOverlays(row, row?.final_prompt || "");
}

function promptSourceLabel(row) {
  const base = { manual: "手工编辑", llm: "AI 扩写", fallback: "内置合成" }[row?.prompt_source] || "未生成";
  const current = overlayValues(row);
  if (overlayText(current) && row?.prompt_overlays && sameOverlays(row.prompt_overlays, current)) {
    return `${base} · 已含风格/滤镜/运镜`;
  }
  return base;
}

function renderPromptRefMap(row) {
  const refs = state.project?.refs || [];
  const used = segmentRefIndices(state.project, row);
  const inherited = !(Array.isArray(row.ref_ids) && row.ref_ids.length);
  const items = used.map((position, order) => {
    const ref = refs[position] || {};
    return `<li><b>Picture ${order + 1}</b>图${position + 1} · ${escapeHtml(ref.title || "参考图")} · ${ROLE_LABELS[ref.role] || "参考"}</li>`;
  });
  if (!items.length) items.push('<li class="inherit">本段没有可用参考图</li>');
  else if (inherited) items.unshift(`<li class="inherit">继承全局 · 共 ${used.length} 张，按全局顺序编号</li>`);
  else items.unshift(`<li class="inherit">本段自定义 · 只送 ${used.length} 张，按全局顺序重新编号</li>`);
  $("#prompt-refs").innerHTML = items.join("");
}

function renderPromptCheck(row) {
  const warnings = [...(row.prompt_warnings || [])];
  const items = warnings.length
    ? warnings.map((text) => `<li>${escapeHtml(text)}</li>`)
    : [
      row.final_prompt ? '<li class="ok">服务端校验通过：六段标题顺序、图片编号与单一 [Shot 1]</li>' : "<li>尚未生成提示词</li>",
      "<li>生成前请确认参考图角色：人物 / 场景 / 服装造型 / 道具</li>",
      "<li>空镜段不会要求口型同步，也不会声明 &lt;Audio 1&gt;</li>",
    ];
  const stale = row.prompt_stale && row.final_prompt
    ? `<li>${escapeHtml(row.prompt_stale_reason || "素材或要求已修改")}：当前提示词可能已过期，保存或点「AI 重写」可按新内容刷新。</li>`
    : "";
  const current = overlayValues(row);
  const prefix = overlayText(current);
  const baked = row.prompt_overlays && sameOverlays(row.prompt_overlays, current);
  const overlayNote = !prefix
    ? ""
    : baked
      ? '<li class="ok">正文已合并当前风格/滤镜/运镜，生成时不会重复追加。</li>'
      : "<li>保存或生成时，会把全局风格 / 滤镜 / 本段运镜追加到正文最上面。</li>";
  $("#prompt-check").innerHTML = overlayNote + stale + items.join("");
}

async function savePrompt() {
  if (!state.project || state.promptIndex == null) return;
  const row = state.project.segments[state.promptIndex];
  const displayed = $("#prompt-text").value;
  const current = overlayValues(row);
  const prefix = overlayText(current);
  const body = stripOverlayText(displayed, current).trim();
  // 只在“这版正文已经是按这三个字段润色过的”时保留已合并标记，否则正文单独保存、生成时再加前缀
  const wasBaked = row?.prompt_overlays && sameOverlays(row.prompt_overlays, current);
  const keepBaked = wasBaked && (!prefix || !displayed.startsWith(prefix));
  const payload = { final_prompt: body };
  if (keepBaked && prefix) payload.prompt_overlays = current;
  try {
    const data = await api.patchSegment(state.project.id, state.promptIndex, payload);
    setProject(data.project);
    renderSegments();
    const updated = state.project.segments[state.promptIndex];
    $("#prompt-source").textContent = promptSourceLabel(updated);
    renderPromptCheck(updated);
    toast("提示词已保存", "ok");
  } catch (error) {
    toast(error.message, "err");
  }
}

function stripOverlays(text, row) {
  return stripOverlayText(text, overlayValues(row)).trim();
}

async function aiPrompt() {
  if (!state.project || state.promptIndex == null) return;
  $("#llm-test-result").textContent = "";
  busy(state.settings?.llm?.enabled ? "AI 正在扩写本段分镜…" : "正在生成确定性分镜…");
  try {
    const data = await api.generatePrompt(state.project.id, state.promptIndex);
    setProject(data.project);
    const row = state.project.segments[state.promptIndex];
    // LLM 回传的是整段新提示词（已包含风格/滤镜/运镜），直接覆盖提示词框，
    // 靠 prompt_overlays 标记避免下次打开时又把三个字段重复注入一遍。
    $("#prompt-text").value = composePromptWithOverlays(row);
    $("#prompt-source").textContent = promptSourceLabel(row);
    renderPromptCheck(row);
    renderSegments();
    (data.warnings || []).forEach((text) => toast(text, "warn", 5000));
    toast("分镜已更新", "ok");
  } catch (error) {
    toast(error.message, "err", 6000);
  } finally {
    busy("", false);
  }
}

async function resetPrompt() {
  if (!state.project || state.promptIndex == null) return;
  try {
    const data = await api.patchSegment(state.project.id, state.promptIndex, { final_prompt: "" });
    setProject(data.project);
    renderSegments();
    $("#prompt-text").value = "";
    $("#prompt-source").textContent = promptSourceLabel(state.project.segments[state.promptIndex]);
    toast("已清空，保存或 AI 重写会自动生成", "ok");
  } catch (error) {
    toast(error.message, "err");
  }
}

async function writeAllPrompts() {
  if (!state.project) return;
  busy("正在扩写全部分镜…（本地模型可能较慢）");
  try {
    const data = await api.generateAllPrompts(state.project.id);
    setProject(data.project);
    renderSegments();
    (data.warnings || []).forEach((text) => toast(text, "warn", 5000));
    toast("全部分镜已更新", "ok");
  } catch (error) {
    toast(error.message, "err", 6000);
  } finally {
    busy("", false);
  }
}

// ------------------------------- 设置 / LLM -------------------------------
const saveLlmDebounced = debounce(async () => {
  const payload = {
    llm: {
      enabled: $("#llm-enable").checked,
      base_url: $("#llm-base").value.trim(),
      model: $("#llm-model").value.trim(),
      vision: (state.settings?.llm?.vision !== false),
    },
  };
  const key = $("#llm-key").value.trim();
  if (key && !key.startsWith("••••")) payload.llm.api_key = key;
  try {
    const data = await api.saveSettings(payload);
    state.settings = data.settings;
    renderStatus();
    $("#llm-model-label").textContent = data.settings.llm.enabled
      ? (data.settings.llm.model || "已启用") : "未启用";
  } catch (error) {
    toast(error.message, "err");
  }
}, 800);

async function saveSettings() {
  const llm = state.settings?.llm || {};
  const payload = {
    comfy_url: $("#set-comfy").value.trim(),
    ffmpeg: $("#set-ffmpeg").value.trim() || "ffmpeg",
    ffprobe: $("#set-ffprobe").value.trim() || "ffprobe",
    llm: {
      ...llm,
      enabled: $("#llm-enable").checked,
      base_url: $("#set-llm-base").value.trim(),
      model: $("#set-llm-model").value.trim(),
      timeout: Number($("#set-llm-timeout").value) || 180,
      temperature: Number($("#set-llm-temp").value),
      max_tokens: Number($("#set-llm-tokens").value) || 4096,
      vision: $("#set-llm-vision").checked,
    },
  };
  const key = $("#set-llm-key").value.trim();
  if (key && !key.startsWith("••••")) payload.llm.api_key = key;
  busy("正在保存设置…");
  try {
    const data = await api.saveSettings(payload);
    state.settings = data.settings;
    renderSettingsForm();
    renderLLMForm();
    renderStatus();
    closeModal("modal-settings");
    toast("设置已保存", "ok");
    await Promise.all([loadOptions(true), loadHealth()]);
    renderStatus();
  } catch (error) {
    toast(error.message, "err", 6000);
  } finally {
    busy("", false);
  }
}

async function testLlm() {
  const button = $("#btn-llm-test");
  button.disabled = true;
  $("#llm-test-result").textContent = "测试中…";
  const payload = {
    base_url: $("#llm-base").value.trim(),
    model: $("#llm-model").value.trim(),
    vision: state.settings?.llm?.vision !== false,
    timeout: state.settings?.llm?.timeout || 60,
  };
  const key = $("#llm-key").value.trim();
  if (key && !key.startsWith("••••")) payload.api_key = key;
  try {
    const data = await api.testLLM(payload);
    $("#llm-test-result").textContent = `连接成功 · ${data.result.model} · ${data.result.elapsed}s`;
    toast("LLM 连接成功", "ok");
  } catch (error) {
    $("#llm-test-result").textContent = "连接失败";
    toast(error.message, "err", 6000);
  } finally {
    button.disabled = false;
  }
}

// ------------------------------- 事件 -------------------------------
function bindEvents() {
  $("#audio-input").addEventListener("change", (event) => {
    handleAudioFile(event.target.files?.[0]);
    event.target.value = "";
  });
  $("#pack-input").addEventListener("change", (event) => {
    importPack(event.target.files?.[0]);
    event.target.value = "";
  });
  const packZone = $("#pack-drop");
  if (packZone) {
    ["dragenter", "dragover"].forEach((type) => packZone.addEventListener(type, (event) => {
      event.preventDefault();
      packZone.classList.add("drag");
    }));
    ["dragleave", "drop"].forEach((type) => packZone.addEventListener(type, (event) => {
      event.preventDefault();
      packZone.classList.remove("drag");
    }));
    packZone.addEventListener("drop", (event) => {
      const file = event.dataTransfer?.files?.[0];
      if (isPackFile(file)) importPack(file);
      else toast("请拖入 .h3pack.zip 包（音频请拖到上方区域）。", "warn");
    });
  }
  const dropzone = $("#audio-drop");
  ["dragenter", "dragover"].forEach((type) => dropzone.addEventListener(type, (event) => {
    event.preventDefault();
    dropzone.classList.add("drag");
  }));
  ["dragleave", "drop"].forEach((type) => dropzone.addEventListener(type, (event) => {
    event.preventDefault();
    dropzone.classList.remove("drag");
  }));
  dropzone.addEventListener("drop", (event) => {
    handleAudioFile(event.dataTransfer?.files?.[0]);
  });

  $("#ref-groups").addEventListener("change", (event) => {
    const uploader = event.target.closest("[data-ref-upload]");
    if (uploader) {
      uploadRef(uploader.files?.[0], uploader.dataset.refUpload);
      uploader.value = "";
      return;
    }
    const roleSelect = event.target.closest("[data-ref-role]");
    if (roleSelect) changeRefRole(Number(roleSelect.dataset.refRole), roleSelect.value);
  });

  $("#ref-groups").addEventListener("click", (event) => {
    const target = event.target.closest("[data-act]");
    if (!target) return;
    if (target.dataset.act === "del-ref") deleteRef(Number(target.dataset.index));
  });

  $("#segref-list").addEventListener("change", (event) => {
    const box = event.target.closest('input[type="checkbox"]');
    if (!box) return;
    box.closest(".segref-item")?.classList.toggle("picked", box.checked);
  });
  $("#btn-segref-save").addEventListener("click", () => saveSegRefs(false));
  $("#btn-segref-inherit").addEventListener("click", () => saveSegRefs(true));
  $("#btn-split").addEventListener("click", splitAtPlayhead);
  $("#btn-undo").addEventListener("click", undoCut);
  $("#stitch-select").addEventListener("change", () => {
    $("#seam-secondpass-row").hidden = $("#stitch-select").value === "hard";
  });

  $("#segment-list").addEventListener("click", (event) => {
    const target = event.target.closest("[data-act]");
    if (!target || target.disabled) return;
    const index = Number(target.dataset.index);
    if (target.dataset.act === "open-prompt") openPrompt(index);
    if (target.dataset.act === "open-take") openTakeModal(index, target.dataset.take || null);
    if (target.dataset.act === "regen") regenerateSegment(index);
    if (target.dataset.act === "preview") previewSegment(index);
    if (target.dataset.act === "audition") toggleAudition(index);
    if (target.dataset.act === "refs") openSegRefs(index);
    if (target.dataset.act === "merge") mergeSegment(index);
  });

  $("#segment-list").addEventListener("change", (event) => {
    const select = event.target.closest("[data-act='stitch']");
    if (!select) return;
    setSegmentStitch(Number(select.dataset.index), select.value);
  });

  $("#btn-assemble").addEventListener("click", assembleProject);
  $("#btn-take-select").addEventListener("click", selectTakeFromModal);
  $("#btn-take-delete").addEventListener("click", deleteTakeFromModal);
  $("#take-modal-strip").addEventListener("click", (event) => {
    const button = event.target.closest("[data-take]");
    if (!button) return;
    takeModalState.takeId = button.dataset.take;
    renderTakeModal();
  });

  $("#btn-analyze").addEventListener("click", analyzeProject);
  $("#btn-generate").addEventListener("click", () => generate());
  $("#btn-regenerate-all").addEventListener("click", () => {
    const total = state.project?.segments?.length || 0;
    if (!total) return;
    generate({ force: Array.from({ length: total }, (_, index) => index) });
  });
  $("#btn-write-all").addEventListener("click", writeAllPrompts);
  $("#btn-export-pack").addEventListener("click", exportPack);
  $("#btn-cancel").addEventListener("click", cancelRun);
  $("#btn-rebuild").addEventListener("click", assembleProject);
  $("#btn-history").addEventListener("click", async () => {
    await loadProjects();
    renderHistory();
    openModal("modal-history");
  });
  $("#btn-new-project")?.addEventListener("click", startNewProject);
  $("#btn-open-project")?.addEventListener("click", async () => {
    await loadProjects();
    renderHistory();
    openModal("modal-history");
  });
  $("#btn-settings").addEventListener("click", () => {
    renderSettingsForm();
    openModal("modal-settings");
  });
  $("#btn-save-settings").addEventListener("click", saveSettings);
  $("#btn-llm-test").addEventListener("click", testLlm);
  $("#btn-save-prompt").addEventListener("click", savePrompt);
  $("#btn-apply-preset").addEventListener("click", applyPreset);
  $("#btn-save-brief").addEventListener("click", saveBrief);
  $("#note-input").addEventListener("input", saveNoteDebounced);
  $("#asr-toggle").addEventListener("change", () => {
    $("#asr-options").classList.toggle("hidden", !$("#asr-toggle").checked);
  });
  $("#btn-ai-prompt").addEventListener("click", aiPrompt);
  $("#btn-reset-prompt").addEventListener("click", resetPrompt);
  $("#btn-log-toggle").addEventListener("click", () => $("#run-log").classList.toggle("hidden"));
  $("#btn-confirm-yes").addEventListener("click", () => settleConfirm(true));

  $("#target-range").addEventListener("input", (event) => {
    const maximum = Number($("#max-range").value);
    if (Number(event.target.value) > maximum) $("#max-range").value = event.target.value;
    $("#target-label").textContent = `${Number($("#target-range").value).toFixed(1)}s`;
    $("#max-label").textContent = `${Number($("#max-range").value).toFixed(1)}s`;
  });
  $("#max-range").addEventListener("input", (event) => {
    const target = Number($("#target-range").value);
    if (Number(event.target.value) < target) $("#target-range").value = event.target.value;
    $("#target-label").textContent = `${Number($("#target-range").value).toFixed(1)}s`;
    $("#max-label").textContent = `${Number($("#max-range").value).toFixed(1)}s`;
  });
  $("#mp-range").addEventListener("input", () => {
    $("#mp-label").textContent = `${Number($("#mp-range").value).toFixed(1)}MP`;
    persistGeneration();
  });
  $("#shift-toggle").addEventListener("change", () => {
    $("#shift-row").hidden = !$("#shift-toggle").checked;
    persistGeneration();
  });
  ["#steps-input", "#cfg-input", "#lora-strength", "#transition-input", "#lowres-input",
    "#wmin-input", "#wmax-input", "#shift-video", "#shift-audio", "#seed-input",
    "#ref-size-select", "#tiling-toggle", "#refiner-toggle", "#aspect-select",
    "#outfps-select",
    "#stitch-select",
    "#seam-secondpass-toggle",
    "#unet-select", "#clip-select", "#vae-video-select", "#vae-audio-select",
    "#lora-select", "#upscaler-select"].forEach((selector) => {
    const element = $(selector);
    element && element.addEventListener("change", persistGeneration);
  });

  $("#project-name").addEventListener("change", (event) => {
    const name = event.target.value.trim();
    $("#stage-name").textContent = name || "未命名作品";
    persistProjectField({ name });
  });
  $("#script-input").addEventListener("change", (event) => {
    persistProjectField({ script: event.target.value });
  });
  $("#mode-select").addEventListener("change", () => {
    // 空白作品还没有音轨：只保存模式，等上传音频后再分析
    if (!projectHasAudio()) {
      persistProjectField({ mode: $("#mode-select").value });
      return;
    }
    analyzeProject();
  });

  ["#llm-enable", "#llm-base", "#llm-model", "#llm-key", "#set-llm-vision"].forEach((selector) => {
    const element = $(selector);
    element && element.addEventListener("change", saveLlmDebounced);
  });
  $$("[data-llm-preset]").forEach((button) => button.addEventListener("click", () => {
    const preset = button.dataset.llmPreset;
    if (preset === "ollama") {
      $("#llm-base").value = "http://127.0.0.1:11434/v1";
      $("#llm-model").value = $("#llm-model").value || "qwen2.5:7b";
    }
    if (preset === "openai") {
      $("#llm-base").value = "https://api.openai.com/v1";
      $("#llm-model").value = $("#llm-model").value || "gpt-4o-mini";
    }
    if (preset === "custom") $("#llm-base").focus();
    saveLlmDebounced();
  }));

  $("#history-grid").addEventListener("click", async (event) => {
    const remove = event.target.closest("[data-del-project]");
    if (remove) {
      event.stopPropagation();
      await deleteProject(remove.dataset.delProject);
      return;
    }
    const item = event.target.closest("[data-open-project]");
    if (!item) return;
    closeModal("modal-history");
    loadProject(item.dataset.openProject);
  });

  $("#btn-preview-audio").addEventListener("click", () => {
    const audio = $("#audio-el");
    if (audio.paused) {
      if (state.auditionSegment !== null) {
        state.auditionSegment = null;
        renderSegments();
      }
      audio.play().catch(() => {});
    } else {
      audio.pause();
    }
  });
  const audio = $("#audio-el");
  audio.addEventListener("play", () => {
    state.audioPlaying = true;
    $("#btn-preview-audio").innerHTML = '<svg><use href="#i-pause"/></svg>';
  });
  audio.addEventListener("pause", () => {
    state.audioPlaying = false;
    $("#btn-preview-audio").innerHTML = '<svg><use href="#i-play"/></svg>';
    if (state.auditionSegment !== null) {
      state.auditionSegment = null;
      renderSegments();
    }
  });
  audio.addEventListener("timeupdate", () => {
    $("#play-time").textContent = formatTime(audio.currentTime);
    waveform.setPlayhead(audio.currentTime);
    if (!state.project?.segments) return;
    if (state.auditionSegment !== null) {
      const row = state.project.segments[state.auditionSegment];
      if (row && audio.currentTime >= row.end - 0.05) {
        audio.pause();
        audio.currentTime = row.end;
      }
    }
    const index = state.project.segments.findIndex(
      (row) => audio.currentTime >= row.start && audio.currentTime < row.end);
    if (index !== -1 && index !== state.currentSegment) {
      state.currentSegment = index;
      setActiveSegment(index);
      if (!["queued", "running"].includes(state.run?.status)) {
        $(".seg-card.current")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    }
  });
  audio.addEventListener("ended", () => {
    waveform.setPlayhead(0);
    $("#play-time").textContent = formatTime(0);
  });

  document.addEventListener("click", (event) => {
    const closer = event.target.closest("[data-close]");
    if (closer) {
      const modal = closer.closest(".modal");
      if (modal) {
        if (modal.id === "modal-confirm") settleConfirm(false);
        else if (modal.id === "modal-prompt") { autoSavePromptFieldsOnClose(); closeModal(modal.id); }
        else if (modal.id === "modal-creative") { saveCreativeCustomOnClose(); closeModal(modal.id); }
        else closeModal(modal.id);
      }
      return;
    }
    if (event.target.classList?.contains("modal")) {
      if (event.target.id === "modal-confirm") settleConfirm(false);
      else if (event.target.id === "modal-prompt") { autoSavePromptFieldsOnClose(); closeModal(event.target.id); }
      else if (event.target.id === "modal-creative") { saveCreativeCustomOnClose(); closeModal(event.target.id); }
      else closeModal(event.target.id);
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      $$(".modal").forEach((modal) => {
        if (modal.hidden) return;
        if (modal.id === "modal-confirm") settleConfirm(false);
        else if (modal.id === "modal-prompt") { autoSavePromptFieldsOnClose(); closeModal(modal.id); }
        else if (modal.id === "modal-creative") { saveCreativeCustomOnClose(); closeModal(modal.id); }
        else closeModal(modal.id);
      });
    }
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !$("#modal-prompt").hidden) {
      savePrompt();
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
      const target = event.target;
      const tag = (target?.tagName || "").toLowerCase();
      if (["input", "textarea", "select"].includes(tag) || target?.isContentEditable) return;
      event.preventDefault();
      undoCut();
    }
  });

  window.addEventListener("unhandledrejection", (event) => {
    toast(event.reason?.message || "发生未处理的错误", "err");
  });

  // 同步异常（例如点击事件里的 ReferenceError）以前只会进控制台，界面毫无反应；
  // 这里统一提示出来，避免再出现「点了没反应」却查不到原因。
  // ResizeObserver 的 "loop completed" 是浏览器良性告警，不提示。
  window.addEventListener("error", (event) => {
    const message = event?.message || "";
    if (!message || /ResizeObserver loop/i.test(message)) return;
    toast(`页面脚本出错：${message}`, "err", 6000);
  });

  // ===== 风格 / 滤镜 / 运镜 卡片选择器 =====
  // 风格、滤镜是项目级（主页全局设置），运镜是分段级（从本段「提示词」弹窗进入）。
  // 三个字段默认空；选择预设或自定义编辑后写入项目/分段，再叠加到提示词顶部。
  const CREATIVE_META = {
    style: {
      title: "全局风格", placeholder: "例如：赛璐璐动画风格，暖阳高饱和，保留主体与构图",
    },
    filter: {
      title: "全局滤镜", placeholder: "例如：柔和肤色，低对比电影胶片质感",
    },
    motion: {
      title: "本段运镜", placeholder: "例如：缓慢推进镜头，轻微手持呼吸感",
    },
  };
  let catalog = [];
  let creativeCtx = { kind: "style", scope: "project", tab: "style", canMotion: false };

  // 自定义 tab 直接输入时，关闭弹窗也要保存（避免编辑完点「完成/关闭」丢失）
  function saveCreativeCustomOnClose() {
    const custom = $("#creative-custom");
    if (!custom || custom.classList.contains("hidden")) return;
    saveCreativeValue($("#creative-custom-text").value.trim());
  }

  let catalogLoading = null;
  async function loadCatalog() {
    if (catalog.length) return catalog;
    if (!catalogLoading) {
      catalogLoading = (async () => {
        try {
          const res = await fetch("/api/creative-catalog");
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const data = await res.json();
          catalog = data.items || [];
        } catch { catalog = []; }  // 失败置空，下次打开会重试
        finally { catalogLoading = null; }
      })();
    }
    await catalogLoading;
    return catalog;
  }

  function creativeMeta(kind) {
    return CREATIVE_META[kind] || { title: "选择", placeholder: "" };
  }

  function syncCreativeView() {
    const { kind, scope, tab, canMotion } = creativeCtx;
    $("#creative-title").textContent = creativeMeta(kind).title;
    $("#creative-sub").textContent = scope === "project" ? "全片统一" : `第 ${(state.promptIndex ?? 0) + 1} 段`;
    $$(".creative-tab").forEach((button) => {
      const name = button.dataset.tab;
      button.hidden = name === "motion" && !canMotion;
      button.classList.toggle("active", name === tab);
    });
    const isCustom = tab === "custom";
    $("#creative-custom").classList.toggle("hidden", !isCustom);
    $("#creative-grid").classList.toggle("hidden", isCustom);
    $("#creative-search").classList.toggle("hidden", isCustom);
    $("#creative-cats").classList.toggle("hidden", isCustom);
    if (isCustom) {
      const label = $("#creative-custom-label");
      if (label) label.textContent = `${creativeMeta(kind).title} · 自定义内容（直接作为提示词片段使用）`;
      $("#creative-custom-text").placeholder = creativeMeta(kind).placeholder;
      $("#creative-custom-text").value = currentCreativeValue();
    } else {
      $("#creative-cats").dataset.active = "全部";
      renderCreativeGrid();
    }
  }

  async function openCreativePicker(kind, scope) {
    creativeCtx = {
      kind,
      scope,
      tab: kind === "custom" ? "custom" : kind,
      canMotion: scope === "segment",
    };
    syncCreativeView();
    openModal("modal-creative");
    if (!catalog.length) {
      await loadCatalog();
      renderCreativeGrid();
    }
  }

  function currentCreativeValue() {
    if (!state.project) return "";
    if (creativeCtx.scope === "project") return state.project[creativeCtx.kind] || "";
    const row = state.project.segments[state.promptIndex];
    return row?.motion || "";
  }

  function renderCreativeGrid() {
    const kind = creativeCtx.kind;
    const q = ($("#creative-search").value || "").trim().toLowerCase();
    const items = catalog.filter((it) => it.kind === kind &&
      (!q || (it.name + (it.description || "")).toLowerCase().includes(q)));
    const cats = ["全部", ...Array.from(new Set(items.map((it) => it.category)))];
    const activeCat = $("#creative-cats").dataset.active || "全部";
    $("#creative-cats").innerHTML = cats.map((c) =>
      `<button class="creative-cat ${c === activeCat ? "active" : ""}" data-cat="${escapeHtml(c)}">${escapeHtml(c)}</button>`).join("");
    const filtered = activeCat === "全部" ? items : items.filter((it) => it.category === activeCat);
    const current = currentCreativeValue();
    $("#creative-grid").innerHTML = filtered.map((it) => {
      const imgSrc = it.poster || it.preview || "";
      return `<div class="creative-card ${it.prompt === current ? "active" : ""}" data-id="${it.id}" title="${escapeHtml(it.description || it.prompt || "")}">
         <div class="thumb"><img src="${imgSrc}" alt="${escapeHtml(it.name)}" loading="lazy"></div>
         <div class="name">${escapeHtml(it.name)}</div>
       </div>`;
    }).join("") || '<div class="panel-note">无匹配项</div>';
  }

  async function saveCreativeValue(text) {
    if (!state.project) return;
    const { kind, scope } = creativeCtx;
    if (scope === "project") {
      await api.patchProject(state.project.id, { [kind]: text });
      const data = await api.project(state.project.id);
      setProject(data.project);
      renderProject();
      return;
    }
    // 运镜：保存后同步刷新提示词弹窗里顶部的前缀，保留用户尚未保存的正文
    const promptOpen = !$("#modal-prompt").hidden;
    const before = state.project.segments[state.promptIndex];
    const body = promptOpen && before ? stripOverlays($("#prompt-text").value, before) : "";
    await api.patchSegment(state.project.id, state.promptIndex, { motion: text });
    const data = await api.project(state.project.id);
    setProject(data.project);
    renderSegments();
    updateMotionLabel();
    if (promptOpen) {
      const row = state.project.segments[state.promptIndex];
      if (row) {
        $("#prompt-text").value = composeOverlays(row, body);
        $("#prompt-source").textContent = promptSourceLabel(row);
        renderPromptCheck(row);
      }
    }
  }

  // 主页全局风格 / 滤镜入口
  $$(".style-btn").forEach((btn) => {
    btn.addEventListener("click", () => openCreativePicker(btn.dataset.kind, "project"));
  });
  // 本段运镜入口（提示词弹窗内）
  $("#btn-prompt-motion")?.addEventListener("click", () => openCreativePicker("motion", "segment"));

  // 三个字段共用同一个卡片弹窗，tab 切换时同步切换“写入哪个字段”
  $$(".creative-tab").forEach((tabButton) => {
    tabButton.addEventListener("click", () => {
      const tab = tabButton.dataset.tab;
      if (tab === "motion" && !creativeCtx.canMotion) return;
      if (tab === "custom") {
        creativeCtx.tab = "custom";
      } else {
        creativeCtx.kind = tab;
        creativeCtx.tab = tab;
        creativeCtx.scope = tab === "motion" ? "segment" : "project";
      }
      syncCreativeView();
    });
  });

  $("#creative-search").addEventListener("input", debounce(renderCreativeGrid, 150));
  $("#creative-cats").addEventListener("click", (e) => {
    const cat = e.target.closest(".creative-cat");
    if (!cat) return;
    $("#creative-cats").dataset.active = cat.dataset.cat;
    renderCreativeGrid();
  });
  $("#creative-grid").addEventListener("click", async (e) => {
    const card = e.target.closest(".creative-card");
    if (!card) return;
    const item = catalog.find((it) => it.id === card.dataset.id);
    if (!item) return;
    await saveCreativeValue(item.prompt);
    toast(`已选择「${item.name}」`, "ok");
    closeModal("modal-creative");
  });
  // 自定义编辑：失焦（包括点「完成」）即保存；清空内容则清除该字段
  $("#creative-custom-text").addEventListener("change", () => {
    saveCreativeValue($("#creative-custom-text").value.trim());
  });
  $("#creative-clear").addEventListener("click", async () => {
    await saveCreativeValue("");
    $("#creative-custom-text").value = "";
    renderCreativeGrid();
    toast("已清除", "ok");
  });

  loadCatalog();
}

init();
