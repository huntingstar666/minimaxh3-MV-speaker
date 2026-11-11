// 视图渲染
import { state, setProject } from "./store.js";
import { $, $$, escapeHtml, formatDuration, formatTime, formatDate } from "./ui.js";

export const ROLE_LABELS = {
  performer: "人物",
  environment: "场景",
  appearance: "服装造型",
  subject: "道具/配角",
  other: "补充素材",
};

const ROLE_OPTIONS = [
  ["performer", "人物参考"],
  ["environment", "场景参考"],
  ["appearance", "服装造型"],
  ["subject", "道具 / 配角"],
  ["other", "补充素材"],
];

// 左栏把参考图拆成四个用途分组，上传时直接确定用途，不再靠“点标签轮换”
export const REF_GROUPS = [
  { role: "performer", label: "人物参考图", note: "身份、脸、发型、体型", accent: "performer" },
  { role: "environment", label: "场景参考图", note: "空间结构、材质、灯光", accent: "environment" },
  { role: "appearance", label: "服装造型", note: "服装、配饰、妆发细节", accent: "appearance" },
  { role: "subject", label: "道具 / 配角", note: "需要保持外观的物件或配角", accent: "subject" },
];

export function segmentRefIndices(project, row) {
  const refs = project?.refs || [];
  const valid = refs.map((ref, index) => (ref?.filename ? index : -1)).filter((index) => index >= 0);
  const ids = row?.ref_ids;
  if (!Array.isArray(ids) || !ids.length) return valid;
  const picked = ids
    .map((value) => Number(value))
    .filter((value) => value >= 0 && value < refs.length && refs[value]?.filename);
  return picked.length ? picked : valid;
}

const VISUAL_LABELS = {
  performance: "人物表演",
  atmosphere: "氛围表演",
  environment: "空镜环境",
};

const STATUS_LABELS = {
  idle: "待生成",
  running: "生成中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const STAGE_LABELS = {
  queued: "排队等待", prepare: "准备素材", prompt: "生成提示词", queue: "提交 ComfyUI",
  sampling: "双采采样", assemble: "拼接成片", done: "完成", error: "出错",
};

export function renderStatus() {
  const { health } = state;
  const comfy = $("#comfy-chip");
  const gpu = $("#gpu-chip");
  const llm = $("#llm-chip");
  if (health?.comfy) {
    comfy.className = `status-chip ${health.comfy.ok ? "ok" : "err"}`;
    comfy.querySelector("b").textContent = health.comfy.ok
      ? (health.comfy.version ? `v${health.comfy.version}` : "在线") : "离线";
    gpu.className = `status-chip ${health.comfy.ok ? "ok" : ""}`;
    const vram = health.comfy.vram_free;
    gpu.querySelector("b").textContent = health.comfy.device
      ? `${health.comfy.device.replace(/NVIDIA GeForce /, "")}${vram ? ` · ${(vram / 1024 ** 3).toFixed(0)}G 空闲` : ""}`
      : "—";
  } else {
    comfy.className = "status-chip err";
    comfy.querySelector("b").textContent = "不可用";
  }
  const enabled = Boolean(state.settings?.llm?.enabled);
  llm.className = `status-chip ${enabled ? "ok" : "warn"}`;
  llm.querySelector("b").textContent = enabled ? (state.settings.llm.model || "已启用") : "未启用";
}

const ASPECT_LABELS = {
  "1:1 (Square)": "1:1 方形",
  "2:3 (Portrait Photo)": "2:3 竖幅",
  "3:2 (Photo)": "3:2 横幅",
  "3:4 (Portrait Standard)": "3:4 竖屏",
  "4:3 (Standard)": "4:3 横屏",
  "9:16 (Portrait Widescreen)": "9:16 竖屏全屏",
  "16:9 (Widescreen)": "16:9 宽屏",
  "21:9 (Ultrawide)": "21:9 超宽",
};

