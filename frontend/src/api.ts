import type { AppConfig, AuthSession, Bible, Block, Estimate, JobChanges, JobDetail, JobSummary, RunRequest } from "./types";

const BASE = "/api";

export class ApiError extends Error {
  status: number;
  missingKeys: string[];

  constructor(status: number, message: string, missingKeys: string[] = []) {
    super(message);
    this.status = status;
    this.missingKeys = missingKeys;
  }
}

/** Fired when the server says the session is missing or expired; the app shows the login page. */
export const UNAUTHORIZED_EVENT = "subtrans:unauthorized";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, { credentials: "same-origin", ...init });
  } catch {
    throw new ApiError(0, "Cannot reach the SubTrans backend.");
  }
  if (res.status === 401 && !path.startsWith("/auth/")) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = typeof body.detail === "string" ? body.detail : Array.isArray(body.detail) ? body.detail.map((d: { msg: string }) => d.msg).join("; ") : res.statusText;
    throw new ApiError(res.status, detail || `HTTP ${res.status}`, body.missing_keys ?? []);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  session: () => request<AuthSession>("/auth/session"),
  login: (password: string) => request<{ authenticated: boolean }>("/auth/login", json("POST", { password })),
  logout: () => request<{ authenticated: boolean }>("/auth/logout", { method: "POST" }),
  config: () => request<AppConfig>("/config"),
  listJobs: () => request<JobSummary[]>("/jobs"),
  getJob: (id: string) => request<JobDetail>(`/jobs/${id}`),
  changes: (id: string, rev: number, logId: number) => request<JobChanges>(`/jobs/${id}/changes?rev=${rev}&log=${logId}`),
  deleteJob: (id: string) => request<void>(`/jobs/${id}`, { method: "DELETE" }),
  upload: (file: File, targetLanguage: string, contextHints: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("target_language", targetLanguage);
    form.append("context_hints", contextHints);
    return request<JobDetail>("/jobs", { method: "POST", body: form });
  },
  estimate: (id: string, body: RunRequest) => request<Estimate>(`/jobs/${id}/estimate`, json("POST", body)),
  run: (id: string, body: RunRequest) => request<JobSummary>(`/jobs/${id}/run`, json("POST", body)),
  pause: (id: string) => request<JobSummary>(`/jobs/${id}/pause`, { method: "POST" }),
  rerun: (id: string, stage: 2 | 3, body: RunRequest) =>
    request<JobSummary>(`/jobs/${id}/rerun`, json("POST", { ...body, stage })),
  putBible: (id: string, bible: Bible, approve = false) =>
    request<JobSummary>(`/jobs/${id}/bible`, json("PUT", { bible, approve })),
  editBlock: (id: string, blockId: string, text: string) =>
    request<Block>(`/jobs/${id}/blocks/${blockId}`, json("PATCH", { text })),
  revertBlock: (id: string, blockId: string) =>
    request<Block>(`/jobs/${id}/blocks/${blockId}/revert`, { method: "POST" }),

  async downloadExport(id: string): Promise<{ filename: string }> {
    const res = await fetch(`${BASE}/jobs/${id}/export`, { credentials: "same-origin" });
    if (res.status === 401) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    if (!res.ok) throw new ApiError(res.status, "Export failed");
    const disposition = res.headers.get("Content-Disposition") ?? "";
    const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
    const plain = /filename="([^"]+)"/i.exec(disposition);
    const filename = utf8 ? decodeURIComponent(utf8[1]) : plain ? plain[1] : "subtitles.srt";
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    return { filename };
  },
};
