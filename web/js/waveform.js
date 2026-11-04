// 波形绘制 + 分段叠加 + 播放头（静态层离屏缓存，播放头单独重绘）
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
    this.height = 132;
    this.onSeek = null;
    this.onSplit = null;
    this.onBoundary = null;
    this.onDeleteBoundary = null;
    this.editable = false;
    this.dragging = null;
    this.dragTime = null;
    this.resizeObserver = new ResizeObserver(() => this.draw());
    this.resizeObserver.observe(wrap);
    canvas.addEventListener("pointerdown", (event) => {
      if (!this.duration || event.button !== 0) return;  // 只响应左键：右键走 contextmenu 删除
      const rect = canvas.getBoundingClientRect();
      const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
      const time = ratio * this.duration;
      const hit = this.editable ? this.boundaryAt(ratio * this.width) : null;
      if (hit !== null) {
        this.dragging = hit;
        this.dragTime = this.segments[hit].end;
        try { canvas.setPointerCapture(event.pointerId); } catch { /* 合成事件没有指针捕获 */ }
        canvas.style.cursor = "col-resize";
        return;
      }
      if (this.onSeek) this.onSeek(time);
    });
    canvas.addEventListener("pointermove", (event) => {
      const rect = canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      if (this.dragging !== null) {
        const ratio = Math.min(1, Math.max(0, x / rect.width));
        this.dragTime = ratio * this.duration;
        this.paint();
        return;
      }
      if (!this.editable) return;
      canvas.style.cursor = this.boundaryAt(x) !== null ? "col-resize" : "pointer";
    });
    canvas.addEventListener("pointerup", (event) => {
      if (this.dragging === null) return;
      const index = this.dragging;
      this.dragging = null;
      const time = this.dragTime;
      this.dragTime = null;
      this.paint();
      if (this.onBoundary && time !== null) {
        const cuts = this.segments.slice(0, -1).map((row) => Number(row.end));
        cuts[index] = Math.min(Number(this.segments[index + 1]?.end || this.duration) - 0.4,
                               Math.max(Number(this.segments[index].start) + 0.4, time));
        this.onBoundary(cuts);
      }
    });
    canvas.addEventListener("dblclick", (event) => {
      if (!this.editable || !this.onSplit || !this.duration) return;
      const rect = canvas.getBoundingClientRect();
      const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
      this.onSplit(ratio * this.duration);
    });
    canvas.addEventListener("contextmenu", (event) => {
      if (!this.editable || !this.onDeleteBoundary || !this.duration) return;
      const rect = canvas.getBoundingClientRect();
      const hit = this.boundaryAt(event.clientX - rect.left);
      if (hit === null) return;
      event.preventDefault();
      this.onDeleteBoundary(hit);
    });
  }

  setData({ peaks, segments, duration }) {
    this.peaks = Array.isArray(peaks) ? peaks : [];
    this.segments = Array.isArray(segments) ? segments : [];
    this.duration = Number(duration) || 0;
    this.wrap.classList.toggle("loaded", this.peaks.length > 0);
    this.draw();
  }

  setPlayhead(seconds) {
    this.playhead = Number(seconds) || 0;
    this.paint();
  }

  resize() {
    const width = this.wrap.clientWidth || 800;
    const height = this.height;
    const dpr = window.devicePixelRatio || 1;
    for (const target of [this.canvas, this.layer]) {
      target.width = Math.round(width * dpr);
      target.height = Math.round(height * dpr);
      target.style.height = `${height}px`;
      target.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    this.ctx = this.canvas.getContext("2d");
    this.layerCtx = this.layer.getContext("2d");
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.layerCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.width = width;
    return { width, height };
  }

  palette(index) {
    const segment = this.segments[index];
    const instrumental = segment?.audio_role === "instrumental" && !(segment?.text || "").trim();
    if (instrumental) {
      return { top: "rgba(94,234,212,.55)", bottom: "rgba(94,234,212,.18)",
               fillA: "rgba(94,234,212,.040)", fillB: "rgba(94,234,212,.062)" };
    }
    return { top: "rgba(255,180,84,.62)", bottom: "rgba(255,122,77,.20)",
             fillA: "rgba(255,180,84,.036)", fillB: "rgba(255,180,84,.058)" };
  }

  draw() {
    const { width, height } = this.resize();
    const ctx = this.layerCtx;
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "rgba(255,255,255,.015)";
    ctx.fillRect(0, 0, width, height);

    if (this.peaks.length && this.duration) {
      const mid = height / 2;
      const timeToX = (time) => (Number(time) / this.duration) * width;

      this.segments.forEach((segment, index) => {
        const x1 = timeToX(segment.start);
        const x2 = timeToX(segment.end);
        const colors = this.palette(index);
        ctx.fillStyle = index % 2 ? colors.fillB : colors.fillA;
        ctx.fillRect(x1, 0, x2 - x1, height);
      });

      ctx.strokeStyle = "rgba(255,255,255,.06)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, mid);
      ctx.lineTo(width, mid);
      ctx.stroke();

      const count = this.peaks.length;
      const step = width / count;
      const barWidth = Math.max(1, step * 0.55);
      const paints = [];
      for (let i = 0; i < count; i += 1) {
        const value = Math.min(1, this.peaks[i] || 0);
        const colors = this.palette(this.segmentAt((i / count) * this.duration));
        paints.push({ x: i * step, half: Math.max(1.5, value * (height * 0.42)),
                      top: colors.top, bottom: colors.bottom });
      }
      // 按颜色分组，减少渐变对象数量
      for (const paint of paints) {
        const gradient = ctx.createLinearGradient(0, mid - paint.half, 0, mid + paint.half);
        gradient.addColorStop(0, paint.top);
        gradient.addColorStop(1, paint.bottom);
        ctx.fillStyle = gradient;
        ctx.fillRect(paint.x, mid - paint.half, barWidth, paint.half * 2);
      }

      ctx.font = "10px 'JetBrains Mono', Consolas, monospace";
      this.segments.forEach((segment, index) => {
        const x = timeToX(segment.start);
        if (x > 2) {
          ctx.strokeStyle = "rgba(255,255,255,.34)";
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(x, 0);
          ctx.lineTo(x, height);
          ctx.stroke();
        }
        if (index === 0 && timeToX(segment.end) > 6) {
          ctx.fillStyle = "rgba(0,0,0,.45)";
          ctx.fillRect(0, 0, 3, height);
        }
      });

      this.segments.forEach((segment, index) => {
        const x = timeToX(segment.start) + 5;
        if (x + 28 > width) return;
        ctx.fillStyle = "rgba(255,180,84,.9)";
        ctx.font = "600 9.5px 'JetBrains Mono', Consolas, monospace";
        ctx.fillText(String(index + 1).padStart(2, "0"), x, 13);
      });
    }
    this.paint();
  }

  paint() {
    if (!this.width) return;
    const ctx = this.ctx;
    // 静态层离屏缓存后整块贴回，比逐柱重绘快得多
    ctx.clearRect(0, 0, this.width, this.height);
    ctx.drawImage(this.layer, 0, 0, this.width, this.height);
    if (!this.duration) return;
    const x = (Math.min(this.playhead, this.duration) / this.duration) * this.width;
    ctx.save();
    ctx.shadowColor = "rgba(255,180,84,.85)";
    ctx.shadowBlur = 8;
    ctx.strokeStyle = "rgba(255,214,150,.95)";
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, this.height);
    ctx.stroke();
    ctx.restore();
    ctx.fillStyle = "rgba(255,214,150,.95)";
    ctx.beginPath();
    ctx.arc(x, 6, 3.2, 0, Math.PI * 2);
    ctx.fill();
    if (this.dragTime !== null && this.duration) {
      const gx = (this.dragTime / this.duration) * this.width;
      ctx.save();
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = "rgba(167,139,250,.95)";
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.moveTo(gx, 0);
      ctx.lineTo(gx, this.height);
      ctx.stroke();
      ctx.restore();
      ctx.fillStyle = "rgba(167,139,250,.95)";
      ctx.font = "10px 'JetBrains Mono', Consolas, monospace";
      ctx.fillText(`${this.dragTime.toFixed(2)}s`, Math.min(this.width - 42, gx + 5), 24);
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
}