export function fillSelect(element, values, current, autoLabel = "自动选择", labels = null) {
  if (!element) return;
  const options = [];
  if (autoLabel) options.push(`<option value="">${escapeHtml(autoLabel)}</option>`);
  (values || []).forEach((value) => {
    options.push(`<option value="${escapeHtml(value)}">${escapeHtml((labels && labels[value]) || value)}</option>`);
  });
  if (current && !(values || []).includes(current)) {
    options.push(`<option value="${escapeHtml(current)}">${escapeHtml((labels && labels[current]) || current)}</option>`);
  }
  element.innerHTML = options.join("");
  element.value = current || "";
}

export function renderOptions() {
  const options = state.options || {};
  const generation = { ...(state.settings?.generation || {}), ...(state.project?.generation || {}) };
  fillSelect($("#aspect-select"), options.aspects || [], generation.aspect_ratio, "", ASPECT_LABELS);
  fillSelect($("#unet-select"), options.unet || [], generation.unet);
  fillSelect($("#clip-select"), options.clip || [], generation.clip);
  fillSelect($("#vae-video-select"), options.vae || [], generation.vae_video);
  fillSelect($("#vae-audio-select"), options.vae || [], generation.vae_audio);
  fillSelect($("#lora-select"), options.lora || [], generation.lora);
  fillSelect($("#upscaler-select"), options.upscaler || options.upscalers_common || [],
    generation.upscaler_model);
}

export function renderGeneration() {
  const generation = { ...(state.settings?.generation || {}), ...(state.project?.generation || {}) };
  renderOptions();
  if (generation.aspect_ratio) $("#aspect-select").value = generation.aspect_ratio;
  $("#outfps-select").value = String(generation.output_fps ?? 24);
  const stitch = generation.stitch === "mixed" ? "mixed"
    : generation.stitch === "seam" ? "seam" : "hard";
  $("#stitch-select").value = stitch;
  $("#seam-secondpass-toggle").checked = generation.seam_second_pass !== false;
  $("#seam-secondpass-row").hidden = stitch === "hard";
  // overlap 对 seam / mixed 都生效；hard 没有重叠
  $("#seam-overlap-select").value = String(generation.seam_overlap) === "39" ? "39" : "22";
  $("#seam-overlap-row").hidden = stitch === "hard";
  $("#mp-range").value = generation.megapixels ?? 1;
  $("#mp-label").textContent = `${Number($("#mp-range").value).toFixed(1)}MP`;
  $("#steps-input").value = generation.steps ?? 6;
  $("#cfg-input").value = generation.cfg ?? 1;
  $("#lora-strength").value = generation.lora_strength ?? 0.65;
  $("#transition-input").value = generation.transition_step ?? 4;
  $("#lowres-input").value = generation.lowres_scale ?? 0.5;
  $("#wmin-input").value = generation.w_min ?? 0.5;
  $("#wmax-input").value = generation.w_max ?? 1;
  $("#ref-size-select").value = generation.ref_image_size ?? "match";
  $("#tiling-toggle").checked = generation.highres_tiling !== false;
  $("#shift-toggle").checked = Boolean(generation.sigma_shift);
  $("#shift-row").hidden = !generation.sigma_shift;
  $("#shift-video").value = generation.shift_video ?? 12;
  $("#shift-audio").value = generation.shift_audio ?? 3;
  $("#refiner-toggle").checked = Boolean(generation.sigma_refiner);
  $("#seed-input").value = generation.seed ?? 0;
}

export function collectGeneration() {
  const base = { ...(state.settings?.generation || {}), ...(state.project?.generation || {}) };
  return {
    ...base,
    aspect_ratio: $("#aspect-select").value,
    megapixels: Number($("#mp-range").value),
    output_fps: Number($("#outfps-select").value),
    stitch: $("#stitch-select").value,
    seam_second_pass: $("#seam-secondpass-toggle").checked,
    seam_overlap: Number($("#seam-overlap-select").value),
    steps: Number($("#steps-input").value),
    cfg: Number($("#cfg-input").value),
    unet: $("#unet-select").value,
    clip: $("#clip-select").value,
    vae_video: $("#vae-video-select").value,
    vae_audio: $("#vae-audio-select").value,
    lora: $("#lora-select").value,
    lora_strength: Number($("#lora-strength").value),
    upscaler_model: $("#upscaler-select").value,
    ref_image_size: $("#ref-size-select").value,
    transition_step: Number($("#transition-input").value),
    lowres_scale: Number($("#lowres-input").value),
    w_min: Number($("#wmin-input").value),
    w_max: Number($("#wmax-input").value),
    highres_tiling: $("#tiling-toggle").checked,
    sigma_shift: $("#shift-toggle").checked,
    shift_video: Number($("#shift-video").value),
    shift_audio: Number($("#shift-audio").value),
    sigma_refiner: $("#refiner-toggle").checked,
    seed: Number($("#seed-input").value),
  };
}

