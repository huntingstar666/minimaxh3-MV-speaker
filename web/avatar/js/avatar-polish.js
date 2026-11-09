/* ============================================================
   H3 DIGITAL HUMAN · 数字人工作室 · 界面微交互
   ------------------------------------------------------------
   只读取 DOM 并切换“展示用”class，不触碰 avatar.js 的业务状态、
   不新增网络请求。所有行为幂等。
   负责：
   1) 侧栏 / 舞台的可滚动阴影（.can-scroll-up / .can-scroll-down）
   2) 舞台标题区贴顶后的分隔投影（.av-stage.is-scrolled）
   3) 侧栏面板折叠（.panel-fold / .is-collapsed，localStorage 记忆）
   ============================================================ */

const SCROLL_EPSILON = 6;
const STAGE_EPSILON = 2;
const FOLD_KEY_PREFIX = "h3av.fold.v2.";
const FOLD_DURATION = 190;
const FOLD_EASING = "cubic-bezier(.22,1,.36,1)";

function bindScrollAffordance(element, options = {}) {
  if (!element) return () => {};
  const { upClass = "", downClass = "", onScroll = null } = options;
  let frame = 0;

  const apply = () => {
    frame = 0;
    const scrollTop = element.scrollTop;
    const maxScroll = element.scrollHeight - element.clientHeight;
    if (upClass) element.classList.toggle(upClass, scrollTop > SCROLL_EPSILON);
    if (downClass) element.classList.toggle(downClass, maxScroll - scrollTop > SCROLL_EPSILON);
    if (onScroll) onScroll(scrollTop);
  };

  const schedule = () => {
    if (frame) return;
    frame = window.requestAnimationFrame(apply);
  };

  element.addEventListener("scroll", schedule, { passive: true });
  window.addEventListener("resize", schedule, { passive: true });

  // 内容由 avatar.js 动态渲染：结构变化后重新计算一次
  if (typeof MutationObserver === "function") {
    const observer = new MutationObserver(schedule);
    observer.observe(element, { childList: true, subtree: true });
  }

  schedule();
  return schedule;
}

function initScrollAffordance() {
  const rails = Array.from(document.querySelectorAll(".av-rail"));
  rails.forEach((rail) => {
    bindScrollAffordance(rail, { upClass: "can-scroll-up", downClass: "can-scroll-down" });
  });
  // 右栏改成「参数滚动区 + 结果固定区」后，滚动容器是 .av-rail-body
  document.querySelectorAll(".av-rail-body").forEach((body) => {
    bindScrollAffordance(body, { upClass: "can-scroll-up", downClass: "can-scroll-down" });
  });

  const stage = document.querySelector(".av-stage");
  bindScrollAffordance(stage, {
    upClass: "can-scroll-up",
    downClass: "can-scroll-down",
    onScroll: (scrollTop) => {
      stage.classList.toggle("is-scrolled", scrollTop > STAGE_EPSILON);
    },
  });
}

/* ---------------- 侧栏面板折叠 ---------------- */

function prefersReducedMotion() {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

function readFold(key) {
  try {
    return window.localStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}

function writeFold(key, collapsed) {
  try {
    window.localStorage.setItem(key, collapsed ? "1" : "0");
  } catch {
    /* 隐私模式下忽略 */
  }
}

function foldKeyFor(panel, railIndex, panelIndex) {
  return FOLD_KEY_PREFIX + (panel.id || `r${railIndex}c${panelIndex}`);
}

function applyCollapsed(panel, button, collapsed) {
  panel.classList.toggle("is-collapsed", collapsed);
  button.setAttribute("aria-expanded", collapsed ? "false" : "true");
}

function animateFold(panel, from, to) {
  if (prefersReducedMotion() || typeof panel.animate !== "function") return;
  if (Math.abs(from - to) < 1) return;
  panel.style.overflow = "hidden";
  let animation;
  try {
    animation = panel.animate(
      [{ height: `${from}px` }, { height: `${to}px` }],
      { duration: FOLD_DURATION, easing: FOLD_EASING }
    );
  } catch {
    panel.style.overflow = "";
    return;
  }
  const cleanup = () => {
    panel.style.height = "";
    panel.style.overflow = "";
  };
  animation.addEventListener("finish", cleanup, { once: true });
  animation.addEventListener("cancel", cleanup, { once: true });
}

function toggleFold(panel, button, key) {
  const willCollapse = !panel.classList.contains("is-collapsed");
  const fromHeight = panel.getBoundingClientRect().height;
  applyCollapsed(panel, button, willCollapse);
  writeFold(key, willCollapse);
  const toHeight = panel.getBoundingClientRect().height;
  animateFold(panel, fromHeight, toHeight);
}

function createFoldButton() {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "panel-fold";
  button.setAttribute("aria-label", "折叠 / 展开面板");
  button.setAttribute("title", "折叠 / 展开");
  button.setAttribute("aria-expanded", "true");
  button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-chevron"/></svg>';
  return button;
}

function initPanelFold() {
  const rails = Array.from(document.querySelectorAll(".av-rail"));
  rails.forEach((rail, railIndex) => {
    const panels = Array.from(rail.querySelectorAll(".panel")).filter(
      (panel) => panel.closest(".av-rail") === rail
    );
    panels.forEach((panel, panelIndex) => {
      const head = panel.querySelector(":scope > .panel-head");
      if (!head) return;
      // 幂等：已注入过就只同步状态
      let button = head.querySelector(":scope > .panel-fold");
      const key = foldKeyFor(panel, railIndex, panelIndex);
      if (button) {
        applyCollapsed(panel, button, readFold(key));
        return;
      }
      button = createFoldButton();
      const collapsed = readFold(key);
      applyCollapsed(panel, button, collapsed);
      button.addEventListener("click", () => toggleFold(panel, button, key));
      head.appendChild(button);
    });
  });
}

function boot() {
  initScrollAffordance();
  initPanelFold();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot, { once: true });
} else {
  boot();
}

