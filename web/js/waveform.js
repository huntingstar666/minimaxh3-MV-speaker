// 波形绘制：顶部时间标尺（片孔暗示 + 自适应刻度/时间码）+ 圆头柱状波形 + 分段底色与序号
//          + 播放头光晕 / hover 参考线 / 拖动切点提示。
// 静态内容（标尺、波形、段底色、切点、段号）绘制一次后缓存在离屏 canvas；
// 播放头、hover 参考线、切点高亮只重绘动态层（paint），不重画静态层。
const FALLBACK_COLORS = { amber: "#f7b955", amber2: "#ff8a4c", mint: "#57e0c0", violet: "#a78bfa" };
const RULER_H = 18;                       // 顶部时间标尺高度
const TICK_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
const MONO = '"Cascadia Mono","Cascadia Code",Consolas,"JetBrains Mono",monospace';

// 刻度的最小像素间距：低于这个值就换更大的步长，避免时间码挤在一起
const MIN_TICK_PX = 56;

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

// 解析 #rgb / #rrggbb / rgb() / rgba()；解析失败返回 null（由调用方回退到字面量）
function parseColor(value) {
  const text = String(value || "").trim();
  if (!text) return null;
  if (text.startsWith("#")) {
    let hex = text.slice(1);
    if (hex.length === 3 || hex.length === 4) {
      hex = hex.slice(0, 3).split("").map((char) => char + char).join("");
    }
    if (hex.length < 6) return null;
    const r = parseInt(hex.slice(0, 2), 16);
    const g = parseInt(hex.slice(2, 4), 16);
    const b = parseInt(hex.slice(4, 6), 16);
    return [r, g, b].every(Number.isFinite) ? { r, g, b } : null;
  }
  const match = text.match(/^rgba?\(([^)]+)\)$/i);
  if (!match) return null;
  const parts = match[1].split(/[\s,/]+/).filter(Boolean);
  const r = Number(parts[0]);
  const g = Number(parts[1]);
  const b = Number(parts[2]);
  return [r, g, b].every(Number.isFinite) ? { r, g, b } : null;
}

function toRgba(value, alpha) {
  const parsed = parseColor(value);
  if (!parsed) return toRgba(FALLBACK_COLORS.amber, alpha);
  return `rgba(${parsed.r},${parsed.g},${parsed.b},${clamp(alpha, 0, 1)})`;
}

// 时间码：默认 mm:ss.d（和界面上的 00:00.0 一致）
function formatClock(value, decimals = 1) {
  const factor = 10 ** decimals;
  const rounded = Math.round(Math.max(0, Number(value) || 0) * factor) / factor;
  const minutes = Math.floor(rounded / 60);
  const seconds = rounded - minutes * 60;
  const secText = decimals > 0
    ? seconds.toFixed(decimals).padStart(decimals + 3, "0")
    : String(Math.round(seconds)).padStart(2, "0");
  return `${String(minutes).padStart(2, "0")}:${secText}`;
}

export class Waveform {
  constructor(canvas, wrap) {
    this.canvas = canvas;
    this.wrap = wrap;
    this.ctx = canvas.getContext("2d");
    this.layer = document.createElement("canvas");
    this.layerCtx = this.layer.getContext("2d");
    this.peaks = [];
    this.segments = [];
    this.duration = 0;
    this.playhead = 0;
    this.width = 0;
    this.height = 148;
    this.onSeek = null;
    this.onSplit = null;
    this.onBoundary = null;
    this.onDeleteBoundary = null;
    this.editable = false;
    this.dragging = null;
    this.dragTime = null;
    this.hoverX = null;
    this.hoverBoundary = null;
    this.colors = null;
    this._palettes = null;
    this.tip = null;
    this._paintRaf = 0;
    this._dpr = 1;
    // 播放头拖动（scrub）：按下先定位，移动超过阈值后开始试听，松手恢复原播放状态
    this.scrubbing = false;
    this.scrubTime = null;
    this._downX = null;
    this._scrubMoved = false;
    this._hoverPlayhead = false;
    this.onScrub = null;
    this.onScrubStart = null;
    this.onScrubEnd = null;
    this.onTogglePlay = null;
    this._tipTimer = 0;

    // 画布内联高度必须和 this.height 同步（index.html 上的 height 属性只作为初始值）
    this.canvas.style.height = `${this.height}px`;
    // 气泡是 wrap 内的绝对定位元素，wrap 必须是定位上下文（CSS 已给 relative 时不动）
    try {
      if (getComputedStyle(this.wrap).position === "static") this.wrap.style.position = "relative";
    } catch { /* 忽略：没有样式环境时气泡退化为普通块 */ }
    try { this.canvas.setAttribute("aria-label", "音频波形时间线：拖动播放头试听，左右方向键微调"); } catch { /* 忽略 */ }
    try { this.canvas.tabIndex = 0; } catch { /* 忽略 */ }

    this.resizeObserver = new ResizeObserver(() => {
      const width = Math.round(this.wrap.clientWidth || 0);
      if (width && width !== this.width) this.draw();
    });
    this.resizeObserver.observe(wrap);

    canvas.addEventListener("pointerdown", (event) => this._onPointerDown(event));
    canvas.addEventListener("pointermove", (event) => this._onPointerMove(event));
    canvas.addEventListener("pointerup", (event) => this._onPointerUp(event));
    canvas.addEventListener("pointercancel", () => this._cancelDrag());
    canvas.addEventListener("pointerleave", () => this._onPointerLeave());
    canvas.addEventListener("dblclick", (event) => this._onDoubleClick(event));
    canvas.addEventListener("contextmenu", (event) => this._onContextMenu(event));
    canvas.addEventListener("keydown", (event) => this._onKeyDown(event));
  }

