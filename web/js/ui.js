// 通用 UI 工具
export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]
  ));
}

export function formatTime(seconds) {
  // 先量化到 0.1 秒再拆分钟：否则 59.96s 会显示成 00:60.0
  const total = Math.round(Math.max(0, Number(seconds) || 0) * 10) / 10;
  const minutes = Math.floor(total / 60);
  const rest = total - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${rest.toFixed(1).padStart(4, "0")}`;
}

export function formatDuration(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  const minutes = Math.floor(value / 60);
  const rest = Math.round(value - minutes * 60);
  return minutes ? `${minutes}分${rest}秒` : `${rest}秒`;
}

export function formatDate(stamp) {
  if (!stamp) return "—";
  const date = new Date(stamp * 1000);
  return `${date.getMonth() + 1}月${date.getDate()}日 ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

export function toast(message, type = "info", timeout = 3600) {
  const stack = $("#toasts");
  if (!stack) return;
  const item = document.createElement("div");
  item.className = `toast ${type}`;
  item.textContent = message;
  stack.append(item);
  setTimeout(() => {
    item.style.opacity = "0";
    item.style.transform = "translateX(14px)";
    item.style.transition = ".25s";
    setTimeout(() => item.remove(), 260);
  }, timeout);
}

let busyDepth = 0;

export function busy(text, active = true) {
  const overlay = $("#busy-overlay");
  if (!overlay) return;
  busyDepth = Math.max(0, busyDepth + (active ? 1 : -1));
  if (active && text) $("#busy-text").textContent = text;
  overlay.hidden = busyDepth === 0;
}

export function openModal(id) {
  const modal = document.getElementById(id);
  if (modal) modal.hidden = false;
}

export function closeModal(id) {
  const modal = document.getElementById(id);
  if (modal) modal.hidden = true;
}

export function anyModalOpen() {
  return $$(".modal").some((modal) => !modal.hidden);
}

let confirmResolve = null;

export function confirmDialog(title, text, confirmLabel = "确认") {
  // 连点两次时，先把上一个未决的 confirm 置为 false，否则它的 await 永远挂起
  if (confirmResolve) {
    const previous = confirmResolve;
    confirmResolve = null;
    previous(false);
  }
  $("#confirm-title").textContent = title;
  $("#confirm-text").textContent = text;
  $("#btn-confirm-yes").textContent = confirmLabel;
  openModal("modal-confirm");
  return new Promise((resolve) => { confirmResolve = resolve; });
}

export function settleConfirm(value) {
  closeModal("modal-confirm");
  if (confirmResolve) {
    const resolve = confirmResolve;
    confirmResolve = null;
    resolve(value);
  }
}

export function debounce(fn, delay = 500) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, Number(value) || 0));
}
