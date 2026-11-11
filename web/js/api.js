// 后端 API 封装
const JSON_HEADERS = { "Content-Type": "application/json" };

async function parse(response) {
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!response.ok) {
    let message = data?.detail ?? data?.error ?? `请求失败（${response.status}）`;
    if (Array.isArray(message)) message = message.map((item) => item.msg || JSON.stringify(item)).join("；");
    if (typeof message === "object") message = JSON.stringify(message, null, 2);
    throw new Error(message);
  }
  return data;
}

function get(path, query) {
  const url = new URL(path, location.origin);
  if (query) Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
  });
  return fetch(url).then(parse);
}

function post(path, body) {
  return fetch(path, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body ?? {}) }).then(parse);
}

function patch(path, body) {
  return fetch(path, { method: "PATCH", headers: JSON_HEADERS, body: JSON.stringify(body ?? {}) }).then(parse);
}

function put(path, body) {
  return fetch(path, { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify(body ?? {}) }).then(parse);
}

function del(path) {
  return fetch(path, { method: "DELETE" }).then(parse);
}

function form(path, fields, fileField, file) {
  const data = new FormData();
  Object.entries(fields || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null) data.append(key, value);
  });
  if (file) data.append(fileField, file);
  return fetch(path, { method: "POST", body: data }).then(parse);
}

export const api = {
  health: () => get("/api/health"),
  options: (force) => get("/api/options", { force: force ? "true" : "" }),
  settings: () => get("/api/settings"),
  saveSettings: (patchValue) => post("/api/settings", patchValue),
  testLLM: (llm) => post("/api/llm/test", { llm }),
  briefPresets: () => get("/api/brief-presets"),
  projects: () => get("/api/projects"),
  createProject: (fields, file) => form("/api/projects", fields, "audio", file),
  uploadAudio: (id, file) => form(`/api/projects/${id}/audio`, {}, "audio", file),
  importProject: (file) => form("/api/projects/import", {}, "package", file),
  exportProject: (id) => `/api/projects/${id}/export`,
  project: (id) => get(`/api/projects/${id}`),
  patchProject: (id, body) => patch(`/api/projects/${id}`, body),
  deleteProject: (id) => del(`/api/projects/${id}`),
  analyze: (id, body) => post(`/api/projects/${id}/analyze`, body),
  addRef: (id, file, role, title, description) =>
    form(`/api/projects/${id}/refs`, { role, title, description }, "image", file),
  patchRef: (id, index, body) => patch(`/api/projects/${id}/refs/${index}`, body),
  deleteRef: (id, index) => del(`/api/projects/${id}/refs/${index}`),
  patchSegment: (id, index, body) => patch(`/api/projects/${id}/segments/${index}`, body),
  segmentFrame: (id, index, edge) => get(`/api/projects/${id}/segments/${index}/frame`, { edge }),
  setBoundaries: (id, cuts) => put(`/api/projects/${id}/boundaries`, { cuts }),
  splitSegment: (id, time) => post(`/api/projects/${id}/split`, { time }),
  mergeSegment: (id, index) => post(`/api/projects/${id}/segments/${index}/merge`, {}),
  generatePrompt: (id, index, llm) => post(`/api/projects/${id}/segments/${index}/prompt`, { llm }),
  generateAllPrompts: (id) => post(`/api/projects/${id}/prompts`, {}),
  selectTake: (id, index, takeId) =>
    post(`/api/projects/${id}/segments/${index}/takes/${encodeURIComponent(takeId)}/select`, {}),
  deleteTake: (id, index, takeId) =>
    del(`/api/projects/${id}/segments/${index}/takes/${encodeURIComponent(takeId)}`),
  assemble: (id) => post(`/api/projects/${id}/assemble`, {}),
  recheck: (id) => post(`/api/projects/${id}/recheck`, {}),
  generate: (id, body) => post(`/api/projects/${id}/generate`, body),
  regenerate: (id, index, body) => post(`/api/projects/${id}/segments/${index}/regenerate`, body),
  cancel: (id) => post(`/api/projects/${id}/cancel`, {}),
  run: (id) => get(`/api/projects/${id}/run`),
  media: (id, path) => `/api/projects/${id}/media/${path}`,
  download: (id) => `/api/projects/${id}/download`,
};
