// 全局状态与轻量订阅
export const state = {
  health: null,
  options: null,
  settings: null,
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
  lastProject: localStorage.getItem("h3wb:lastProject") || "",
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
  if (project?.id) localStorage.setItem("h3wb:lastProject", project.id);
  emit("project", project);
}