  // ------------------------------- 公开 API -------------------------------
  setData({ peaks, segments, duration }) {
    this.peaks = Array.isArray(peaks) ? peaks : [];
    this.segments = Array.isArray(segments) ? segments : [];
    this.duration = Number(duration) || 0;
    this.wrap.classList.toggle("loaded", this.peaks.length > 0);
    if (!this.duration) {
      this.hoverX = null;
      this.hoverBoundary = null;
      this.hideTip();
    }
    this.draw();
  }

  setPlayhead(seconds) {
    this.playhead = Number(seconds) || 0;
    this.paint();
  }

  resize() {
    const width = Math.max(1, Math.round(this.wrap.clientWidth || this.canvas.clientWidth || 800));
    const height = this.height;
    const dpr = Math.max(1, Math.min(3, window.devicePixelRatio || 1));
    const pixelWidth = Math.max(1, Math.round(width * dpr));
    const pixelHeight = Math.max(1, Math.round(height * dpr));
    this._dpr = dpr;

    if (this.canvas.width !== pixelWidth || this.canvas.height !== pixelHeight) {
      this.canvas.width = pixelWidth;
      this.canvas.height = pixelHeight;
    }
    this.canvas.style.height = `${height}px`;
    this.ctx = this.canvas.getContext("2d");
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    if (this.layer.width !== pixelWidth || this.layer.height !== pixelHeight) {
      this.layer.width = pixelWidth;
      this.layer.height = pixelHeight;
    }
    this.layerCtx = this.layer.getContext("2d");
    this.layerCtx.setTransform(dpr, 0, 0, dpr, 0, 0);

    this.width = width;
    return { width, height };
  }

  draw() {
    const { width, height } = this.resize();
    this.colors = this.readColors();
    this._palettes = {
      vocal: this.paletteFor("vocal"),
      instrumental: this.paletteFor("instrumental"),
    };
    const ctx = this.layerCtx;
    const bodyTop = RULER_H;
    const bodyBottom = height;
    const mid = bodyTop + (bodyBottom - bodyTop) / 2;
    const duration = this.duration;
    const timeToX = (time) => (duration ? (Number(time) || 0) / duration * width : 0);

    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "rgba(255,255,255,.014)";
    ctx.fillRect(0, 0, width, height);

    // 段底色：低 alpha，只用来区分人声 / 器乐
    if (duration) {
      this.segments.forEach((segment, index) => {
        const x1 = clamp(timeToX(segment.start), 0, width);
        const x2 = clamp(timeToX(segment.end), 0, width);
        if (x2 - x1 < 0.6) return;
        const tint = this.palette(index);
        ctx.fillStyle = index % 2 ? tint.tintB : tint.tintA;
        ctx.fillRect(x1, bodyTop, x2 - x1, bodyBottom - bodyTop);
      });
    }

    // 中轴线
    ctx.strokeStyle = "rgba(255,255,255,.055)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, Math.round(mid) + 0.5);
    ctx.lineTo(width, Math.round(mid) + 0.5);
    ctx.stroke();

    if (this.peaks.length && duration) this.drawBars(ctx, width, mid, bodyTop, bodyBottom);
    if (duration) this.drawBoundaries(ctx, width, timeToX, bodyTop, bodyBottom);
    if (this.peaks.length && duration) this.drawIndexLabels(ctx, width, timeToX);
    if (duration) this.drawRuler(ctx, width);

    this.paint();
  }