export function renderLLMForm() {
  const llm = state.settings?.llm || {};
  $("#llm-enable").checked = Boolean(llm.enabled);
  $("#llm-base").value = llm.base_url || "";
  $("#llm-model").value = llm.model || "";
  $("#llm-key").value = llm.api_key || "";
  $("#llm-model-label").textContent = llm.enabled ? (llm.model || "已启用") : "未启用";
  $("#set-llm-base").value = llm.base_url || "";
  $("#set-llm-model").value = llm.model || "";
  $("#set-llm-key").value = llm.api_key || "";
  $("#set-llm-timeout").value = llm.timeout ?? 180;
  $("#set-llm-temp").value = llm.temperature ?? 0.4;
  $("#set-llm-tokens").value = llm.max_tokens ?? 4096;
  $("#set-llm-vision").checked = llm.vision !== false;
}

export function renderSettingsForm() {
  const settings = state.settings || {};
  $("#set-comfy").value = settings.comfy_url || "";
  $("#set-ffmpeg").value = settings.ffmpeg || "ffmpeg";
  $("#set-ffprobe").value = settings.ffprobe || "ffprobe";
  const setupRoot = state.setup?.comfy?.root || "";
  const dirInput = $("#set-comfy-dir");
  if (dirInput && document.activeElement !== dirInput) dirInput.value = setupRoot;
  renderLLMForm();
}

