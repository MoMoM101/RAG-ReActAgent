import { authHeaders } from "../stores/authStore";

const BASE_URL = "";

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

async function parseError(res: Response, method: string, path: string): Promise<never> {
  if (res.status === 401) {
    sessionStorage.removeItem("rag_admin_token");
    window.dispatchEvent(new CustomEvent("auth:required"));
  }
  let detail = `${method} ${path}: ${res.status}`;
  try {
    const body = JSON.parse(await res.text());
    if (body.detail) detail = body.detail;
  } catch { /* keep default */ }
  throw new ApiError(res.status, detail);
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: authHeaders(),
  });
  if (!res.ok) await parseError(res, "GET", path);
  return res.json();
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const isFormData = body instanceof FormData;
  const res = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: authHeaders(isFormData ? {} : { "Content-Type": "application/json" }),
    body: isFormData ? body : JSON.stringify(body),
  });
  if (!res.ok) await parseError(res, "POST", path);
  return res.json();
}

export function apiUpload<T>(
  path: string,
  form: FormData,
  onProgress?: (percent: number) => void,
  signal?: AbortSignal,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const abortRequest = () => xhr.abort();
    const cleanup = () => signal?.removeEventListener("abort", abortRequest);
    xhr.open("POST", `${BASE_URL}${path}`);
    const headers = authHeaders() as Record<string, string>;
    Object.entries(headers).forEach(([key, value]) => xhr.setRequestHeader(key, value));

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) {
        onProgress?.(Math.min(100, Math.round((event.loaded / event.total) * 100)));
      }
    };
    xhr.onload = () => {
      cleanup();
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as T);
        } catch {
          reject(new ApiError(xhr.status, "上传响应格式无效"));
        }
        return;
      }
      if (xhr.status === 401) {
        sessionStorage.removeItem("rag_admin_token");
        window.dispatchEvent(new CustomEvent("auth:required"));
      }
      let detail = `POST ${path}: ${xhr.status}`;
      try {
        const body = JSON.parse(xhr.responseText) as { detail?: string };
        if (body.detail) detail = body.detail;
      } catch { /* keep default */ }
      reject(new ApiError(xhr.status, detail));
    };
    xhr.onerror = () => {
      cleanup();
      reject(new ApiError(0, "上传网络连接失败"));
    };
    xhr.onabort = () => {
      cleanup();
      reject(new DOMException("Upload cancelled", "AbortError"));
    };

    if (signal?.aborted) {
      xhr.abort();
      return;
    }
    signal?.addEventListener("abort", abortRequest, { once: true });
    xhr.send(form);
  });
}

export async function apiDelete<T = void>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok) await parseError(res, "DELETE", path);
  return res.json();
}

export async function apiPut<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: "PUT",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (!res.ok) await parseError(res, "PUT", path);
  return res.json();
}
