// H3 Studio · 主工作台交互增强（零依赖 / 幂等 / DOMContentLoaded 后执行）
// 面板折叠 / 侧栏滚动阴影 / 时间线贴顶 / data-tip 气泡 / ? 快捷键帮助 / 按钮波纹
// 约定：JS 新建元素的 class 只用设计规范 §6 的类名；视觉优先由 CSS 提供，
//       仅在 CSS 缺失时给气泡/波纹补最小内联兜底，避免样式全部写死在 JS 里。
const FOLD_KEY = "h3.fold.";
const FOLD_DURATION = 180;
const EASE_OUT = "cubic-bezier(.22,1,.36,1)";
const TIP_DELAY = 400;
const HELP_SHORTCUTS = [
  ["空格", "预览 / 暂停"],
  ["Ctrl + Z", "撤回上一次切分"],
  ["双击波形", "在指针处切分"],
  ["右键切点", "删除分割线"],
  ["?", "打开快捷键帮助"],
  ["Esc", "关闭帮助 / 遮罩"],
];

const scrollTargets = [];
let tipEl = null;
let tipTimer = 0;
let tipTarget = null;
let tipFallbackChecked = false;
let tipUsesFallbackStyles = false;
let helpEl = null;
let helpCloseButton = null;
let helpOpen = false;
let helpPreviousFocus = null;

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function reducedMotion() {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

function safe(label, task) {
  try {
    task();
  } catch (error) {
    try { console.warn(`[h3-ui-polish] ${label}:`, error); } catch { /* 忽略 */ }
  }
}

function readStore(key) {
  try { return window.localStorage.getItem(key); } catch { return null; }
}

function writeStore(key, value) {
  try { window.localStorage.setItem(key, value); } catch { /* 隐私模式 / 配额忽略 */ }
}

function directChildren(element, className) {
  return Array.from(element?.children || []).filter((node) => node.classList?.contains(className));
}

// 探测 CSS 是否真的给某个类定义了某条规则：用来判断要不要加内联兜底
function cssProvides(className, property, defaultValue) {
  try {
    const probe = document.createElement("div");
    probe.className = className;
    probe.style.cssText = "position:absolute;left:-9999px;top:0;width:10px;height:10px;visibility:hidden;pointer-events:none";
    document.body.appendChild(probe);
    const value = window.getComputedStyle(probe).getPropertyValue(property);
    probe.remove();
    return !!value && value.trim() !== "" && value.trim() !== defaultValue;
  } catch {
    return false;
  }
}

// ------------------------------- 1. 侧栏面板折叠 -------------------------------
function foldStorageKey(panel, railIndex, panelIndex) {
  const name = panel.id || `idx${railIndex}-${panelIndex}`;
  return `${FOLD_KEY}${name}`;
}

function applyCollapsed(panel, button, collapsed) {
  panel.classList.toggle("is-collapsed", collapsed);
  if (button) button.setAttribute("aria-expanded", String(!collapsed));
}

function setCollapsed(panel, button, collapsed, animated) {
  const canAnimate = animated && !reducedMotion() && typeof panel.animate === "function";
  if (canAnimate) {
    let from = 0;
    try { from = panel.offsetHeight || 0; } catch { from = 0; }
    applyCollapsed(panel, button, collapsed);
    let to = 0;
    try { to = panel.offsetHeight || 0; } catch { to = 0; }
    if (Math.abs(from - to) >= 1) {
      try { panel._h3Fold?.cancel?.(); } catch { /* 忽略 */ }
      panel.style.overflow = "hidden";
      let animation = null;
      try {
        animation = panel.animate(
          [{ height: `${from}px` }, { height: `${to}px` }],
          { duration: FOLD_DURATION, easing: EASE_OUT },
        );
      } catch {
        animation = null;
      }
      if (animation) {
        panel._h3Fold = animation;
        const finish = () => {
          if (panel._h3Fold !== animation) return; // 已被下一次折叠接管
          panel._h3Fold = null;
          panel.style.overflow = "";
        };
        try { animation.finished.then(finish).catch(finish); } catch { finish(); }
        return;
      }
      panel.style.overflow = "";
    }
    return;
  }
  applyCollapsed(panel, button, collapsed);
}

function initPanels() {
  // 只注入主站左右侧栏（modal / 其他页面的 .panel 一律不碰）
  const rails = Array.from(document.querySelectorAll(".rail-left, .rail-right"));
  rails.forEach((rail, railIndex) => {
    const panels = directChildren(rail, "panel");
    panels.forEach((panel, panelIndex) => {
      const head = directChildren(panel, "panel-head")[0] || panel.querySelector(".panel-head");
      if (!head) return;
      let button = directChildren(head, "panel-fold")[0];
      if (!button) {
        button = document.createElement("button");
        button.type = "button";
        button.className = "panel-fold";
        button.setAttribute("aria-expanded", "true");
        button.title = "折叠 / 展开";
        button.setAttribute("aria-label", "折叠 / 展开");
        button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><use href="#i-chevron"/></svg>';
        head.appendChild(button);
      }
      const key = foldStorageKey(panel, railIndex, panelIndex);
      // 恢复上次状态（不播动画）
      applyCollapsed(panel, button, readStore(key) === "1");
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const collapsed = !panel.classList.contains("is-collapsed");
        setCollapsed(panel, button, collapsed, true);
        writeStore(key, collapsed ? "1" : "0");
        updateScrollStates();
      });
    });
  });
}

