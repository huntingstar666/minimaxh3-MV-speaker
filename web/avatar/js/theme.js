// 数字人工作台双主题：黑金（默认） / 8-bit 复古
const KEY = "h3wb.avatarTheme";
const THEMES = ["gold", "8bit"];
const LABELS = { gold: "黑金", "8bit": "8-bit" };

function currentTheme() {
  try {
    const saved = localStorage.getItem(KEY);
    if (THEMES.includes(saved)) return saved;
  } catch { /* 隐私模式忽略 */ }
  return "gold";
}

function wipe(theme) {
  let layer = document.querySelector(".av-theme-wipe");
  if (!layer) {
    layer = document.createElement("div");
    layer.className = "av-theme-wipe";
    document.body.append(layer);
  }
  layer.dataset.theme = theme;
  layer.classList.remove("is-running");
  void layer.offsetWidth;
  layer.classList.add("is-running");
  layer.addEventListener("animationend", () => layer.classList.remove("is-running"), { once: true });
}

function apply(theme, animate = true) {
  const next = THEMES.includes(theme) ? theme : "gold";
  const body = document.body;
  body.classList.toggle("av-gold", next === "gold");
  body.classList.toggle("av-light", next === "8bit");
  body.classList.toggle("av-8bit", next === "8bit");
  document.documentElement.dataset.avTheme = next;
  const label = document.querySelector("#theme-label");
  if (label) label.textContent = LABELS[next];
  const button = document.querySelector("#btn-theme");
  if (button) {
    button.title = next === "8bit" ? "切换黑金风格" : "切换 8-bit 复古风格";
    button.setAttribute("aria-pressed", next === "8bit" ? "true" : "false");
  }
  try { localStorage.setItem(KEY, next); } catch { /* 隐私模式忽略 */ }
  if (animate) wipe(next);
}

function toggle() {
  apply(currentTheme() === "8bit" ? "gold" : "8bit");
}

function init() {
  apply(currentTheme(), false);
  document.querySelector("#btn-theme")?.addEventListener("click", toggle);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init, { once: true });
} else {
  init();
}