export function renderProject() {
  const project = state.project;
  const hasProject = Boolean(project);
  document.body.classList.toggle("has-project", hasProject);
  $("#project-id-label").textContent = hasProject ? `#${project.id.slice(0, 6)}` : "未创建";
  const nameInput = $("#project-name");
  // 正在输入时不要用服务端旧值覆盖（否则打字会被清掉）
  if (nameInput && document.activeElement !== nameInput) nameInput.value = project?.name || "";
  const noteInput = $("#note-input");
  if (noteInput && document.activeElement !== noteInput) {
    noteInput.value = project?.material_note || "";
    noteInput.disabled = !hasProject;
  }
  $("#stage-name").textContent = project?.name || "从一段声音开始";
  const segments = project?.segments || [];
  const hasAudio = Number(project?.source?.samples || 0) > 0
    || Number(project?.source?.duration || 0) > 0;
  $("#stage-sub").textContent = hasProject
    ? (hasAudio
      ? `${formatDuration(project.source?.duration)} · ${segments.length} 段 · ${project.mode === "speaking" ? "口播" : "演唱"}模式`
      : `空白作品 · 拖入音频后开始分段 · ${project.mode === "speaking" ? "口播" : "演唱"}模式`)
    : "上传音频、锁定人物，剩下的交给导演编排";
  $("#source-meta").innerHTML = hasProject && hasAudio ? `
    <div>时长<b>${formatTime(project.source?.duration)}</b></div>
    <div>分段<b>${segments.length} 段</b></div>
    <div>采样率<b>${(project.source?.sample_rate || 44100) / 1000} kHz</b></div>
    <div>生成帧<b>${segments.reduce((sum, row) => sum + (row.generation_frames || 0), 0)} 帧</b></div>
  ` : "";
  if (hasProject) {
    $("#mode-select").value = project.mode || "singing";
    $("#target-range").value = project.target_seconds || 11;
    $("#max-range").value = project.max_seconds || 15;
    $("#target-label").textContent = `${Number($("#target-range").value).toFixed(1)}s`;
    $("#max-label").textContent = `${Number($("#max-range").value).toFixed(1)}s`;
    const scriptInput = $("#script-input");
    if (scriptInput && document.activeElement !== scriptInput) scriptInput.value = project.script || "";
    const asrToggle = $("#asr-toggle");
    if (document.activeElement !== asrToggle) asrToggle.checked = Boolean(project.asr?.enabled);
    $("#asr-options").classList.toggle("hidden", !asrToggle.checked);
    if (project.asr_model) $("#asr-model").value = project.asr_model;
    if (project.asr_language !== undefined) $("#asr-lang").value = project.asr_language || "";
    if (project.asr?.enabled) {
      const language = project.asr.language ? ` · ${project.asr.language}` : "";
      $("#asr-hint").textContent = `已识别 ${project.asr.note || ""}${language}`;
    }
  }
  const audio = $("#audio-el");
  if (hasProject && hasAudio) {
    const expected = `/api/projects/${project.id}/media/source.wav`;
    if (!audio.src.endsWith(expected)) {
      audio.src = expected;
    }
    $("#audio-file-label").textContent = project.source?.filename || "";
    $("#total-time").textContent = formatTime(project.source?.duration);
  } else {
    audio.removeAttribute("src");
    audio.load();
    $("#audio-file-label").textContent = "";
    $("#total-time").textContent = "00:00.0";
  }
  renderRefs();
  renderSegments();
  renderFinal();
  renderRun();
  // 全局风格 / 滤镜按钮文案
  const styleLabel = $("#style-label");
  const filterLabel = $("#filter-label");
  if (styleLabel) styleLabel.textContent = project?.style ? `风格：${project.style.slice(0,14)}` : "风格：未设置";
  if (filterLabel) filterLabel.textContent = project?.filter ? `滤镜：${project.filter.slice(0,14)}` : "滤镜：未设置";
  $("#btn-analyze").disabled = !hasProject || !hasAudio;
  $("#btn-analyze").title = !hasProject
    ? "请先新建或打开作品"
    : !hasAudio ? "请先拖入音频，再分析分段" : "重新分析音频并重建分段";
  $("#btn-split").disabled = !hasProject || !hasAudio || segments.length === 0;
  $("#btn-generate").disabled = !hasProject || segments.length === 0;
  $("#btn-regenerate-all").disabled = !hasProject || segments.length === 0;
  $("#btn-write-all").disabled = !hasProject || segments.length === 0;
  const exportButton = $("#btn-export-pack");
  if (exportButton) exportButton.disabled = !hasProject;
  $("#btn-rebuild").disabled = !hasProject || (segments.some((row) => row.status !== "completed"));
  const assembleButton = $("#btn-assemble");
  if (assembleButton) {
    assembleButton.disabled = !hasProject || segments.some((row) => row.status !== "completed");
  }
  $("#empty-stage").hidden = segments.length > 0;
}

export function renderRefs() {
  const project = state.project;
  const refs = project?.refs || [];
  $("#ref-count").textContent = `${refs.length} / 9`;
  const container = $("#ref-groups");
  if (!container) return;
  const room = refs.length < 9;
  container.innerHTML = REF_GROUPS.map((group) => {
    const members = refs
      .map((ref, index) => ({ ref, index }))
      .filter(({ ref }) => group.role === "subject"
        ? (ref.role === "subject" || ref.role === "other")
        : ref.role === group.role);
    const cards = members.map(({ ref, index }) => `
      <div class="ref-card" data-ref-index="${index}">
        <img src="/api/projects/${project.id}/media/refs/${escapeHtml(ref.filename)}" alt="${escapeHtml(ref.title || "")}">
        <button class="ref-del" data-act="del-ref" data-index="${index}" title="删除"><svg><use href="#i-close"/></svg></button>
        <div class="ref-title" title="${escapeHtml(ref.title || "")}">图${index + 1} · ${escapeHtml(ref.title || "参考图")}</div>
        <select class="ref-role-select" data-ref-role="${index}">
          ${ROLE_OPTIONS.map(([value, label]) =>
            `<option value="${value}" ${ref.role === value ? "selected" : ""}>${label}</option>`).join("")}
        </select>
      </div>
    `).join("");
    return `
      <div class="ref-group">
        <div class="ref-group-head">
          <span class="ref-dot ${group.accent}"></span>
          <strong>${group.label}</strong>
          <em>${group.note}</em>
          <span class="ref-group-count">${members.length}</span>
        </div>
        ${cards ? `<div class="ref-grid">${cards}</div>` : ""}
        ${room ? `
          <label class="ghost-add">
            <input type="file" accept="image/*" hidden data-ref-upload="${group.role}">
            <svg><use href="#i-plus"/></svg><span>${members.length ? "再加一张" : `添加${group.label}`}</span>
          </label>` : ""}
      </div>
    `;
  }).join("");
}