// ------------------------------- 2. 侧栏滚动阴影 -------------------------------
function updateScrollState(el) {
  let max = 0;
  let top = 0;
  try {
    max = el.scrollHeight - el.clientHeight;
    top = el.scrollTop || 0;
  } catch { return; }
  el.classList.toggle("can-scroll-up", max > 2 && top > 2);
  el.classList.toggle("can-scroll-down", max > 2 && top < max - 2);
}

function updateScrollStates() {
  for (const el of scrollTargets) updateScrollState(el);
}

function observeScrollTarget(el) {
  if (el.__h3ScrollBound) return;
  el.__h3ScrollBound = true;
  scrollTargets.push(el);
  el.addEventListener("scroll", () => updateScrollState(el), { passive: true });
  if (typeof ResizeObserver === "function") {
    try {
      const observer = new ResizeObserver(() => updateScrollState(el));
      observer.observe(el);
      el.__h3ScrollResize = observer;
    } catch { /* 忽略 */ }
  }
  // 面板折叠 / 分镜列表重建都会改变可滚高度，监听子树变化兜住
  if (typeof MutationObserver === "function") {
    try {
      const observer = new MutationObserver(() => updateScrollState(el));
      observer.observe(el, { childList: true, subtree: true });
      el.__h3ScrollMutation = observer;
    } catch { /* 忽略 */ }
  }
}

function initScrollShadows() {
  const targets = Array.from(document.querySelectorAll(".rail, .stage"));
  targets.forEach(observeScrollTarget);
  window.addEventListener("resize", updateScrollStates, { passive: true });
  const kick = () => updateScrollStates();
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(kick);
  setTimeout(kick, 250);
  try { document.fonts?.ready?.then(kick).catch(() => {}); } catch { /* 忽略 */ }
}

// ------------------------------- 3. 时间线贴顶 -------------------------------
function initTimelineStuck() {
  const card = document.querySelector(".timeline-card");
  if (!card || card.__h3StuckBound) return;
  card.__h3StuckBound = true;
  let scroller = null;
  try { scroller = card.closest(".stage") || card.parentElement; } catch { scroller = card.parentElement; }
  const update = () => {
    let top = 0;
    try { top = card.getBoundingClientRect().top; } catch { return; }
    // sticky top:0 相对滚动容器：容器顶部 +1px 亚像素容差；没有滚动容器时退回视口 8px
    let threshold = 8;
    if (scroller && typeof scroller.getBoundingClientRect === "function") {
      try { threshold = Math.max(8, scroller.getBoundingClientRect().top + 1); } catch { threshold = 8; }
    }
    card.classList.toggle("is-stuck", top <= threshold);
  };
  if (scroller && typeof scroller.addEventListener === "function") {
    scroller.addEventListener("scroll", update, { passive: true });
  }
  window.addEventListener("scroll", update, { passive: true });
  window.addEventListener("resize", update, { passive: true });
  if (scroller && typeof ResizeObserver === "function") {
    try { new ResizeObserver(update).observe(scroller); } catch { /* 忽略 */ }
  }
  const kick = () => update();
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(kick);
  setTimeout(kick, 250);
}