  paint() {
    if (!this.width || !this.ctx) return;
    const ctx = this.ctx;
    const width = this.width;
    const height = this.height;
    const bodyTop = RULER_H;
    const colors = this.colors || FALLBACK_COLORS;

    ctx.clearRect(0, 0, width, height);
    // 静态层离屏缓存后整块贴回，比逐柱重绘快得多
    ctx.drawImage(this.layer, 0, 0, width, height);
    if (!this.duration) return;

    const timeToX = (time) => (clamp(Number(time) || 0, 0, this.duration) / this.duration) * width;

    // hover 竖向参考线（贴着切点时让位给切点高亮）
    if (this.dragging === null && this.hoverX !== null && this.hoverBoundary === null) {
      const hx = Math.round(this.hoverX) + 0.5;
      if (hx >= 0 && hx <= width) {
        ctx.save();
        ctx.setLineDash([3, 4]);
        ctx.strokeStyle = "rgba(255,255,255,.20)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(hx, bodyTop);
        ctx.lineTo(hx, height);
        ctx.stroke();
        ctx.restore();
        ctx.fillStyle = "rgba(255,255,255,.45)";
        ctx.beginPath();
        ctx.moveTo(hx - 3.5, RULER_H);
        ctx.lineTo(hx + 3.5, RULER_H);
        ctx.lineTo(hx, RULER_H - 4);
        ctx.closePath();
        ctx.fill();
      }
    }

    // hover 到切点：琥珀色高亮 + 顶部拉手
    if (this.dragging === null && this.hoverBoundary !== null) {
      const segment = this.segments[this.hoverBoundary];
      if (segment) {
        const x = Math.round(timeToX(segment.end)) + 0.5;
        ctx.save();
        ctx.shadowColor = toRgba(colors.amber, .7);
        ctx.shadowBlur = 7;
        ctx.strokeStyle = toRgba(colors.amber, .9);
        ctx.lineWidth = 1.8;
        ctx.beginPath();
        ctx.moveTo(x, bodyTop);
        ctx.lineTo(x, height);
        ctx.stroke();
        ctx.restore();
        ctx.fillStyle = toRgba(colors.amber, .92);
        this.roundRect(ctx, x - 2, bodyTop - 1, 4, 9, 2);
        ctx.fill();
      }
    }

    // 播放头：抓手表头 + 竖线 + 光晕；hover / 拖动时放大提亮
    const px = clamp(timeToX(this.playhead), 0, width);
    if (this.duration > 0) {
      const active = this.scrubbing || this._hoverPlayhead;
      const glow = ctx.createLinearGradient(px - 16, 0, px + 16, 0);
      glow.addColorStop(0, toRgba(colors.amber, 0));
      glow.addColorStop(0.5, toRgba(colors.amber, active ? .16 : .10));
      glow.addColorStop(1, toRgba(colors.amber, 0));
      ctx.fillStyle = glow;
      ctx.fillRect(px - 16, bodyTop, 32, height - bodyTop);

      ctx.save();
      ctx.shadowColor = toRgba(colors.amber, .85);
      ctx.shadowBlur = active ? 12 : 8;
      ctx.strokeStyle = toRgba(colors.amber, .95);
      ctx.lineWidth = active ? 2.2 : 1.8;
      ctx.beginPath();
      ctx.moveTo(px, 13);
      ctx.lineTo(px, height);
      ctx.stroke();
      ctx.restore();

      // 抓手表头：圆角旗 + 向下小三角，明确「这里可以拖」
      const headW = active ? 13 : 11;
      const headH = active ? 13 : 11;
      const headTop = 2;
      ctx.save();
      ctx.shadowColor = toRgba(colors.amber, .8);
      ctx.shadowBlur = active ? 12 : 7;
      ctx.fillStyle = toRgba(colors.amber, 1);
      this.roundRect(ctx, px - headW / 2, headTop, headW, headH, 3);
      ctx.fill();
      ctx.beginPath();
      ctx.moveTo(px - 3.4, headTop + headH - 0.5);
      ctx.lineTo(px + 3.4, headTop + headH - 0.5);
      ctx.lineTo(px, headTop + headH + 4);
      ctx.closePath();
      ctx.fill();
      ctx.restore();

      // 表头内两道防滑细纹
      ctx.strokeStyle = "rgba(7,10,14,.9)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(px - 1.7, headTop + 3.6);
      ctx.lineTo(px - 1.7, headTop + headH - 3);
      ctx.moveTo(px + 1.7, headTop + 3.6);
      ctx.lineTo(px + 1.7, headTop + headH - 3);
      ctx.stroke();
    }

    // 拖动切点：紫色虚线 + 顶部拉手（时间由 DOM 气泡显示）
    if (this.dragging !== null && this.dragTime !== null) {
      const dx = clamp((this.dragTime / this.duration) * width, 0, width);
      ctx.save();
      ctx.shadowColor = toRgba(colors.violet, .85);
      ctx.shadowBlur = 9;
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = toRgba(colors.violet, .95);
      ctx.lineWidth = 1.8;
      ctx.beginPath();
      ctx.moveTo(dx, bodyTop - 1);
      ctx.lineTo(dx, height);
      ctx.stroke();
      ctx.restore();
      ctx.fillStyle = toRgba(colors.violet, .95);
      this.roundRect(ctx, dx - 2.5, bodyTop - 2, 5, 10, 2.5);
      ctx.fill();
    }
  }

