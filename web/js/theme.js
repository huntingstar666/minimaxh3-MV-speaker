// MV 工作台主题：夜班（默认） / 粉色渐变
const KEY = "h3wb.mvTheme";
const THEMES = ["night", "pink"];
const LABELS = { night: "夜班", pink: "粉色" };

function currentTheme() {
  try {
    const saved = localStorage.getItem(KEY);
    if (THEMES.includes(saved)) return saved;
  } catch { /* 隐私模式忽略 */ }
  return "night";
}

function wipe(theme) {
  let layer = document.querySelector(".theme-wipe");
  if (!layer) {
    layer = document.createElement("div");
    layer.className = "theme-wipe";
    document.body.append(layer);
  }
  layer.dataset.theme = theme;
  layer.classList.remove("is-running");
  // 强制重排，保证连续切换也能重新播放
  void layer.offsetWidth;
  layer.classList.add("is-running");
  layer.addEventListener("animationend", () => layer.classList.remove("is-running"), { once: true });
}

function apply(theme, animate = true) {
  const next = THEMES.includes(theme) ? theme : "night";
  if (next === "pink") {
    document.documentElement.dataset.theme = "pink";
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
  const label = document.querySelector("#theme-label");
  if (label) label.textContent = LABELS[next];
  const button = document.querySelector("#btn-theme");
  if (button) {
    button.title = next === "pink" ? "切换回夜班风格" : "切换粉色渐变风格";
    button.setAttribute("aria-pressed", next === "pink" ? "true" : "false");
  }
  try { localStorage.setItem(KEY, next); } catch { /* 隐私模式忽略 */ }
  if (animate) wipe(next);
}

export function toggleTheme() {
  apply(currentTheme() === "pink" ? "night" : "pink");
}

export function initTheme() {
  apply(currentTheme(), false);
  const button = document.querySelector("#btn-theme");
  if (button) button.addEventListener("click", toggleTheme);
}
