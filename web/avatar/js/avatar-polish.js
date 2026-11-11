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

/* ---------------- 主区「作品内容」卡：记忆展开状态 ---------------- */

function initWorkCardFold() {
  const details = document.querySelector(".av-work-details");
  if (!details) return;
  const key = "h3av.workcard.v1";
  try {
    if (window.localStorage.getItem(key) === "0") details.open = false;
  } catch {
    /* 隐私模式下忽略 */
  }
  details.addEventListener("toggle", () => {
    try {
      window.localStorage.setItem(key, details.open ? "1" : "0");
    } catch {
      /* 忽略 */
    }
  });
}

/* ---------------- 作品设定 / 参数卡片弹窗 ---------------- */

function bindDialog(modal, openSelector) {
  if (!modal) return;
  const openers = Array.from(document.querySelectorAll(openSelector));
  let lastFocus = null;
  const focusFirst = () => {
    const target = modal.querySelector(
      '[data-autofocus], button:not([disabled]):not([hidden]), input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled]), [href]'
    );
    if (target) target.focus({ preventScroll: true });
  };
  const close = () => {
    modal.hidden = true;
    if (lastFocus && lastFocus.isConnected) lastFocus.focus({ preventScroll: true });
  };
  openers.forEach((btn) =>
    btn.addEventListener("click", () => {
      lastFocus = document.activeElement;
      modal.hidden = false;
      window.setTimeout(focusFirst, 30);
    })
  );
  modal.querySelectorAll("[data-close]").forEach((btn) => btn.addEventListener("click", close));
  modal.addEventListener("click", (event) => {
    if (event.target === modal) close();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.hidden) close();
  });
  // 弹窗内 Tab 循环，避免焦点跑到遮罩后的页面
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Tab" || modal.hidden) return;
    const items = Array.from(modal.querySelectorAll(
      'button:not([disabled]):not([hidden]), input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled]), [href]'
    )).filter((el) => el.offsetParent !== null);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
}

function initDialogs() {
  bindDialog(document.getElementById("modal-settings"), "#btn-settings, #btn-open-settings");
  bindDialog(document.getElementById("modal-sys-settings"), "#btn-settings-top");
  bindDialog(document.getElementById("modal-history"), "#btn-history");
  bindDialog(document.getElementById("modal-params"), '[data-sheet="params"]');
  bindDialog(document.getElementById("modal-advanced"), '[data-sheet="advanced"]');
  bindDialog(document.getElementById("modal-prompts"), '[data-sheet="prompts"]');
  bindDialog(document.getElementById("modal-anchor"), '[data-sheet="anchor"]');
  bindDialog(document.getElementById("modal-backup"), "#btn-backup");
  bindDialog(document.getElementById("modal-trash"), "#btn-trash");
  // 裁切弹窗由 avatar.js 打开（需要先加载图片），这里只负责关闭与 Esc
  bindDialog(document.getElementById("modal-anchor-crop"), "[data-anchor-crop]");
}

/* ---------------- 右栏设置行：显示当前值，不用点开才知道 ---------------- */

function initSettingSummary() {
  const readSelect = (select) =>
    select && select.options[select.selectedIndex] ? select.options[select.selectedIndex].textContent.trim() : "";

  const aspect = document.getElementById("f-aspect");
  const mp = document.getElementById("f-mp");
  const seed = document.getElementById("f-seed");
  const stitch = document.getElementById("f-stitch");
  const paramsOut = document.getElementById("sum-params");

  const syncParams = () => {
    if (!paramsOut) return;
    const parts = [];
    if (aspect && aspect.value.trim()) parts.push(aspect.value.trim().split("(")[0].trim());
    const mpText = readSelect(mp);
    if (mpText) parts.push(mpText.split("·")[0].trim());
    const seedValue = seed ? seed.value.trim() : "";
    // 首尾帧锚定：0 = 项目固定种子（后端按项目 ID 派生，永远同一条）；硬切：0 = 每段随机
    const fixedSeed = ["anchor", "seam"].includes(stitch?.value || "");
    if (seedValue && Number(seedValue) !== 0) parts.push(`种子 ${seedValue}`);
    else parts.push(fixedSeed ? "种子固定" : "种子随机");
    paramsOut.textContent = parts.join(" · ");
    paramsOut.title = fixedSeed
      ? "锚定/接缝模式用固定种子：0 = 项目固定种子（同项目每次生成都一样）"
      : "硬切模式用随机种子：0 = 每段随机";
  };

  if (paramsOut) {
    [aspect, mp, seed].forEach((el) => {
      if (!el) return;
      el.addEventListener("change", syncParams);
      el.addEventListener("input", syncParams);
    });
    document.addEventListener("avatar:stitch", syncParams);
    stitch?.addEventListener("change", syncParams);
    if (aspect) new MutationObserver(syncParams).observe(aspect, { childList: true });
    syncParams();
    document.querySelector('[data-sheet="params"]')?.addEventListener("click", syncParams);
  }

  const steps = document.getElementById("a-steps");
  const cfg = document.getElementById("a-cfg");
  const advancedOut = document.getElementById("sum-advanced");
  const syncAdvanced = () => {
    if (!advancedOut) return;
    const parts = [];
    if (steps && steps.value.trim()) parts.push(`采样 ${steps.value.trim()} 步`);
    if (cfg && cfg.value.trim()) parts.push(`CFG ${cfg.value.trim()}`);
    advancedOut.textContent = parts.length ? parts.join(" · ") : "采样 / 模型 / 二次采样";
  };
  if (advancedOut) {
    [steps, cfg].forEach((el) => {
      if (!el) return;
      el.addEventListener("input", syncAdvanced);
      el.addEventListener("change", syncAdvanced);
    });
    syncAdvanced();
    document.querySelector('[data-sheet="advanced"]')?.addEventListener("click", syncAdvanced);
  }

  const promptSummary = document.getElementById("prompt-summary");
  const promptsOut = document.getElementById("sum-prompts");
  if (promptSummary && promptsOut) {
    const syncPrompts = () => {
      promptsOut.textContent = promptSummary.textContent.trim() || "未配置";
    };
    new MutationObserver(syncPrompts).observe(promptSummary, {
      childList: true,
      characterData: true,
      subtree: true,
    });
    syncPrompts();
  }
}

/* ---------------- 音轨吸顶：标题栏高度写进 --av-head-h ---------------- */

function initStickyTimeline() {
  const head = document.querySelector(".av-stage-head");
  const stage = document.querySelector(".av-stage");
  if (!head || !stage) return;
  const sync = () => {
    stage.style.setProperty("--av-head-h", `${Math.round(head.getBoundingClientRect().height)}px`);
  };
  sync();
  if (typeof ResizeObserver === "function") new ResizeObserver(sync).observe(head);
  window.addEventListener("resize", sync, { passive: true });
}

function boot() {
  initScrollAffordance();
  initPanelFold();
  initWorkCardFold();
  initDialogs();
  initSettingSummary();
  initStickyTimeline();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot, { once: true });
} else {
  boot();
}