  boundaryAt(x) {
    if (!this.duration || !this.segments.length) return null;
    for (let index = 0; index < this.segments.length - 1; index += 1) {
      const position = (Number(this.segments[index].end) / this.duration) * this.width;
      if (Math.abs(x - position) <= 7) return index;
    }
    return null;
  }

  segmentAt(time) {
    for (let i = 0; i < this.segments.length; i += 1) {
      if (time >= this.segments[i].start && time < this.segments[i].end) return i;
    }
    return this.segments.length ? this.segments.length - 1 : 0;
  }

  // 时间 → 画布 x（供气泡 / 命中判断复用，避免各handler重复算比例）
  xOf(time) {
    if (!this.duration) return 0;
    return (clamp(Number(time) || 0, 0, this.duration) / this.duration) * this.width;
  }

  // ------------------------------- 静态层细节 -------------------------------
  roundRect(ctx, x, y, width, height, radius) {
    const safeRadius = Math.max(0, Math.min(radius, width / 2, height / 2));
    if (typeof ctx.roundRect === "function") {
      ctx.roundRect(x, y, Math.max(0.1, width), Math.max(0.1, height), safeRadius);
    } else {
      ctx.rect(x, y, Math.max(0.1, width), Math.max(0.1, height));
    }
  }

  readColors() {
    let styles = null;
    try { styles = getComputedStyle(this.canvas); } catch { styles = null; }
    const read = (name, fallback) => {
      try {
        const value = styles?.getPropertyValue(name)?.trim();
        if (value && parseColor(value)) return value;
      } catch { /* 忽略 */ }
      return fallback;
    };
    return {
      amber: read("--amber", FALLBACK_COLORS.amber),
      amber2: read("--amber-2", FALLBACK_COLORS.amber2),
      mint: read("--mint", FALLBACK_COLORS.mint),
      violet: read("--violet", FALLBACK_COLORS.violet),
    };
  }

  paletteFor(kind) {
    const colors = this.colors || FALLBACK_COLORS;
    if (kind === "instrumental") {
      return {
        kind,
        tip: toRgba(colors.mint, .90),
        core: toRgba(colors.mint, .26),
        tintA: toRgba(colors.mint, .028),
        tintB: toRgba(colors.mint, .046),
      };
    }
    return {
      kind: "vocal",
      tip: toRgba(colors.amber, .95),
      core: toRgba(colors.amber2, .26),
      tintA: toRgba(colors.amber, .026),
      tintB: toRgba(colors.amber2, .044),
    };
  }

  palette(index) {
    const segment = this.segments[index];
    const instrumental = segment?.audio_role === "instrumental" && !(segment?.text || "").trim();
    const kind = instrumental ? "instrumental" : "vocal";
    return this._palettes?.[kind] || this.paletteFor(kind);
  }