export function renderSegments() {
  const project = state.project;
  const segments = project?.segments || [];
  const list = $("#segment-list");
  // 以左上角当前选择为准：刚切到「混合」还没等到自动保存时，也要立刻出现每段接续控件
  const stitchMode = $("#stitch-select")?.value
    || { ...(state.settings?.generation || {}), ...(project?.generation || {}) }.stitch
    || "hard";
  const isMixed = stitchMode === "mixed";
  // seam / mixed 都是一次提交整条，没有单段 take
  const isSeam = stitchMode === "seam" || isMixed;
  if (!segments.length) {
    list.innerHTML = "";
    return;
  }
  list.innerHTML = segments.map((row, index) => {
    const status = row.status || "idle";
    const audioPill = (row.text || "").trim()
      ? `<span class="pill vocal">人声</span>`
      : row.audio_role === "instrumental"
        ? `<span class="pill instrumental">${row.audio_section === "interlude" ? "间奏空镜" : row.audio_section === "intro" ? "前奏空镜" : row.audio_section === "outro" ? "尾奏空镜" : "器乐空镜"}</span>`
        : `<span class="pill">人声待确认</span>`;
    const warning = (row.prompt_warnings || []).length
      ? `<div class="seg-warn">${escapeHtml(row.prompt_warnings.slice(0, 2).join("；"))}</div>` : "";
    const refsUsed = segmentRefIndices(project, row);
    const refOverride = Array.isArray(row.ref_ids) && row.ref_ids.length > 0;
    return `
      <article class="seg-card ${status === "running" ? "current" : ""} ${status === "failed" ? "failed" : ""}" data-index="${index}">
        <div class="seg-index">
          <div class="seg-num">${String(index + 1).padStart(2, "0")}</div>
          <div class="seg-time">${formatTime(row.start)}<br>${formatTime(row.end)}</div>
        </div>
        <div class="seg-main">
          <div class="seg-top">
            <h3>${escapeHtml(VISUAL_LABELS[row.visual_type] || "人物表演")}</h3>
            <span class="pill energy-${row.relative_energy || "medium"}">${({ low: "低能量", medium: "中能量", high: "高能量" })[row.relative_energy] || "中能量"}</span>
            ${audioPill}
            <span class="pill ${refOverride ? "ref-override" : ""}">参考图 ${refsUsed.length}${refOverride ? " · 本段" : " · 全局"}</span>
            <span class="pill status-${status}">${STATUS_LABELS[status] || status}</span>
            <span class="pill">${Number(row.duration || 0).toFixed(1)}s · ${row.edit_frames || 0}/${row.generation_frames || 0} 帧</span>
            ${isMixed ? chainTag(segments, row, index) : ""}
            ${isMixed ? stitchSelect(row, index) : ""}
          </div>
          <div class="seg-line">${escapeHtml(row.brief ? row.brief.split("\n")[0].replace(/^画面任务：/, "") : "等待导演简报")} · ${escapeHtml(row.camera_start || "")} ${escapeHtml(row.camera_start_angle || "")} → ${escapeHtml(row.camera_end || "")}</div>
          ${(row.text || "").trim() ? `<div class="seg-script" title="${escapeHtml(row.text)}">台词 / 歌词：${escapeHtml(row.text.slice(0, 60))}${row.text.length > 60 ? "…" : ""}</div>` : ""}
          <div class="seg-cam">
            <span>${escapeHtml((row.camera_move || "").slice(0, 54))}${(row.camera_move || "").length > 54 ? "…" : ""}</span>
            <span>入场：${escapeHtml(row.entry_cut_strategy || "—")}</span>
            <span>风险：${escapeHtml(row.entry_cut_risk || "—")}</span>
          </div>
          ${row.final_prompt ? `<div class="seg-prompt" data-act="open-prompt" data-index="${index}">${escapeHtml(row.final_prompt.replace(/\s+/g, " ").slice(0, 150))}…</div>` : `<div class="seg-prompt" data-act="open-prompt" data-index="${index}">尚未生成提示词 · 点击编辑</div>`}
          ${row.prompt_stale && row.final_prompt ? `<div class="seg-warn">${escapeHtml(row.prompt_stale_reason || "素材或要求已修改")}：提示词可能已过期，建议打开「提示词」→「AI 重写」刷新。</div>` : ""}
          <div class="seg-refs">
            <span class="seg-ref-label">本段用图</span>
            ${refsUsed.map((position, order) => {
              const ref = (project.refs || [])[position] || {};
              return `<span class="seg-ref-thumb" data-act="refs" data-index="${index}" title="图${position + 1} · ${escapeHtml(ROLE_LABELS[ref.role] || "参考图")} · ${escapeHtml(ref.title || "")}">
                <img src="/api/projects/${project.id}/media/refs/${escapeHtml(ref.filename)}" alt="" loading="lazy" decoding="async">
                <b>${order + 1}</b>
                <i class="${escapeHtml(ref.role || "other")}"></i>
              </span>`;
            }).join("") || `<button class="seg-ref-empty" data-act="refs" data-index="${index}">还没有参考图，点击添加</button>`}
            ${refsUsed.length < (project.refs || []).length ? `<button class="seg-ref-add" data-act="refs" data-index="${index}" title="选择本段用图"><svg><use href="#i-plus"/></svg></button>` : ""}
          </div>
          ${warning}
        </div>
        <aside class="seg-side">
          ${takeRail(project, row, index, isSeam, status)}
          <div class="seg-actions">
            <button class="btn ghost" data-act="open-prompt" data-index="${index}"><svg><use href="#i-spark"/></svg>提示词</button>
            <button class="btn ghost ${refOverride ? "active" : ""}" data-act="refs" data-index="${index}"><svg><use href="#i-layers"/></svg>参考图</button>
            ${index > 0 && project.final ? `<button class="btn ghost" data-act="seam-frame" data-index="${index}"><svg><use href="#i-layers"/></svg>接缝</button>` : ""}
            <button class="btn ghost ${state.auditionSegment === index ? "active" : ""}" data-act="audition" data-index="${index}"><svg><use href="#${state.auditionSegment === index ? "i-stop" : "i-play"}"/></svg>${state.auditionSegment === index ? "停止" : "试听"}</button>
            <button class="btn ghost" data-act="merge" data-index="${index}" ${index === segments.length - 1 ? "disabled" : ""}><svg><use href="#i-merge"/></svg>合并下段</button>
            <button class="btn ghost" data-act="regen" data-index="${index}" ${isSeam ? 'disabled title="整条生成模式不能单段重生成"' : ""}><svg><use href="#i-refresh"/></svg>重生成</button>
          </div>
        </aside>
      </article>
    `;
  }).join("");
}