// ------------------------------- 4. data-tip 提示气泡 -------------------------------
function ensureTipElement() {
  if (tipEl && tipEl.isConnected) return tipEl;
  const el = document.createElement("div");
  el.className = "h3-tip";
  el.setAttribute("aria-hidden", "true");
  el.setAttribute("role", "tooltip");
  // 只写定位：颜色 / 圆角 / 阴影 / 过渡由 CSS 的 .h3-tip 提供
  el.style.left = "0px";
  el.style.top = "0px";
  document.body.appendChild(el);
  tipEl = el;
  return el;
}

function applyTipFallback(el) {
  if (tipFallbackChecked) return;
  tipFallbackChecked = true;
  // CSS 里已有 .h3-tip 的圆角就说明样式在位，不再用内联覆盖
  if (cssProvides("h3-tip", "border-radius", "0px")) return;
  tipUsesFallbackStyles = true;
  el.style.position = "fixed";
  el.style.zIndex = "92";
  el.style.pointerEvents = "none";
  el.style.opacity = "0";
  el.style.background = "rgba(9,13,18,.96)";
  el.style.color = "#eef3fa";
  el.style.border = "1px solid rgba(255,255,255,.14)";
  el.style.borderRadius = "8px";
  el.style.padding = "6px 9px";
  el.style.fontSize = "11.5px";
  el.style.lineHeight = "1.5";
  el.style.maxWidth = "280px";
  el.style.textAlign = "center";
  el.style.boxShadow = "0 10px 28px rgba(0,0,0,.5)";
  el.style.transition = "opacity .16s ease";
}

function hideTip() {
  if (tipTimer) {
    clearTimeout(tipTimer);
    tipTimer = 0;
  }
  tipTarget = null;
  if (!tipEl) return;
  tipEl.classList.remove("is-visible");
  tipEl.setAttribute("aria-hidden", "true");
  if (tipUsesFallbackStyles) tipEl.style.opacity = "0";
}

function showTip(target) {
  const text = target?.getAttribute?.("data-tip");
  if (!text) return;
  const el = ensureTipElement();
  applyTipFallback(el);
  let rect = null;
  try { rect = target.getBoundingClientRect(); } catch { return; }
  if (!rect || (!rect.width && !rect.height)) return;
  el.textContent = text;
  el.classList.add("is-visible");
  el.setAttribute("aria-hidden", "false");
  if (tipUsesFallbackStyles) el.style.opacity = "1";
  // 先亮出来再量尺寸：避免 CSS 用 display/visibility 控制显隐时量不到
  let tipRect = { width: 0, height: 0 };
  try { tipRect = el.getBoundingClientRect(); } catch { tipRect = { width: 0, height: 0 }; }
  const margin = 8;
  let top = rect.top - tipRect.height - margin;
  let placement = "top";
  if (top < margin) {
    top = rect.bottom + margin;
    placement = "bottom";
  }
  let left = rect.left + rect.width / 2 - tipRect.width / 2;
  const viewportWidth = window.innerWidth || 1280;
  left = clamp(left, margin, Math.max(margin, viewportWidth - tipRect.width - margin));
  el.style.top = `${Math.round(top)}px`;
  el.style.left = `${Math.round(left)}px`;
  el.dataset.placement = placement;
}

function scheduleTip(target) {
  if (!target || tipTarget === target) return;
  if (tipTimer) {
    clearTimeout(tipTimer);
    tipTimer = 0;
  }
  tipTarget = target;
  tipTimer = setTimeout(() => {
    tipTimer = 0;
    if (!target.isConnected) return;
    let hovered = false;
    try { hovered = target.matches(":hover"); } catch { hovered = false; }
    let focused = false;
    try { focused = target.contains(document.activeElement); } catch { focused = false; }
    if (!hovered && !focused) return;
    showTip(target);
  }, TIP_DELAY);
}