  // 峰值柱：按像素预算分桶取 max，4000+ 个峰值仍是 O(n)
  drawBars(ctx, width, mid, bodyTop, bodyBottom) {
    const peaks = this.peaks;
    const count = peaks.length;
    if (!count || !this.duration) return;
    const maxBars = Math.max(1, Math.floor(width / 2.2));
    const bucket = Math.max(1, Math.ceil(count / maxBars));
    const barCount = Math.ceil(count / bucket);
    const stepX = width / barCount;
    const barWidth = Math.max(1, Math.min(stepX * 0.58, 3.4));
    const bodyHalf = Math.max(6, (bodyBottom - bodyTop) / 2 - 6);

    const groups = { vocal: [], instrumental: [] };
    for (let b = 0; b < barCount; b += 1) {
      const from = b * bucket;
      const to = Math.min(count, from + bucket);
      let value = 0;
      for (let i = from; i < to; i += 1) {
        const peak = Number(peaks[i]);
        if (Number.isFinite(peak) && peak > value) value = peak;
      }
      value = clamp(value, 0, 1);
      const half = Math.max(1.3, value * bodyHalf);
      const x = b * stepX + (stepX - barWidth) / 2;
      const time = ((b + 0.5) / barCount) * this.duration;
      const kind = this.palette(this.segmentAt(time)).kind;
      (groups[kind] || groups.vocal).push({ x, half });
    }

    for (const kind of Object.keys(groups)) {
      const bars = groups[kind];
      if (!bars.length) continue;
      const palette = this._palettes?.[kind] || this.paletteFor(kind);
      const gradient = ctx.createLinearGradient(0, bodyTop, 0, bodyBottom);
      gradient.addColorStop(0, palette.tip);
      gradient.addColorStop(0.5, palette.core);
      gradient.addColorStop(1, palette.tip);
      ctx.fillStyle = gradient;
      ctx.beginPath();
      for (const bar of bars) {
        const radius = Math.max(0.6, Math.min(barWidth / 2, bar.half, 2));
        this.roundRect(ctx, bar.x, mid - bar.half, barWidth, bar.half * 2, radius);
      }
      ctx.fill();
    }
  }

  drawBoundaries(ctx, width, timeToX, bodyTop, bodyBottom) {
    ctx.save();
    ctx.lineWidth = 1;
    this.segments.forEach((segment, index) => {
      if (index === 0) return; // 0s 处不画分割线
      const x = Math.round(timeToX(segment.start)) + 0.5;
      if (x < 0 || x > width) return;
      ctx.strokeStyle = "rgba(255,255,255,.22)";
      ctx.beginPath();
      ctx.moveTo(x, bodyTop);
      ctx.lineTo(x, bodyBottom);
      ctx.stroke();
      ctx.fillStyle = "rgba(255,255,255,.5)";
      this.roundRect(ctx, x - 1.5, bodyTop, 3, 7, 1.5);
      ctx.fill();
    });
    ctx.restore();
  }

  drawIndexLabels(ctx, width, timeToX) {
    ctx.save();
    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";
    ctx.font = `600 10px ${MONO}`;
    const amber = toRgba((this.colors || FALLBACK_COLORS).amber, 1);
    this.segments.forEach((segment, index) => {
      const startX = timeToX(segment.start);
      const endX = timeToX(segment.end);
      if (endX - startX < 26) return;
      const label = String(index + 1).padStart(2, "0");
      const textWidth = ctx.measureText(label).width;
      const x = Math.max(1, startX) + 7;
      if (x + textWidth + 9 > width) return;
      // 段号药丸放在标尺下方；深底 + 极细琥珀描边，保证压在波形上也读得清
      const y = RULER_H + 1.5;
      ctx.beginPath();
      this.roundRect(ctx, x - 4.5, y, textWidth + 9, 14, 3.5);
      ctx.fillStyle = "rgba(5,8,12,.86)";
      ctx.fill();
      ctx.strokeStyle = "rgba(247,185,85,.22)";
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.fillStyle = amber;
      ctx.fillText(label, x, y + 10.5);
    });
    ctx.restore();
  }

  pickTickStep(perSecond) {
    if (!Number.isFinite(perSecond) || perSecond <= 0) return TICK_STEPS[0];
    for (const step of TICK_STEPS) {
      if (step * perSecond >= MIN_TICK_PX) return step;
    }
    return TICK_STEPS[TICK_STEPS.length - 1];
  }