function chainIndexOf(segments, index) {
  // 与后端 seam.group_chains 一致：只有本段 stitch=seam 才并入上一链
  let chain = 1;
  for (let position = 1; position <= index; position += 1) {
    if (segments[position]?.stitch !== "seam") chain += 1;
  }
  return chain;
}

function chainTag(segments, row, index) {
  const chain = chainIndexOf(segments, index);
  const head = index === 0 || row.stitch !== "seam";
  return `<span class="pill chain-tag ${head ? "chain-head" : ""}" title="同链的段一次提交、链内 latent 续接；链间硬切">链 ${chain}${head ? " 起" : ""}</span>`;
}

function stitchSelect(row, index) {
  const stitch = row.stitch === "seam" ? "seam" : "hard";
  const overlap = Number(row.overlap) === 39 ? 39 : 22;
  const value = stitch === "seam" ? `seam:${overlap}` : "hard";
  const locked = index === 0;
  const title = locked ? "第 1 段没有上一段，只能是硬切" : "本段与上一段的接续方式与重叠帧数";
  if (locked) {
    return `<span class="seg-stitch locked" title="${title}"><span class="seg-stitch-label">接续</span><b>硬切</b></span>`;
  }
  const option = (optionValue, label, cls) => `
    <button type="button" class="seg-stitch-opt ${cls} ${value === optionValue ? "active" : ""}"
      data-act="stitch-set" data-index="${index}" data-value="${optionValue}">${label}</button>`;
  return `<span class="seg-stitch" role="group" title="${title}">
    <span class="seg-stitch-label">接续</span>
    ${option("hard", "硬切", "hard")}
    ${option("seam:22", "续接 22", "seam")}
    ${option("seam:39", "续接 39", "seam")}
  </span>`;
}