function tipTargetOf(event) {
  const target = event?.target;
  if (!target || typeof target.closest !== "function") return null;
  return target.closest("[data-tip]");
}

function initTooltips() {
  document.addEventListener("pointerover", (event) => {
    if (event.pointerType && event.pointerType !== "mouse") return;
    const target = tipTargetOf(event);
    if (target) scheduleTip(target);
  });
  document.addEventListener("pointerout", (event) => {
    const target = tipTargetOf(event);
    if (!target) return;
    const related = event.relatedTarget;
    if (related && typeof target.contains === "function" && target.contains(related)) return;
    hideTip();
  });
  document.addEventListener("focusin", (event) => {
    const target = tipTargetOf(event);
    if (target) scheduleTip(target);
  });
  document.addEventListener("focusout", () => hideTip());
  document.addEventListener("pointerdown", () => hideTip(), true);
  document.addEventListener("scroll", () => hideTip(), true);
}

// ------------------------------- 5. ? 快捷键帮助 -------------------------------
function isEditableTarget(target) {
  if (!target) return false;
  if (typeof target.closest !== "function") return false;
  try {
    if (target.isContentEditable) return true;
    if (target.closest("[contenteditable='true'], [contenteditable='']")) return true;
  } catch { /* 忽略 */ }
  const tag = (target.tagName || "").toUpperCase();
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || tag === "OPTION";
}

// 空格快捷键：焦点在输入框 / 可点击控件上时让原生行为优先，避免和「空格激活按钮」打架
function spaceShortcutBlocked(target) {
  if (isEditableTarget(target)) return true;
  if (!target || typeof target.closest !== "function") return false;
  try {
    return !!target.closest("button, a[href], summary, [role='button'], [role='link'], [role='menuitem']");
  } catch {
    return false;
  }
}

function ensureHelpElement() {
  if (helpEl && helpEl.isConnected) return helpEl;
  const overlay = document.createElement("div");
  overlay.className = "h3-help";
  overlay.hidden = true;
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-label", "快捷键帮助");
  overlay.setAttribute("aria-hidden", "true");

  const card = document.createElement("div");
  card.className = "h3-help-card";
  const title = document.createElement("h2");
  title.textContent = "快捷键";

  const list = document.createElement("ul");
  list.className = "h3-help-list";
  HELP_SHORTCUTS.forEach(([keys, label]) => {
    const row = document.createElement("li");
    row.className = "h3-help-row";
    const name = document.createElement("span");
    name.textContent = label;
    const kbd = document.createElement("kbd");
    kbd.className = "h3-help-key";
    kbd.textContent = keys;
    row.append(name, kbd);
    list.append(row);
  });

  const close = document.createElement("button");
  close.type = "button";
  close.className = "btn ghost h3-help-close";
  close.textContent = "关闭（Esc）";
  close.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    closeHelp();
  });

  card.append(title, list, close);
  overlay.append(card);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) closeHelp();
  });
  document.body.appendChild(overlay);
  helpEl = overlay;
  helpCloseButton = close;
  return overlay;
}

function openHelp() {
  if (helpOpen) return;
  const overlay = ensureHelpElement();
  hideTip();
  helpPreviousFocus = document.activeElement || null;
  overlay.hidden = false;
  overlay.setAttribute("aria-hidden", "false");
  helpOpen = true;
  if (helpCloseButton) {
    try { helpCloseButton.focus({ preventScroll: true }); } catch { try { helpCloseButton.focus(); } catch { /* 忽略 */ } }
  }
}

function closeHelp() {
  if (!helpEl) return;
  helpEl.hidden = true;
  helpEl.setAttribute("aria-hidden", "true");
  helpOpen = false;
  const previous = helpPreviousFocus;
  helpPreviousFocus = null;
  if (previous && previous.isConnected && typeof previous.focus === "function") {
    try { previous.focus({ preventScroll: true }); } catch { try { previous.focus(); } catch { /* 忽略 */ } }
  }
}

