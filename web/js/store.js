// 全局状态与轻量订阅
function readStorage(key) {
  try { return window.localStorage.getItem(key) || ""; } catch { return ""; }
}

function writeStorage(key, value) {
  try { window.localStorage.setItem(key, value); } catch { /* 隐私模式忽略 */ }
}

export const state = {
  health: null,
  options: null,
  settings: null,
  setup: null,
  projects: [],
  project: null,
  run: null,
  currentSegment: -1,
  promptIndex: null,
  pollTimer: null,
  refreshTimer: null,
  audioPlaying: false,
  auditionSegment: null,
  presets: [],
  pollInFlight: false,
  generatingSegment: -1,
  lastProject: readStorage("h3wb:lastProject"),
};

const listeners = new Set();

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function emit(type, detail) {
  listeners.forEach((fn) => fn(type, detail));
}

export function setProject(project) {
  state.project = project;
  if (project?.id) writeStorage("h3wb:lastProject", project.id);
  emit("project", project);
}