function segmentSignature(row) {
  return `${Number(row.start_frame || 0)}-${Number(row.end_frame || 0)}`;
}

function visibleTakes(project, row) {
  const signature = segmentSignature(row);
  return (Array.isArray(row.takes) ? row.takes : [])
    .filter((take) => !take.segment_signature || take.segment_signature === signature);
}

function takeMedia(project, path) {
  return `/api/projects/${project.id}/media/${String(path || "").replace(/^\/+/, "")}`;
}

function takeRail(project, row, index, isSeam, status) {
  if (isSeam) {
    return `<div class="take-rail take-disabled"><span>整条生成模式，不支持单段选片</span></div>`;
  }
  const takes = visibleTakes(project, row);
  const selected = takes.find((take) => take.take_id === row.selected_take)
    || takes[takes.length - 1];
  // 播放高亮用 state.currentSegment，这里判断「正在生成」要用独立状态，
  // 否则试听时会把播放头所在段误显示成生成中
  const running = status === "running" || state.generatingSegment === index;
  const poster = selected?.thumb
    ? `<img src="${takeMedia(project, selected.thumb)}" alt="" loading="lazy" decoding="async">`
    : `<span class="take-poster-empty"><svg><use href="#i-film"/></svg></span>`;
  const current = selected ? `
    <div class="take-current ${running ? "busy" : ""}" data-act="open-take" data-index="${index}" data-take="${escapeHtml(selected.take_id)}" title="点击预览 / 切换片段">
      <div class="take-poster">
        ${poster}
        <span class="take-play"><svg><use href="#i-play"/></svg></span>
        <span class="take-label">${takes.length > 1 ? `第 ${takes.indexOf(selected) + 1} / ${takes.length} 条` : "已生成片段"}</span>
        <span class="take-selected">使用中</span>
      </div>
      ${running ? '<div class="take-busy"><span class="spinner"></span>生成中…</div>' : ""}
    </div>` : `
    <div class="take-current empty ${running ? "busy" : ""}">
      <div class="take-poster">
        <span class="take-poster-empty"><svg><use href="#i-film"/></svg></span>
        <span class="take-label">尚未生成片段</span>
      </div>
      ${running ? '<div class="take-busy"><span class="spinner"></span>生成中…</div>'
        : `<button class="btn ghost sm take-generate" data-act="regen" data-index="${index}">生成这一段</button>`}
    </div>`;
  const strip = takes.length > 1 ? `
    <div class="take-strip">
      ${takes.map((take, order) => `
        <button class="take-thumb ${take.take_id === selected?.take_id ? "active" : ""}"
          data-act="open-take" data-index="${index}" data-take="${escapeHtml(take.take_id)}"
          title="第 ${order + 1} 条 · ${Number(take.duration || 0).toFixed(1)}s · 点击预览 / 选用">
          ${take.thumb ? `<img src="${takeMedia(project, take.thumb)}" alt="" loading="lazy" decoding="async">`
            : '<span class="take-thumb-empty"><svg><use href="#i-film"/></svg></span>'}
          <b>${order + 1}</b>
          ${take.take_id === selected?.take_id ? '<i class="take-check">✓</i>' : ""}
        </button>`).join("")}
      <span class="take-count">共 ${takes.length} 条</span>
    </div>` : (takes.length === 1
      ? `<div class="take-strip single"><span class="take-count">共 1 条 · 重新生成会保留旧片段</span></div>` : "");
  return `<div class="take-rail">${current}${strip}</div>`;
}