function onKeydown(event) {
  if (event.key === "Escape" && helpOpen) {
    // 帮助层在最上面：先关它，别让底层弹窗一起响应 Esc
    event.preventDefault();
    event.stopPropagation();
    closeHelp();
    return;
  }
  // 组合键一律让给页面（app.js 的 Ctrl+Z / Ctrl+Enter 等）
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.key === "?") {
    if (event.repeat || helpOpen) return;
    if (isEditableTarget(event.target)) return;
    event.preventDefault();
    openHelp();
    return;
  }
  // 空格：预览 / 暂停（复用 #btn-preview-audio 的既有 handler，不重复实现播放逻辑）
  if (event.code === "Space" && !event.repeat) {
    if (helpOpen) return;
    if (spaceShortcutBlocked(event.target)) return;
    const preview = document.getElementById("btn-preview-audio");
    if (!preview) return;
    event.preventDefault();
    preview.click();
  }
}

function initHelp() {
  document.addEventListener("keydown", onKeydown, true);
}

// ------------------------------- 6. 按钮波纹 -------------------------------
function initRipple() {
  document.addEventListener("pointerdown", (event) => {
    if (reducedMotion()) return;
    if (event.pointerType === "mouse" && event.button !== 0) return;
    const target = event.target;
    if (!target || typeof target.closest !== "function") return;
    const host = target.closest(".btn, .chip, .mode-card");
    if (!host) return;
    if (host.disabled || host.getAttribute("aria-disabled") === "true") return;
    let rect = null;
    try { rect = host.getBoundingClientRect(); } catch { return; }
    if (!rect || rect.width < 4 || rect.height < 4) return;

    let position = "";
    let overflow = "";
    try {
      const styles = window.getComputedStyle(host);
      position = styles.position;
      overflow = styles.overflow;
    } catch { /* 忽略 */ }
    if (!position || position === "static") host.style.position = "relative";
    if (overflow && overflow !== "hidden") host.style.overflow = "hidden";

    const size = Math.max(8, Math.hypot(rect.width, rect.height));
    const ripple = document.createElement("span");
    ripple.className = "h3-ripple";
    ripple.setAttribute("aria-hidden", "true");
    // 只写几何；颜色 / 混合模式 / 初始 transform 由 CSS 的 .h3-ripple 提供
    ripple.style.left = `${event.clientX - rect.left - size / 2}px`;
    ripple.style.top = `${event.clientY - rect.top - size / 2}px`;
    ripple.style.width = `${size}px`;
    ripple.style.height = `${size}px`;
    ripple.style.pointerEvents = "none";
    if (!cssProvides("h3-ripple", "border-radius", "0px")) {
      // CSS 缺失时的最小兜底
      ripple.style.position = "absolute";
      ripple.style.borderRadius = "50%";
      ripple.style.background = "radial-gradient(circle at center, rgba(255,255,255,.34), rgba(255,255,255,.10) 55%, rgba(255,255,255,0) 72%)";
    }
    host.appendChild(ripple);

    const cleanup = () => {
      try { if (ripple.isConnected) ripple.remove(); } catch { /* 忽略 */ }
    };
    try {
      if (typeof ripple.animate === "function") {
        const animation = ripple.animate(
          [{ transform: "scale(.12)", opacity: 0.55 }, { transform: "scale(1)", opacity: 0 }],
          { duration: 400, easing: EASE_OUT },
        );
        animation.finished.then(cleanup).catch(cleanup);
      } else {
        ripple.style.transition = "transform .4s ease, opacity .4s ease";
        ripple.style.transform = "scale(1)";
        ripple.style.opacity = "0";
      }
    } catch { /* 忽略 */ }
    setTimeout(cleanup, 700);
  }, { passive: true });
}

// ------------------------------- 启动 -------------------------------
function boot() {
  const root = document.documentElement;
  if (root.dataset.h3Polish === "1") return; // 幂等：只初始化一次
  root.dataset.h3Polish = "1";
  safe("panels", initPanels);
  safe("scroll-shadows", initScrollShadows);
  safe("timeline-stuck", initTimelineStuck);
  safe("tooltips", initTooltips);
  safe("help", initHelp);
  safe("ripple", initRipple);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot, { once: true });
} else {
  boot();
}