  drawRuler(ctx, width) {
    const duration = this.duration;
    if (!duration) return;
    // 标尺底
    ctx.fillStyle = "rgba(255,255,255,.028)";
    ctx.fillRect(0, 0, width, RULER_H);
    // 胶片片孔：顶部一排暗色小圆角槽（用短槽暗示，不喧宾夺主）
    ctx.fillStyle = "rgba(4,7,11,.72)";
    ctx.beginPath();
    for (let x = 5; x < width - 2; x += 12) this.roundRect(ctx, x, 1.8, 5, 2.4, 1.2);
    ctx.fill();
    // 底部发丝线
    ctx.strokeStyle = "rgba(255,255,255,.075)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, RULER_H - 0.5);
    ctx.lineTo(width, RULER_H - 0.5);
    ctx.stroke();

    const perSecond = width / duration;
    const step = this.pickTickStep(perSecond);
    const majorSpacing = step * perSecond;
    const minorStep = step / 2;
    // 只有主刻度间距足够时才画次刻度，避免和 mm:ss 文字叠在一起
    const showMinor = majorSpacing >= 90;
    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";
    ctx.font = `9.5px ${MONO}`;

    for (let time = 0; time <= duration + 1e-6; time += step) {
      const rawX = time * perSecond;
      if (rawX > width - 0.5) break;
      const x = Math.round(rawX) + 0.5;
      // 主刻度：从标尺底往上 7.5px
      ctx.strokeStyle = "rgba(255,255,255,.28)";
      ctx.beginPath();
      ctx.moveTo(x, RULER_H);
      ctx.lineTo(x, RULER_H - 7.5);
      ctx.stroke();
      // 次刻度：只占底部 3px，落在时间码基线之下
      if (showMinor && time + minorStep <= duration) {
        const minorX = Math.round((time + minorStep) * perSecond) + 0.5;
        ctx.strokeStyle = "rgba(255,255,255,.13)";
        ctx.beginPath();
        ctx.moveTo(minorX, RULER_H);
        ctx.lineTo(minorX, RULER_H - 3);
        ctx.stroke();
      }
      // 时间码（等宽小字；右侧放不下就只留刻度）
      const label = formatClock(time, 0);
      const labelWidth = ctx.measureText(label).width;
      if (rawX + 5 + labelWidth <= width - 2) {
        ctx.fillStyle = "rgba(233,238,246,.52)";
        ctx.fillText(label, rawX + 4, RULER_H - 3);
      }
    }
  }

  // ------------------------------- 动态层调度 / 气泡 -------------------------------
  schedulePaint() {
    if (!this.width) return;
    if (this._paintRaf) return;
    if (typeof requestAnimationFrame !== "function") {
      this.paint();
      return;
    }
    this._paintRaf = requestAnimationFrame(() => {
      this._paintRaf = 0;
      this.paint();
    });
  }

  ensureTip() {
    if (this.tip && this.tip.isConnected) return this.tip;
    const el = document.createElement("div");
    el.className = "wave-time-tip";
    el.setAttribute("aria-hidden", "true");
    el.style.cssText = [
      "position:absolute", "left:0", "top:1px", "z-index:6",
      "transform:translateX(-50%)",
      "padding:2px 6px", "border-radius:6px",
      `font-family:${MONO}`, "font-size:9.5px", "font-weight:600",
      "line-height:1.5", "letter-spacing:.02em", "white-space:nowrap",
      "pointer-events:none", "user-select:none",
      "opacity:0", "transition:opacity .12s ease",
    ].join(";");
    this.wrap.appendChild(el);
    this.tip = el;
    return el;
  }

  showTip(x, label, tone) {
    try {
      const el = this.ensureTip();
      el.textContent = label;
      // 播放头气泡贴表头下方，避免盖住抓手；切点 / hover 气泡贴顶
      el.style.top = tone === "playhead" ? `${RULER_H + 2}px` : "1px";
      if (tone === "drag") {
        el.style.background = "rgba(18,12,30,.94)";
        el.style.border = "1px solid rgba(167,139,250,.5)";
        el.style.color = "#ddd0ff";
        el.style.boxShadow = "0 6px 18px rgba(0,0,0,.5)";
      } else if (tone === "playhead") {
        el.style.background = "rgba(247,185,85,.96)";
        el.style.border = "1px solid rgba(255,214,150,.72)";
        el.style.color = "#241803";
        el.style.boxShadow = "0 8px 20px rgba(0,0,0,.3)";
      } else {
        el.style.background = "rgba(8,11,16,.92)";
        el.style.border = "1px solid rgba(247,185,85,.34)";
        el.style.color = "#ffd9a8";
        el.style.boxShadow = "0 6px 18px rgba(0,0,0,.45)";
      }
      const anchor = clamp(x, 0, this.width);
      el.style.left = `${anchor}px`;
      const tipWidth = el.offsetWidth || 44;
      const left = clamp(anchor, tipWidth / 2 + 2, Math.max(tipWidth / 2 + 2, this.width - tipWidth / 2 - 2));
      el.style.left = `${left}px`;
      el.style.opacity = "1";
    } catch { /* 气泡失败不影响波形本身 */ }
  }