export function renderFinal() {
  const project = state.project;
  const final = project?.final;
  const frame = $("#final-frame");
  const download = $("#btn-download");
  const stale = $("#final-stale");
  if (!project || !final?.path) {
    frame.innerHTML = `<div class="final-empty"><svg><use href="#i-film"/></svg><span>生成完成后在此预览</span></div>`;
    download.hidden = true;
    $("#final-duration").textContent = "—";
    if (stale) stale.hidden = true;
    return;
  }
  const source = `/api/projects/${project.id}/media/${final.path}?v=${Math.round(final.created || 0)}`;
  frame.innerHTML = `<video controls playsinline preload="metadata" poster="${`/api/projects/${project.id}/media/${final.thumb}?v=${Math.round(final.created || 0)}`}" src="${source}"></video>`;
  download.hidden = false;
  download.href = `/api/projects/${project.id}/download`;
  $("#final-duration").textContent = `${final.duration || 0}s · ${(final.bytes / 1024 / 1024).toFixed(1)}MB`;
  if (stale) {
    stale.hidden = !final.stale;
    const text = $("#final-stale-text");
    if (text) text.textContent = final.stale_reason || "分段选材已变更";
  }
}

export function renderRun() {
  const run = state.run || {};
  const strip = $("#progress-strip");
  const active = ["queued", "running", "failed", "cancelled", "partial"].includes(run.status);
  strip.classList.toggle("hidden", !active);
  if (!active) {
    $("#btn-cancel").classList.add("hidden");
    // 结束态要把按钮状态复位；以前这里直接 return，按钮会一直保持 disabled
    $("#btn-generate").disabled = !state.project;
    $("#btn-regenerate-all").disabled = !state.project;
    state.generatingSegment = -1;
    return;
  }
  const percent = Math.round((run.progress || 0) * 100);
  $("#run-stage").textContent = STAGE_LABELS[run.stage] || run.stage || "处理中";
  $("#run-percent").textContent = `${percent}%`;
  $("#progress-bar").style.width = `${percent}%`;
  $("#run-message").textContent = run.message || "—";
  const logs = (run.logs || []).slice(-8).reverse();
  $("#run-log").innerHTML = logs.map((log) => `
    <div class="${log.level === "error" ? "err" : ""}">[${escapeHtml(log.time)}] ${escapeHtml(log.message)}</div>
  `).join("") || "<div>暂无日志</div>";
  const running = ["queued", "running"].includes(run.status);
  $("#btn-cancel").classList.toggle("hidden", !running);
  $("#btn-generate").disabled = running;
  $("#btn-regenerate-all").disabled = running || !state.project;
  state.currentSegment = (run.status === "running" && Number.isFinite(run.segment)) ? run.segment : -1;
  state.generatingSegment = state.currentSegment;
}

export function renderHistory() {
  const grid = $("#history-grid");
  if (!grid) return;
  const projects = state.projects || [];
  if (!projects.length) {
    grid.innerHTML = `<div class="history-empty">还没有作品，点左侧「新建作品」开始吧。</div>`;
    return;
  }
  grid.innerHTML = projects.map((item) => `
    <div class="history-item" data-open-project="${escapeHtml(item.id)}">
      <button class="history-del" data-del-project="${escapeHtml(item.id)}" title="删除项目"><svg><use href="#i-trash"/></svg></button>
      <div class="history-thumb">
        ${item.thumb ? `<img src="/api/projects/${item.id}/media/${item.thumb}" alt="">` : `<svg><use href="#i-film"/></svg>`}
      </div>
      <div class="history-meta">
        <strong>${escapeHtml(item.name || "未命名")}</strong>
        <span>${formatDuration(item.duration)} · ${item.segments} 段 · ${formatDate(item.updated)}</span>
      </div>
    </div>
  `).join("");
}

export function setAudioWaveform(waveform) {
  const project = state.project;
  if (!project) {
    waveform.setData({ peaks: [], segments: [], duration: 0 });
    return;
  }
  waveform.setData({
    peaks: project.analysis?.waveform?.original || [],
    segments: project.segments || [],
    duration: project.source?.duration || 0,
    stitch: project.generation?.stitch || project.stitch || "hard",
  });
}

export function setActiveSegment(index) {
  $$(".seg-card").forEach((card) => {
    card.classList.toggle("current", Number(card.dataset.index) === index);
  });
}
