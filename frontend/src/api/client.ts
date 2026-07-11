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