  hideTip() {
    clearTimeout(this._tipTimer);
    this._tipTimer = 0;
    if (!this.tip) return;
    // pointerleave / 拖动结束即把气泡从 DOM 移除，不留隐藏节点
    try { this.tip.remove(); } catch { try { this.tip.style.opacity = "0"; } catch { /* 忽略 */ } }
    this.tip = null;
  }

  // ------------------------------- 指针交互（语义保持不变） -------------------------------
  _localX(event) {
    const rect = this.canvas.getBoundingClientRect();
    const width = Math.max(1, rect.width);
    return { rect, width, x: event.clientX - rect.left, ratio: clamp((event.clientX - rect.left) / width, 0, 1) };
  }

  // 播放头命中带：±8px 内视为抓到了播放头，光标变 ew-resize
  _nearPlayhead(x) {
    return this.duration > 0 && Math.abs(x - this.xOf(this.playhead)) <= 8;
  }

  _onPointerDown(event) {
    if (!this.duration || event.button !== 0) return; // 只响应左键：右键走 contextmenu 删除
    const { x, ratio } = this._localX(event);
    const time = ratio * this.duration;
    const hit = this.editable ? this.boundaryAt(x) : null;
    if (hit !== null) {
      this.dragging = hit;
      this.dragTime = Number(this.segments[hit]?.end) || 0;
      this.hoverBoundary = hit;
      try { this.canvas.setPointerCapture(event.pointerId); } catch { /* 合成事件没有指针捕获 */ }
      this.canvas.style.cursor = "col-resize";
      this.showTip(x, formatClock(this.dragTime, 2), "drag");
      this.paint();
      return;
    }
    // 播放头拖动：按下先定位（保持原“点击定位”语义），移动超过 3px 才开始试听
    this.scrubbing = true;
    this.scrubTime = time;
    this._downX = x;
    this._scrubMoved = false;
    this._hoverPlayhead = true;
    this.hoverX = null;
    this.hoverBoundary = null;
    try { this.canvas.setPointerCapture(event.pointerId); } catch { /* 忽略 */ }
    try { this.canvas.focus({ preventScroll: true }); } catch { /* 忽略 */ }
    this.canvas.style.cursor = "ew-resize";
    if (this.onSeek) this.onSeek(time);
    this.showTip(this.xOf(time), formatClock(time, 2), "playhead");
    this.paint();
  }

  _onPointerMove(event) {
    const { x, width, ratio } = this._localX(event);
    if (this.scrubbing) {
      this.scrubTime = ratio * this.duration;
      this.playhead = this.scrubTime;
      if (!this._scrubMoved && Math.abs(x - this._downX) >= 3) {
        this._scrubMoved = true;
        if (this.onScrubStart) this.onScrubStart(this.scrubTime);
      }
      if (this._scrubMoved) {
        if (this.onScrub) this.onScrub(this.scrubTime);
        else if (this.onSeek) this.onSeek(this.scrubTime);
      }
      this.showTip(this.xOf(this.scrubTime), formatClock(this.scrubTime, 1), "playhead");
      this.schedulePaint();
      return;
    }
    if (this.dragging !== null) {
      this.dragTime = ratio * this.duration;
      this.showTip(x, formatClock(this.dragTime, 2), "drag");
      this.schedulePaint();
      return;
    }
    if (!this.duration) return;
    const isMouse = !event.pointerType || event.pointerType === "mouse" || event.pointerType === "pen";
    const hit = this.editable ? this.boundaryAt(x) : null;
    const near = hit === null && this._nearPlayhead(x);
    const hoverChanged = isMouse && Math.abs((this.hoverX ?? -9999) - x) > 0.5;
    const boundaryChanged = hit !== this.hoverBoundary;
    const nearChanged = near !== this._hoverPlayhead;
    this.hoverX = isMouse ? clamp(x, 0, width) : null;
    this.hoverBoundary = hit;
    this._hoverPlayhead = near;
    this.canvas.style.cursor = hit !== null ? "col-resize" : near ? "ew-resize" : "crosshair";
    if (isMouse && width > 0) {
      if (hit !== null) this.showTip(x, formatClock(Number(this.segments[hit]?.end) || 0, 1), "hover");
      else if (near) this.showTip(this.xOf(this.playhead), formatClock(this.playhead, 1), "playhead");
      else this.showTip(x, formatClock(ratio * this.duration, 1), "hover");
    }
    if (hoverChanged || boundaryChanged || nearChanged) this.schedulePaint();
  }

  _onPointerUp(event) {
    if (this.scrubbing) {
      const moved = this._scrubMoved;
      const time = this.scrubTime;
      this.scrubbing = false;
      this.scrubTime = null;
      this._downX = null;
      this._scrubMoved = false;
      this._hoverPlayhead = false;
      try { this.canvas.releasePointerCapture?.(event.pointerId); } catch { /* 忽略 */ }
      this.hideTip();
      this.canvas.style.cursor = "crosshair";
      if (moved && this.onScrubEnd) this.onScrubEnd(time);
      this.paint();
      return;
    }
    if (this.dragging === null) return;
    const index = this.dragging;
    this.dragging = null;
    const time = this.dragTime;
    this.dragTime = null;
    this.hoverBoundary = null;
    this.hideTip();
    this.paint();
    if (this.onBoundary && time !== null) {
      const cuts = this.segments.slice(0, -1).map((row) => Number(row.end));
      cuts[index] = Math.min(Number(this.segments[index + 1]?.end || this.duration) - 0.4,
                             Math.max(Number(this.segments[index].start) + 0.4, time));
      this.onBoundary(cuts);
    }
  }

  _cancelDrag() {
    if (this.scrubbing) {
      const moved = this._scrubMoved;
      this.scrubbing = false;
      this.scrubTime = null;
      this._downX = null;
      this._scrubMoved = false;
      this._hoverPlayhead = false;
      if (moved && this.onScrubEnd) this.onScrubEnd(null);
      this.hideTip();
      this.paint();
    }
    if (this.dragging === null) return;
    this.dragging = null;
    this.dragTime = null;
    this.hoverBoundary = null;
    this.hideTip();
    this.paint();
  }

  _onPointerLeave() {
    if (this.dragging !== null || this.scrubbing) return;
    this.hoverX = null;
    this.hoverBoundary = null;
    this._hoverPlayhead = false;
    this.hideTip();
    this.paint();
  }

  // 键盘微调：←/→ 0.1s（Shift 1s），Home/End 到首尾，Space 播放/暂停
  _onKeyDown(event) {
    if (event.code === "Space" && this.onTogglePlay) {
      event.preventDefault();
      this.onTogglePlay();
      return;
    }
    if (!this.duration) return;
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const step = event.shiftKey ? 1 : 0.1;
    let next = Number(this.playhead) || 0;
    if (event.key === "ArrowLeft") next -= step;
    else if (event.key === "ArrowRight") next += step;
    else if (event.key === "Home") next = 0;
    else next = this.duration;
    next = clamp(next, 0, this.duration);
    this.playhead = next;
    this._hoverPlayhead = true;
    if (this.onScrub) this.onScrub(next);
    else if (this.onSeek) this.onSeek(next);
    this.showTip(this.xOf(next), formatClock(next, 1), "playhead");
    clearTimeout(this._tipTimer);
    this._tipTimer = setTimeout(() => this.hideTip(), 900);
    this.paint();
  }

  _onDoubleClick(event) {
    if (!this.editable || !this.onSplit || !this.duration) return;
    const { ratio } = this._localX(event);
    this.hideTip();
    this.onSplit(ratio * this.duration);
  }

  _onContextMenu(event) {
    if (!this.editable || !this.onDeleteBoundary || !this.duration) return;
    const { x } = this._localX(event);
    const hit = this.boundaryAt(x);
    if (hit === null) return;
    event.preventDefault();
    this.hideTip();
    this.onDeleteBoundary(hit);
  }
}
